# -*- coding: utf-8 -*-
"""autolabel_unknown.py —— 用 ONNX 模型自动标注"未知对面头像"。

为什么值得做
------------
实测（2026-11-21 04:45）40 张未知对面头像里：
    17 张能被 620 类模型高置信识别（>=0.85）
    识别出的 10 个名字**全部在库里**
    （黑化加尔（黑化的样子）5 次、霹雳迪迪 3 次、邪眼巨魔 2 次…）

这些样本现在躺在 assets/portraits_foe/_unknown_*.png 里没人用，
而 HUD 模板库只有 71 张、覆盖率 46%。把它们标好能直接提高覆盖率。

安全策略
--------
· 只标置信 >= 0.90 的（比 0.85 更严，宁缺勿滥）
· 名字必须能在 species 表里查到
· 同名已有多张时最多补到 3 张（避免同类样本刷爆模板库）
· 默认只预览，加 --apply 才真的改名

用法
----
    python scripts\\autolabel_unknown.py            # 预览
    python scripts\\autolabel_unknown.py --apply    # 执行
"""
from __future__ import annotations

import glob
import os
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict

import numpy as np

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))

import portrait_onnx as P  # noqa: E402
from portrait_db import DB_PATH, add_portrait  # noqa: E402

CONF_MIN = 0.90
MAX_PER_NAME = 3


def main():
    apply = "--apply" in sys.argv
    if not P.available():
        print("模型不可用")
        return 1

    foe_dir = os.path.join(ROOT, "assets", "portraits_foe")
    files = [f for f in glob.glob(os.path.join(foe_dir, "_unknown_*.png"))
             if "_name" not in os.path.basename(f)]
    print("未知对面头像: %d 张" % len(files))

    # 现有每个名字的模板数
    have = Counter()
    for f in glob.glob(os.path.join(foe_dir, "*.png")):
        b = os.path.basename(f)
        if b.startswith("_unknown_"):
            continue
        have[b[:-4].split("#")[0]] += 1

    import cv2
    db = sqlite3.connect(DB_PATH)
    plan = []
    skipped_conf = skipped_db = skipped_full = 0
    for f in files:
        try:
            im = cv2.imdecode(np.fromfile(f, dtype=np.uint8),
                              cv2.IMREAD_UNCHANGED)
        except Exception:
            continue
        if im is None:
            continue
        if im.ndim == 2:
            im = cv2.cvtColor(im, cv2.COLOR_GRAY2RGB)
        r = P.predict(im, topk=1)
        if not r:
            continue
        name, conf = r[0]
        if conf < CONF_MIN:
            skipped_conf += 1
            continue
        row = db.execute("SELECT id FROM species WHERE name=? LIMIT 1",
                         (name,)).fetchone()
        if not row:
            skipped_db += 1
            continue
        if have[name] >= MAX_PER_NAME:
            skipped_full += 1
            continue
        have[name] += 1
        plan.append((f, name, conf, row[0]))

    db.close()
    print("\n=== 计划 ===")
    print("  可自动标注: %d 张" % len(plan))
    print("  跳过（置信 <%.2f）: %d" % (CONF_MIN, skipped_conf))
    print("  跳过（库里没有）  : %d" % skipped_db)
    print("  跳过（已达上限 %d）: %d" % (MAX_PER_NAME, skipped_full))
    print()
    by_name = defaultdict(list)
    for f, name, conf, sid in plan:
        by_name[name].append((os.path.basename(f), conf))
    for name, items in sorted(by_name.items(), key=lambda kv: -len(kv[1])):
        print("  %-22s %d 张  置信 %s"
              % (name, len(items),
                 ", ".join("%.2f" % c for _f, c in items[:3])))

    if not apply:
        print("\n（预览，加 --apply 执行）")
        return 0

    print("\n=== 执行 ===")
    n = 0
    for f, name, conf, sid in plan:
        base = os.path.basename(f)[:-4]
        dst = os.path.join(foe_dir, "%s.png" % name)
        # 已存在同名就加序号
        k = 1
        while os.path.exists(dst):
            k += 1
            dst = os.path.join(foe_dir, "%s#%d.png" % (name, k))
        try:
            shutil.move(f, dst)
            add_portrait(os.path.basename(dst), sid, "普通", "foe", "onnx")
            # 名字条一起改名（保持配对）
            src_name = f[:-4] + "_name.png"
            if os.path.exists(src_name):
                shutil.move(src_name, dst[:-4] + "_name.png")
            n += 1
        except Exception as e:
            print("  !! %s: %s" % (os.path.basename(f), e))
    print("已标注 %d 张" % n)
    print("剩余未知: %d 张"
          % len([x for x in glob.glob(os.path.join(foe_dir, "_unknown_*.png"))
                 if "_name" not in os.path.basename(x)]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
