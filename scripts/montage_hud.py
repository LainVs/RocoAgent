# -*- coding: utf-8 -*-
"""montage_hud.py —— 把血条裁出来拼成"人工读数用"的对照图

为什么要专门的工具
------------------
之前的"真值"是 OCR，而 OCR 自己就会错（解析成功率 ~50%），
用不准的尺子量不准的尺子，得到的只是"一致性"，不是准确度。

这个工具只做一件事：把每一帧的 HUD 条放大、编号、拼图，
人（或模型）**只看原图**读数。图里**不画任何检测框**，避免被检测器的结论带偏。

输出
----
    logs/annot/hud_00.png   对照图（每行 = 一帧，左我方右敌方）
    logs/annot/index.json   行号 -> {frame, side, crop, bar_h, scale}
    logs/annot/spec.json    本次抽样的分层统计

用法
----
    python scripts\\montage_hud.py --per 12 --max 96
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import matdet  # noqa: E402

OUT = os.path.join(ROOT, "logs", "annot")
ROW_H = 44              # 每行目标高度（放大后血条的高度，便于读数）


def to_client(img):
    if img is None:
        return None
    if img.shape[:2] == (1671, 2586):
        return img[58:58 + 1600, 13:13 + 2560]
    return img


def crop_around(img, box, x_wide=2.2, y_up=0.9, y_dn=2.2):
    """以血条为中心，横向放宽到"整条 HUD"，纵向留出数字上下留白。"""
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    x0 = max(0, int(x - w * (x_wide - 1) * 0.6))
    x1 = min(img.shape[1], int(x + w + w * (x_wide - 1) * 0.6))
    y0 = max(0, int(y - h * y_up))
    y1 = min(img.shape[0], int(y + h * y_dn))
    return img[y0:y1, x0:x1], (x0, y0, x1 - x0, y1 - y0)


def scale_to(img, target_h):
    if img is None or img.size == 0:
        return None, 1.0
    s = max(1.0, target_h / max(1, img.shape[0]))
    return cv2.resize(img, None, fx=s, fy=s,
                      interpolation=cv2.INTER_CUBIC), s


def collect(limit):
    """分层抽样：按 (分辨率, 血条高, 血量桶) 分层，避免只抽到一种帧。"""
    files = []
    for pat in ("logs/frames/*.png", "logs/resultshots/*.jpg",
                "logs/shot/*.jpg"):
        files += sorted(glob.glob(os.path.join(ROOT, pat)))
    step = max(1, len(files) // max(1, limit * 3))
    cand = files[::step]

    picked = []
    seen = {}
    for f in cand:
        if len(picked) >= limit:
            break
        img = to_client(cv2.imread(f))
        if img is None:
            continue
        hud = matdet.locate_hud(img)
        for side in ("my", "foe"):
            bar = hud["%s_bar" % side]
            if bar is None:
                continue
            bh = bar["h"]
            res = "%dx%d" % (img.shape[1], img.shape[0])
            key = (res, bh // 4)          # 血条高按 4px 分箱
            if seen.get(key, 0) >= 8:      # 每格最多 8 张
                continue
            seen[key] = seen.get(key, 0) + 1
            picked.append({"file": f, "side": side, "bar": bar,
                           "res": res, "bar_h": bh})

    return picked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=12, help="每张图几行")
    ap.add_argument("--max", type=int, default=96, help="最多标多少条")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    items = collect(a.max)
    if not items:
        print("no frames")
        return 2

    index = []
    pages = [items[i:i + a.per] for i in range(0, len(items), a.per)]
    for pi, page in enumerate(pages):
        rows = []
        for it in page:
            img = to_client(cv2.imread(it["file"]))
            if img is None:
                continue
            crop, cb = crop_around(img, it["bar"])
            big, s = scale_to(crop, ROW_H * 2)
            if big is None:
                continue
            idx = len(index)
            index.append({"idx": idx, "file": os.path.relpath(it["file"], ROOT),
                          "side": it["side"], "res": it["res"],
                          "bar_h": it["bar_h"], "crop": list(cb),
                          "scale": round(s, 2)})
            rows.append((idx, big))
        if not rows:
            continue
        W = max(r[1].shape[1] for r in rows) + 90
        H = sum(r[1].shape[0] + 10 for r in rows) + 12
        canvas = np.zeros((H, W, 3), dtype=np.uint8)
        y = 6
        for idx, im in rows:
            cv2.putText(canvas, "%02d" % idx, (6, y + im.shape[0] // 2 + 7),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
            canvas[y:y + im.shape[0], 70:70 + im.shape[1]] = im
            y += im.shape[0] + 10
        p = os.path.join(OUT, "hud_%02d.png" % pi)
        cv2.imwrite(p, canvas)
        print("page ->", p, "rows", len(rows))

    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=1)
    spec = {"total": len(index),
            "by_res": {}, "by_barh": {}}
    for it in index:
        spec["by_res"][it["res"]] = spec["by_res"].get(it["res"], 0) + 1
        k = "%d-%dpx" % (it["bar_h"] // 4 * 4, it["bar_h"] // 4 * 4 + 3)
        spec["by_barh"][k] = spec["by_barh"].get(k, 0) + 1
    with open(os.path.join(OUT, "spec.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=1)
    print("total", len(index), json.dumps(spec, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
