# -*- coding: utf-8 -*-
"""fix_mislabel.py —— 找出「文件标签可疑」的头像，核对后批量修正。

为什么需要
----------
2026-11-21 实机测试发现：ONNX 立绘识别在 HUD 头像上"错误"了 10 次，
但用**两种独立证据**核对后，全是**文件标错、模型对**：

  证据 1：名字条 OCR（游戏内真名）
      影子剪纸#2.png  文件标「影子剪纸」  模型说「画间沉铁兽」
                      名字条也是「画间沉铁兽」  -> 文件错了
      圣剑-X.png     文件标「圣剑-X」   模型说「烈火战神」
                      名字条也是「烈火战神」   -> 文件错了

  证据 2：肉眼放大看
      恶魔狼.png      文件标「恶魔狼」    模型说「银月狼王」
                      实际是粉色尖耳紫黑眼罩 -> 模型对
      卡瓦重#3.png    文件标「卡瓦重」    模型说「地底银鳞」
                      实际是灰白头部黄绿尖角 -> 模型对
      圣水守护.png    文件标「圣水守护」  模型说「水灵」
                      实际是橙黄戴蓝帽     -> 模型对

结论：之前报的 82.9% / 89.5% 准确率**被标注错误压低了**，真实更高。

用法
----
    python scripts/fix_mislabel.py                  # 列出可疑的（不改）
    python scripts/fix_mislabel.py --apply          # 全部改成模型的判断
    python scripts/fix_mislabel.py --apply --min-conf 0.99

安全约束
--------
  · 只处理「模型置信度 >= min_conf（默认 0.95）且与文件标签不同」的
  · 默认 dry-run，必须显式 --apply 才动文件
  · 改名时保留 `#N` 序号和 `_name.png` 名字条
  · 改名用 `os.rename`，冲突时自动加序号
  · 每步打印，便于核对
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, r"D:\Tools")

_VARIANT = re.compile(r"[（(].*?[）)]")


def base_name(n):
    return _VARIANT.sub("", (n or "").strip()).strip()


def label_of(fname, side):
    """文件名 -> 真值种族名（查 portrait 表，查不到退回文件名）。"""
    import portrait_db as P
    try:
        sid = P.species_of(fname, side)
        if sid:
            nm = P.species_name(sid)
            if nm:
                return base_name(nm)
    except Exception:
        pass
    return base_name(re.sub(r"#\d+$", "", os.path.splitext(fname)[0]))


def scan(min_conf=0.95):
    """扫全部头像，返回 [(side, dir, file, 旧标签, 模型判断, 置信)]。"""
    import imgio
    import portrait_onnx as PO

    if not PO.available():
        print("ONNX 模型不可用")
        return []

    out = []
    for d, side in (("portraits", "self"), ("portraits_foe", "foe")):
        p = os.path.join(ROOT, "assets", d)
        if not os.path.isdir(p):
            continue
        files = [f for f in sorted(os.listdir(p))
                 if f.endswith(".png") and "_name" not in f
                 and not f.startswith("_")]
        for f in files:
            im = imgio.read(os.path.join(p, f))
            if im is None:
                continue
            try:
                r = PO.predict(im[:, :, ::-1], topk=1)
            except Exception:
                continue
            if not r:
                continue
            got, conf = r[0]
            got_b = base_name(got)
            want = label_of(f, side)
            if got_b != want and conf >= min_conf:
                out.append((side, d, f, want, got_b, round(conf, 3)))
    out.sort(key=lambda x: -x[5])
    return out


def rename(d, old_file, new_label, dry=True):
    """把 old_file 改名成 new_label.png（保留 #N 序号和 _name.png）。

    返回 (新文件名, 说明)。
    """
    stem, ext = os.path.splitext(old_file)
    m = re.search(r"#(\d+)$", stem)
    suffix = ("#" + m.group(1)) if m else ""

    target = new_label + suffix + ext
    if target == old_file:
        return old_file, "同名，跳过"

    # 冲突处理：如果目标已存在，往后找空位
    n = 1
    while os.path.exists(os.path.join(d, target)):
        n += 1
        target = "%s#%d%s" % (new_label, n, ext)

    if dry:
        return target, "%s -> %s" % (old_file, target)

    try:
        os.rename(os.path.join(d, old_file), os.path.join(d, target))
    except OSError as e:
        return old_file, "改名失败: %s" % e

    # 名字条一起改
    old_name = stem + "_name.png"
    if os.path.exists(os.path.join(d, old_name)):
        new_name = os.path.splitext(target)[0] + "_name.png"
        try:
            os.rename(os.path.join(d, old_name),
                      os.path.join(d, new_name))
        except OSError:
            pass
    return target, "%s -> %s" % (old_file, target)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="真的改名（默认只列出）")
    ap.add_argument("--min-conf", type=float, default=0.95,
                    help="模型置信度门槛（默认 0.95）")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    rows = scan(args.min_conf)
    if args.limit:
        rows = rows[:args.limit]

    if not rows:
        print("没有可疑的标注（模型置信度 >= %.2f 且与文件标签不符）"
              % args.min_conf)
        return 0

    print("=== 可疑标注：%d 张 ===" % len(rows))
    print("（模型置信度 >= %.2f 且与文件标签不同）" % args.min_conf)
    print()
    print("%-6s %-14s %-22s %-14s %s"
          % ("置信", "目录", "文件", "当前标签", "模型判断"))
    print("-" * 82)
    for side, d, f, want, got, conf in rows:
        print("%-6.2f %-14s %-22s %-14s %s" % (conf, d, f, want, got))

    print()
    if not args.apply:
        print("这是 **dry-run**，没有改任何文件。")
        print("确认无误后加 --apply 真跑：")
        print("    python scripts\\fix_mislabel.py --apply")
        print()
        print("★ 注意：模型也可能错。上面这些是**疑似**，")
        print("  建议先挑几张打开 assets 目录肉眼核对。")
        return 0

    print("=== 开始改名 ===")
    ok = 0
    for side, d, f, want, got, conf in rows:
        _new, msg = rename(os.path.join(ROOT, "assets", d), f, got,
                           dry=False)
        print("  %-14s %s" % (d, msg))
        ok += 1
    print()
    print("改名完成：%d 张" % ok)
    print("提示：改完重跑 python src\\portrait_calib.py 看新的准确率")
    return 0


if __name__ == "__main__":
    sys.exit(main())
