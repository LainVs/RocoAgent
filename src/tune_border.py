"""tune_border.py - pick a border-gray threshold that separates battle from menus.

Ground truth by filename:
  battle  : f0 f1 f4 g1 k1
  menu    : h2 h3 h5 h6 h8   (victory / result / lobby)
"""
import glob
import os
import sys

import cv2
import numpy as np

LOGS = r"D:\Projects\RocoAgent\logs"
MY = (166, 170, 244, 20)
FOE = (2251, 170, 245, 20)


def load(name):
    p = os.path.join(LOGS, name + ".png")
    if not os.path.exists(p):
        return None
    return cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]


def border_score(rgb, rect, sat_max, lo, hi):
    x, y, w, h = rect
    if x + w > rgb.shape[1] or y + h + 2 >= rgb.shape[0]:
        return 0.0
    edge = np.concatenate([rgb[y - 2:y, x:x + w], rgb[y + h:y + h + 2, x:x + w]])
    er = edge[:, :, 0].astype(np.int16)
    eg = edge[:, :, 1].astype(np.int16)
    eb = edge[:, :, 2].astype(np.int16)
    mx = np.maximum(np.maximum(er, eg), eb)
    mn = np.minimum(np.minimum(er, eg), eb)
    return float((((mx - mn) < sat_max) & (mx > lo) & (mx < hi)).mean())


battle = [n for n in ["f0", "f1", "f4", "g1", "k1"] if load(n) is not None]
menu = [n for n in ["h2", "h3", "h5", "h6", "h8"] if load(n) is not None]

print("%-6s %-6s %8s %8s %8s" % ("sat", "lo-hi", "battleMin", "menuMax", "margin"))
for sat in (14, 20, 24, 30, 36):
    for lo, hi in ((70, 125), (60, 135), (55, 140)):
        bs, ms = [], []
        for n in battle:
            rgb = load(n)
            bs.append(min(border_score(rgb, MY, sat, lo, hi),
                          border_score(rgb, FOE, sat, lo, hi)))
        for n in menu:
            rgb = load(n)
            ms.append(max(border_score(rgb, MY, sat, lo, hi),
                          border_score(rgb, FOE, sat, lo, hi)))
        if not bs or not ms:
            continue
        print("%-6d %-6s %8.3f %8.3f %8.3f" % (
            sat, "%d-%d" % (lo, hi), min(bs), max(ms), min(bs) - max(ms)))
