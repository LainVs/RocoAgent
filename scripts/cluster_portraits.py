# -*- coding: utf-8 -*-
"""把未标注头像按相似度聚类，输出分组，方便一次性命名。"""
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), *([".."] * 0), "src"))
import paths
import glob
import os

import cv2
import numpy as np

LIB = str(paths.ROOT / "assets/portraits")
OUT_IMG = str(paths.ROOT / "logs/portrait_groups.png")
OUT_TXT = os.path.join(os.environ["TEMP"], "oc_groups.txt")


def prep(path):
    im = cv2.imread(path)
    if im is None:
        return None
    im = cv2.resize(im, (64, 64), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(im, (3, 3), 0).astype(np.float32)


def main():
    files = sorted(glob.glob(os.path.join(LIB, "_unknown_*.png")))
    files = [f for f in files if not f.endswith("_name.png")]
    imgs = [(f, prep(f)) for f in files]
    imgs = [(f, i) for f, i in imgs if i is not None]

    groups = []
    for f, im in imgs:
        placed = False
        for g in groups:
            r = cv2.matchTemplate(im, g["ref"], cv2.TM_CCOEFF_NORMED)
            if float(r.max()) >= 0.72:
                g["files"].append(f)
                placed = True
                break
        if not placed:
            groups.append({"ref": im, "files": [f]})

    lines = []
    lines.append("共 %d 张，聚成 %d 组" % (len(imgs), len(groups)))
    for gi, g in enumerate(groups, 1):
        lines.append("\n组 %d（%d 张）" % (gi, len(g["files"])))
        for f in g["files"]:
            lines.append("   " + os.path.basename(f))

    # 拼图：每组一行，第一列是组中心
    rows = []
    for gi, g in enumerate(groups, 1):
        tiles = [cv2.resize(cv2.imread(f), (110, 116)) for f in g["files"][:10]]
        while len(tiles) < 10:
            tiles.append(np.zeros((116, 110, 3), dtype=np.uint8))
        row = np.hstack(tiles)
        cv2.putText(row, "G%d (%d)" % (gi, len(g["files"])), (4, 110),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        rows.append(row)
    if rows:
        cv2.imwrite(OUT_IMG, np.vstack(rows))

    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
    print("ok")
