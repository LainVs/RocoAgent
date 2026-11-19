# -*- coding: utf-8 -*-
"""头像库体检：找出"标签是错的"的模板。

问题：模板是机器人自己抓、再由名字条 OCR 自动打标的。名字条一读错
（或者抓到了换人动画中途），一张错标的模板就会污染整个库 ——
实测出现过"我方是卡瓦重，却稳定认成音速犬"这种情况。

判据（不需要人工标注）：
    同一只精灵的模板之间应该高度相似；如果某张模板和"别的精灵"比和
    "自己的同族"更像，那它多半标错了。

动作：
    - 组内最大相关 < 0.55 -> 疑似废图，移到 _quarantine/
    - 跨组最佳匹配 > 0.78 且比组内最佳还高 -> 疑似错标，移到 _quarantine/
    - 只报告，不删，人工/下次再定

用法：
    python verify_portraits.py --dry
    python verify_portraits.py
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2          # noqa: E402
import numpy as np  # noqa: E402

import portrait_db as P   # noqa: E402

INTRA_MIN = 0.55
CROSS_MAX = 0.78


def load_gray(path):
    buf = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    return cv2.resize(img, (48, 48)).astype(np.float32)


def corr(a, b):
    a = a - a.mean()
    b = b - b.mean()
    den = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-6
    return float((a * b).sum() / den)


def main():
    dry = "--dry" in sys.argv
    for lib, side in ((P.SELF_DIR, "self"), (P.FOE_DIR, "foe")):
        if not os.path.isdir(lib):
            continue
        files = sorted(f for f in os.listdir(lib)
                       if f.endswith(".png") and not f.startswith("_"))
        imgs = {}
        for f in files:
            g = load_gray(os.path.join(lib, f))
            if g is not None:
                imgs[f] = g
        groups = {}
        for f in imgs:
            sid = P.species_of(f, side)
            groups.setdefault(sid if sid is not None else ("?", f), []).append(f)

        print("== %s: %d 张, %d 组 ==" % (side, len(imgs), len(groups)))
        bad = []
        for f, g in imgs.items():
            sid = P.species_of(f, side)
            intra = max([corr(g, imgs[o]) for o in imgs
                         if o != f and P.species_of(o, side) == sid] or [0.0])
            cross_best, cross_name = 0.0, None
            for o, og in imgs.items():
                if o == f or P.species_of(o, side) == sid:
                    continue
                c = corr(g, og)
                if c > cross_best:
                    cross_best, cross_name = c, o
            flag = None
            if intra < INTRA_MIN and len(groups.get(sid, [])) > 1:
                flag = "组内不像(%.2f)" % intra
            elif cross_best > CROSS_MAX and cross_best > intra:
                flag = "更像 %s (%.2f vs 组内 %.2f)" % (cross_name, cross_best, intra)
            if flag:
                bad.append((f, flag))
        for f, why in bad:
            print("  SUSPECT %-24s %s" % (f, why))
            if not dry:
                q = os.path.join(lib, "_quarantine")
                os.makedirs(q, exist_ok=True)
                try:
                    shutil.move(os.path.join(lib, f), os.path.join(q, f))
                except OSError:
                    pass
        print("  可疑 %d 张%s" % (len(bad), "" if dry else "，已移到 _quarantine/"))


if __name__ == "__main__":
    main()
