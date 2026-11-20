# -*- coding: utf-8 -*-
"""audit_unknown_portraits.py —— 用 ONNX 模型盘点"未知对面头像"能否识别。

背景
----
实测（2026-11-21 04:40）最近 40 分钟：
    FOE 换人 403 次 -> HUD 认出 187 (46%)、立绘 51 (13%)、未知 182 (45%)
这 45% 的未知样本被存成 _unknown_*.png，从来没被回头利用。

本脚本用 roco-cal 的 620 类模型跑一遍这些未知样本，看：
  1. 有多少能被高置信识别（>=0.85）
  2. 识别出的名字是否在库里（能否补进头像库）

用法
----
    python scripts\\audit_unknown_portraits.py
"""
from __future__ import annotations

import glob
import os
import sys
from collections import Counter

import numpy as np

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))

import portrait_onnx as P  # noqa: E402


def main():
    if not P.available():
        print("模型不可用")
        return 1

    foe_dir = os.path.join(ROOT, "assets", "portraits_foe")
    files = [f for f in glob.glob(os.path.join(foe_dir, "_unknown_*.png"))
             if "_name" not in os.path.basename(f)]
    print("未知对面头像: %d 张" % len(files))
    if not files:
        return 0

    import cv2
    hits = Counter()
    confs = []
    for f in files:
        try:
            buf = np.fromfile(f, dtype=np.uint8)
            im = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
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
        confs.append(conf)
        if conf >= 0.85:
            hits[name] += 1

    print("\n可高置信识别(>=0.85): %d / %d"
          % (sum(hits.values()), len(files)))
    if confs:
        confs.sort()
        print("置信度: 中位 %.2f  P90 %.2f  最高 %.2f"
              % (confs[len(confs) // 2], confs[int(len(confs) * 0.9)],
                 confs[-1]))
    print("\n识别结果分布（前 15）:")
    for n, c in hits.most_common(15):
        print("  %-16s %d 次" % (n, c))

    # 这些名字在库里吗
    import sqlite3
    from portrait_db import DB_PATH
    db = sqlite3.connect(DB_PATH)
    in_db = 0
    not_in = []
    for n in hits:
        r = db.execute("SELECT 1 FROM species WHERE name=? LIMIT 1",
                       (n,)).fetchone()
        if r:
            in_db += 1
        else:
            not_in.append(n)
    db.close()
    print("\n其中在库里的: %d / %d" % (in_db, len(hits)))
    if not_in:
        print("  不在库里: %s" % ", ".join(not_in[:10]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
