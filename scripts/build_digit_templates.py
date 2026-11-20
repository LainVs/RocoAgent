# -*- coding: utf-8 -*-
"""build_digit_templates.py —— 从现有帧自监督建"血条数字"字形库

为什么不用 OCR 在线跑
--------------------
血条数字只有 3~4 位、字高 7~20px，RapidOCR 一次 0.2~0.4s，解析成功率 ~50%。
但它的**错法**是稳定的（分隔符识别成数字、多加一位）。
于是：**OCR 只在离线跑一次当标注器，在线只做模板匹配（<1ms）**。

为什么是"先聚类再投票"，不是"按位置贴标签"
------------------------------------------
按位置贴标签时，只要有一处斜杠粘连，后面所有字都会被整体错位
（实测第 7 簇里把 4、5 都贴成了 7）。改成：
    1) 先把所有字形按外形聚类（NCC > 0.86 归一类）
    2) 每个簇用"位置标签的多数票"定名字
    3) 少数派噪声会被多数票纠正掉
这样即使 20% 的序列错位，模板仍然是干净的。

用法
----
    python scripts\\build_digit_templates.py --limit 150
    python scripts\\build_digit_templates.py --limit 150 --montage
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

OUT = os.path.join(ROOT, "assets", "digits_hp")
GLYPH = (16, 16)          # (w, h)


def to_client(img):
    if img is None:
        return None
    if img.shape[:2] == (1671, 2586):
        return img[58:58 + 1600, 13:13 + 2560]
    return img


def white_mask(crop):
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    return ((hsv[:, :, 2] > 190) & (hsv[:, :, 1] < 110)).astype(np.uint8)


def split_glyphs(mask, min_gap):
    """按列投影把白字切成一个个字形。返回 [(x0, x1, y0, y1)]。

    为什么先找"文字行带"：血条顶上常有一条**高光行**，它横跨整条，
    直接按列投影会把所有字并成一个。所以先在行投影里找出最密的一段
    （文字只占中间几条），再在带内切列。
    """
    h, w = mask.shape[:2]
    if h < 4 or w < 8:
        return []
    rows = mask.sum(axis=1)
    if rows.max() < 3:
        return []
    # 以最密的一行为中心，向上下扩到密度跌到 35% 以下
    peak = int(np.argmax(rows))
    thr = max(2, int(rows[peak] * 0.35))
    y0 = peak
    while y0 > 0 and rows[y0 - 1] >= thr:
        y0 -= 1
    y1 = peak
    while y1 < h - 1 and rows[y1 + 1] >= thr:
        y1 += 1
    band = mask[y0:y1 + 1]
    if band.shape[0] < 3:
        return []
    col = band.sum(axis=0) > max(1, int(band.shape[0] * 0.20))
    idx = np.flatnonzero(col)
    if idx.size < 3:
        return []
    runs = []
    start = prev = int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i - prev <= min_gap:
            prev = i
            continue
        runs.append((start, prev))
        start = prev = i
    runs.append((start, prev))
    out = []
    for x0c, x1c in runs:
        if x1c - x0c < 1:
            continue
        sub = band[:, x0c:x1c + 1]
        rr = np.flatnonzero(sub.any(axis=1))
        if rr.size == 0:
            continue
        out.append((x0c, x1c, y0 + int(rr[0]), y0 + int(rr[-1])))
    return out


def norm_glyph(mask, box):
    x0, x1, y0, y1 = box
    sub = mask[y0:y1 + 1, x0:x1 + 1]
    if sub.size == 0:
        return None
    h, w = sub.shape
    th = GLYPH[1]
    tw = max(1, int(round(w * th / float(h))))
    r = cv2.resize(sub.astype(np.float32), (tw, th), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((th, GLYPH[0]), dtype=np.float32)
    if tw >= GLYPH[0]:
        canvas[:] = r[:, :GLYPH[0]]
    else:
        off = (GLYPH[0] - tw) // 2
        canvas[:, off:off + tw] = r
    return canvas


def ncc(a, b):
    """两个归一化字形的相关系数。"""
    a = a.reshape(-1) - a.mean()
    b = b.reshape(-1) - b.mean()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-6 or nb < 1e-6:
        return 0.0
    return float(a.dot(b) / (na * nb))


def collect(files, log=print):
    """-> [(glyph, positional_label)]"""
    samples = []
    nbars = 0
    for f in files:
        img = to_client(cv2.imread(f))
        if img is None:
            continue
        hud = matdet.locate_hud(img)
        for box, side in ((hud["my_bar"], "my"), (hud["foe_bar"], "foe")):
            if box is None:
                continue
            nbars += 1
            crop, gs = matdet.digit_strip(img, box)
            if crop is None:
                continue
            m = matdet.white_mask(crop)
            if m.sum() < 20:
                continue
            gl = matdet.split_glyphs(
                m, min_gap=max(2, int(round(box["h"] * gs * 0.10))))
            if not (3 <= len(gl) <= 10):
                continue
            ratio, text = matdet.read_hp_text(img, box, side)
            seq = None
            if side == "my":
                got = matdet.parse_hp_cur_max(text)
                if got:
                    s = "%d/%d" % got
                    if len(s) == len(gl):
                        seq = s
            else:
                r = matdet.parse_pct(text)
                if r is not None:
                    s = "%d" % int(round(r * 100))
                    if len(s) == len(gl):
                        seq = s
                    elif len(s) + 1 == len(gl):
                        # 多出来的那个是百分号
                        seq = s + "%"
            if not seq:
                continue
            for g, ch in zip(gl, seq):
                gimg = matdet.norm_glyph(m, g)
                if gimg is not None:
                    samples.append((gimg, ch))
    log("  bars=%d glyphs=%d" % (nbars, len(samples)))
    return samples


def cluster(samples, thr=0.86):
    """贪心聚类：与已有簇心够像就归进去，否则开新簇。"""
    centers = []      # list of [sum_vec, count, labels(Counter)]
    labels = []
    for g, ch in samples:
        best, bi = -1.0, -1
        for i, c in enumerate(centers):
            v = ncc(g, c[0] / max(1, c[1]))
            if v > best:
                best, bi = v, i
        if best >= thr and bi >= 0:
            c = centers[bi]
            c[0] = c[0] + g
            c[1] += 1
            c[2][ch] = c[2].get(ch, 0) + 1
        else:
            centers.append([g.copy(), 1, {ch: 1}])
    out = []
    for s, n, lab in centers:
        maj, cnt = max(lab.items(), key=lambda kv: kv[1])
        total = sum(lab.values())
        out.append({"centroid": s / max(1, n), "n": n,
                    "label": maj if maj != "-" else None,
                    "purity": cnt / float(total), "votes": dict(lab)})
    out.sort(key=lambda d: -d["n"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--thr", type=float, default=0.86)
    ap.add_argument("--montage", action="store_true")
    ap.add_argument("--keep-leading", type=int, default=2,
                    help="每个标签保留几个簇心（不同分辨率/描边厚度）")
    a = ap.parse_args()

    files = []
    for pat in ("logs/shot/*.jpg", "logs/resultshots/*.jpg",
                "logs/frames/*.png"):
        files += sorted(glob.glob(os.path.join(ROOT, pat)))
    step = max(1, len(files) // a.limit) if len(files) > a.limit else 1
    files = files[::step][:a.limit]
    print("frames=%d" % len(files))

    samples = collect(files)
    if not samples:
        print("no glyphs")
        return 2
    cl = cluster(samples, a.thr)
    print("clusters=%d" % len(cl))
    for c in cl[:30]:
        print("   n=%4d label=%-5s purity=%.2f votes=%s"
              % (c["n"], c["label"], c["purity"],
                 {k: v for k, v in sorted(c["votes"].items()) if k != "-"}))

    # 每个标签取票数最多的 keep_leading 个簇心
    by_label = {}
    for c in cl:
        if c["label"] is None or c["purity"] < 0.75:
            continue
        by_label.setdefault(c["label"], []).append(c)
    templates, tlabels = [], []
    for lab, lst in by_label.items():
        lst.sort(key=lambda d: -d["n"])
        for c in lst[:a.keep_leading]:
            templates.append(c["centroid"].astype(np.float32))
            tlabels.append(lab)
    print("templates=%d  labels=%s" % (len(templates), sorted(set(tlabels))))

    os.makedirs(OUT, exist_ok=True)
    # 存**全部样本**而不是簇心：k-NN 比"每类 2 个质心"稳得多
    # （实测：质心版把 354 认成 95、100 认成 78；样本多了近邻投票能纠正）
    # 但先按"簇纯净度"过滤：位置对齐贴错标签的样本会被剔除。
    keep_g, keep_l = [], []
    if templates:
        cents = np.stack(templates)
        purity = np.array([c["purity"] for c in cl if c["label"] is not None])
        cent_lab = np.array(tlabels)
        for g, ch in samples:
            sims = np.array([ncc(g, c) for c in cents])
            k = int(np.argmax(sims))
            if cent_lab[k] == ch and (k < len(purity) and purity[k] >= 0.8):
                keep_g.append(g)
                keep_l.append(ch)
    if not keep_g:
        keep_g = [g for g, _ in samples]
        keep_l = [ch for _, ch in samples]
    all_tpl = np.stack(keep_g).astype(np.float32)
    all_lab = np.array(keep_l)
    print("keep samples %d / %d" % (len(keep_g), len(samples)))
    np.savez_compressed(os.path.join(OUT, "templates.npz"),
                        tpl=all_tpl,
                        labels=all_lab,
                        centroids=np.stack(templates) if templates else all_tpl[:0],
                        centroid_labels=np.array(tlabels),
                        n=int(all_tpl.shape[0]))
    with open(os.path.join(OUT, "build_report.json"), "w", encoding="utf-8") as fh:
        json.dump({"frames": len(files), "glyphs": len(samples),
                   "clusters": [{"n": c["n"], "label": c["label"],
                                 "purity": round(c["purity"], 3)}
                                for c in cl],
                   "templates": len(templates)},
                  fh, ensure_ascii=False, indent=1)

    if a.montage:
        cols = 6
        tw, thh = GLYPH[0] * 2, GLYPH[1] * 2
        W = 70 + cols * (tw + 6)
        H = int(np.ceil(len(templates) / float(cols))) * (thh + 8) + 8
        canvas = np.zeros((H, W, 3), dtype=np.uint8)
        for k, (t, lab) in enumerate(zip(templates, tlabels)):
            r, c = divmod(k, cols)
            y = 4 + r * (thh + 8)
            x = 66 + c * (tw + 6)
            cv2.putText(canvas, str(lab), (4, y + thh // 2 + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
            big = cv2.resize(np.clip(t * 255, 0, 255).astype(np.uint8),
                             (tw, thh), interpolation=cv2.INTER_NEAREST)
            canvas[y:y + thh, x:x + tw] = cv2.cvtColor(big, cv2.COLOR_GRAY2BGR)
        p = os.path.join(ROOT, "logs", "det2", "digit_templates.png")
        cv2.imwrite(p, canvas)
        print("montage ->", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
