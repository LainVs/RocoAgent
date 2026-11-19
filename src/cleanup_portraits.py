# -*- coding: utf-8 -*-
"""清理头像库：去空白、去近似重复、每种最多留 N 张。

为什么需要：
    机器人每 12 秒存一张未知头像，同一只精灵能存几十张，其中夹着
    "换人动画中途"和"头像被挡住"的废图。废图会拉低匹配分数，也会让
    每次识别多花几毫秒乘以模板数。

留下哪张：
    质量分 = 灰度标准差（越有内容越高）× 边缘密度。
    空白/纯色背景的分数接近 0，会被直接删掉。
    同一只精灵内部再做两两相关，>0.95 的认为是同一张，只留分高的。

用法：
    python cleanup_portraits.py --dry
    python cleanup_portraits.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2          # noqa: E402
import numpy as np  # noqa: E402

import portrait_db as P   # noqa: E402

KEEP_PER_SPECIES = 3
MIN_QUALITY = 12.0
DUP_CORR = 0.95


def load(path):
    buf = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def quality(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    std = float(g.std())
    edges = float(cv2.Laplacian(g, cv2.CV_8U).mean())
    return std * (1.0 + edges / 10.0)


def main():
    dry = "--dry" in sys.argv
    total_removed = 0
    for lib, side in ((P.SELF_DIR, "self"), (P.FOE_DIR, "foe")):
        if not os.path.isdir(lib):
            continue
        files = sorted(f for f in os.listdir(lib)
                       if f.endswith(".png") and not f.startswith("_"))
        groups = {}
        for f in files:
            sid = P.species_of(f, side)
            groups.setdefault(sid if sid is not None else ("?", f), []).append(f)

        print("== %s: %d 张, %d 组 ==" % (side, len(files), len(groups)))
        for key, fs in groups.items():
            scored = []
            for f in fs:
                img = load(os.path.join(lib, f))
                if img is None:
                    continue
                q = quality(img)
                if q < MIN_QUALITY:
                    print("  drop blank  %-24s q=%.1f" % (f, q))
                    if not dry:
                        os.remove(os.path.join(lib, f))
                    total_removed += 1
                    continue
                scored.append((q, f, img))
            scored.sort(reverse=True, key=lambda t: t[0])
            kept = []
            for q, f, img in scored:
                small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (32, 32)).astype(np.float32)
                dup = False
                for _q2, _f2, kimg in kept:
                    k = cv2.resize(cv2.cvtColor(kimg, cv2.COLOR_BGR2GRAY), (32, 32)).astype(np.float32)
                    a = small - small.mean()
                    b = k - k.mean()
                    den = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-6
                    if float((a * b).sum() / den) > DUP_CORR:
                        dup = True
                        break
                if dup or len(kept) >= KEEP_PER_SPECIES:
                    print("  drop dup    %-24s q=%.1f" % (f, q))
                    if not dry:
                        os.remove(os.path.join(lib, f))
                    total_removed += 1
                    continue
                kept.append((q, f, img))
            print("  keep %d for %s: %s" % (len(kept), key,
                                             ", ".join(f for _q, f, _i in kept)))
    print("\nremoved %d" % total_removed)


if __name__ == "__main__":
    main()
