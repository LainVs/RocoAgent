# -*- coding: utf-8 -*-
"""portrait_calib.py —— 立绘识别的准确率定标。

为什么需要
----------
`portrait_onnx` 是 620 类分类器，但它的类别名带**变体后缀**：

    加油蟹（单只海葵的样子）
    化蝶（平常的样子）
    卡瓦重（火山附近的样子）

而我们的头像库用的是**基础名**（`加油蟹.png` / `化蝶.png` / `卡瓦重.png`）。
直接比对会把"猜对了变体"算成错，实测把准确率从 ~95% 压到 74.7%。

所以定标时必须先**归一化变体后缀**（去掉 `（...）`），再比对。

用法
----
    python src/portrait_calib.py                     # 默认标定敌方
    python src/portrait_calib.py --side self
    python src/portrait_calib.py --conf 0.7          # 只看高置信度区间

输出
----
    Top-1 / Top-3 准确率
    按置信度分档的准确率  <- 这才是"实战里敢不敢用"的依据
    错误样例 + 按物种统计
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, r"D:\Tools")

_VARIANT = re.compile(r"[（(].*?[）)]")


def base_name(name):
    """去掉变体后缀：'加油蟹（单只海葵的样子）' -> '加油蟹'。"""
    return _VARIANT.sub("", (name or "").strip()).strip()


def label_of(fname, side="foe"):
    """文件名 -> 真值种族名。

    ★ 不能用文件名直接当标签
    ------------------------
    头像库里有历史遗留的英文名（wolf.png / wolf_leader.png），
    它们通过 portrait 表映射到种族：
        wolf.png        -> species_id 3011      -> 恶魔狼
        wolf_leader.png -> species_id 13000152  -> 首领-恶魔狼

    以前定标脚本直接 `re.sub(r"#\\d+$", "", 文件名)` 当标签，
    于是把"wolf.png 识别成 恶魔狼"算成错误 —— 实际是**完全正确**的，
    只是标签用错了命名空间。这会把准确率低估一大截。

    现在优先查 portrait 表，查不到才退回文件名。
    """
    import portrait_db as P
    base = os.path.splitext(fname)[0]
    try:
        sid = P.species_of(fname, side)
        if sid:
            nm = P.species_name(sid)
            if nm:
                return base_name(nm)
    except Exception:
        pass
    return base_name(re.sub(r"#\d+$", "", base))


def calibrate(directory, side="foe", show=15, compare=False):
    import imgio
    import portrait_onnx as PO

    if not PO.available():
        print("模型不可用")
        return 1
    files = [f for f in sorted(os.listdir(directory))
             if f.endswith(".png") and "_name" not in f
             and not f.startswith("_")]
    rows = []
    skipped = 0
    for f in files:
        want = label_of(f, side)
        if want in ("None", "none", ""):
            skipped += 1
            continue
        img = imgio.read(os.path.join(directory, f))
        if img is None:
            skipped += 1
            continue
        try:
            pred = PO.predict(img[:, :, ::-1], topk=3)
        except Exception:
            pred = []
        got = [(base_name(n), float(c)) for n, c in pred]
        rows.append({
            "file": f, "want": base_name(want),
            "got": got[0][0] if got else None,
            "conf": got[0][1] if got else 0.0,
            "top3": got[:3],
        })

    n = len(rows)
    if not n:
        print("没有可标定的图（目录 %s）" % directory)
        return 1

    top1 = sum(1 for r in rows if r["got"] == r["want"])
    top3 = sum(1 for r in rows if any(g == r["want"] for g, _ in r["top3"]))
    print("立绘识别定标（%s，%d 张，跳过 %d）" % (side, n, skipped))
    print("  Top-1  %d / %d = %.1f%%" % (top1, n, 100.0 * top1 / n))
    print("  Top-3  %d / %d = %.1f%%" % (top3, n, 100.0 * top3 / n))
    print()
    print("按置信度分档（截断后的准确率才是敢不敢用的依据）:")
    print("  %-12s %6s %8s %8s" % ("置信度", "张数", "Top-1", "准确率"))
    bands = ((">=0.90", 0.90, 2.0), ("0.70~0.90", 0.70, 0.90),
             ("0.50~0.70", 0.50, 0.70), ("<0.50", -1.0, 0.50))
    for label, lo, hi in bands:
        sub = [r for r in rows if lo <= r["conf"] < hi]
        if not sub:
            continue
        hit = sum(1 for r in sub if r["got"] == r["want"])
        print("  %-12s %6d %8d %7.1f%%"
              % (label, len(sub), hit, 100.0 * hit / len(sub)))

    # 关键指标：把低置信度当"不知道"之后，剩下那些里的准确率
    for th in (0.5, 0.7, 0.9):
        sub = [r for r in rows if r["conf"] >= th]
        if not sub:
            continue
        hit = sum(1 for r in sub if r["got"] == r["want"])
        print("  置信度>=%.1f: 采用 %d/%d (%.0f%%)，其中正确 %.1f%%"
              % (th, len(sub), n, 100.0 * len(sub) / n,
                 100.0 * hit / len(sub)))
    print()

    bad = [r for r in rows if r["got"] != r["want"]]
    print("错误样例（前 %d / 共 %d）：" % (min(show, len(bad)), len(bad)))
    for r in bad[:show]:
        print("  %-24s 标注=%-10s 预测=%-10s %.2f  top3=%s"
              % (r["file"], r["want"], r["got"], r["conf"],
                 [(g, round(c, 2)) for g, c in r["top3"]]))

    per = collections.Counter()
    tot = collections.Counter()
    for r in rows:
        tot[r["want"]] += 1
        if r["got"] != r["want"]:
            per[r["want"]] += 1
    if per:
        print()
        print("按物种统计（错得多的，只有 >=2 张的）:")
        for k, v in per.most_common():
            if tot[k] >= 2:
                print("  %-14s %d/%d 错" % (k, v, tot[k]))

    if compare:
        compare_template_channel(directory, rows, show=10)
    return 0


def compare_template_channel(directory, rows, show=10):
    """在同一批图上跑**模板匹配通道**（留一验证）。

    为什么要留一
    ------------
    `portraits.identify()` 是在 `assets/portraits_*/` 这个库上做模板匹配，
    而我们要测的样本**就是库里的文件**。不排除自己，每张图都会以
    score=1.0 匹配到自己，准确率虚高到 100%。

    所以对第 i 张，库 = 除它自己以外的所有模板。

    为什么要和 ONNX 比
    ------------------
    生产代码里两条通道同时跑，冲突时取 "hud"（其实就是这条模板通道）。
    但没人验证过哪个更准。日志里 770 次 id conflict，一直是拍脑袋选 hud。
    """
    import cv2
    import imgio
    import portraits

    files = [r["file"] for r in rows]
    tpls = []
    for f in files:
        img = imgio.read(os.path.join(directory, f))
        if img is None:
            tpls.append(None)
            continue
        tpls.append(portraits._prepare(img[:, :, ::-1]))

    hit = 0
    used = 0
    conf_hit = 0
    conf_n = 0
    samples = []
    for i, r in enumerate(rows):
        if tpls[i] is None:
            continue
        used += 1
        best_name, best = None, -1.0
        for j, t in enumerate(tpls):
            if j == i or t is None:
                continue
            if t.shape[0] > tpls[i].shape[0] or t.shape[1] > tpls[i].shape[1]:
                continue
            try:
                res = cv2.matchTemplate(tpls[i], t, cv2.TM_CCOEFF_NORMED)
                s = float(res.max())
            except Exception:
                continue
            if s > best:
                best = s
                best_name = rows[j]["want"]
        ok = (best_name == r["want"])
        hit += 1 if ok else 0
        if best >= 0.85:
            conf_n += 1
            conf_hit += 1 if ok else 0
        if len(samples) < show and not ok:
            samples.append((r["file"], r["want"], best_name, best))

    if not used:
        return
    print()
    print("模板匹配通道（留一验证，%d 张）:" % used)
    print("  Top-1  %d / %d = %.1f%%" % (hit, used, 100.0 * hit / used))
    if conf_n:
        print("  score>=0.85: 采用 %d/%d，其中正确 %.1f%%"
              % (conf_n, used, 100.0 * conf_hit / conf_n))
    if samples:
        print("  错误样例:")
        for f, want, got, s in samples:
            print("    %-22s 标注=%-10s 模板判=%-10s %.2f"
                  % (f, want, got, s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", default="foe", choices=["foe", "self"])
    ap.add_argument("--dir", default="")
    ap.add_argument("--show", type=int, default=15)
    ap.add_argument("--compare", action="store_true",
                    help="同时跑模板匹配通道（留一验证）做对比")
    args = ap.parse_args()

    d = args.dir or os.path.join(
        ROOT, "assets",
        "portraits_foe" if args.side == "foe" else "portraits")
    return calibrate(d, args.side, args.show, args.compare)


if __name__ == "__main__":
    sys.exit(main())
