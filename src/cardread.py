# -*- coding: utf-8 -*-
"""技能卡读取（颜色无关版）。

老实现（digitread.find_pills）只认 `BG=(61,61,61)` 的深灰胶囊，
但实测胶囊底色有**三种**：
    绿值 → 深灰底
    红值 → 暗红底
    白值 → 白底
于是 4 张卡只有 1 张能被找到，读数率 ~25%。

本模块只依赖两件与颜色无关的性质：
    ① 胶囊是一个**内部颜色均匀的圆角矩形**（宽 44-130、高 22-52）
    ② 里面有一串**与底色对比明显**的字形
底色从胶囊边框现采，所以绿/红/白都能处理。
"""
from __future__ import annotations
import paths

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(paths.TOOLS))

import cv2
import numpy as np

import digitread as dr


def _uniform_rects(sub, min_w=40, max_w=140, min_h=20, max_h=58, max_std=26.0):
    """找出"内部颜色均匀"的矩形候选。"""
    # 中值滤波把细笔画抹掉，胶囊就变成一块"纯色方块"，
    # 而卡面美术纹理丰富、抹完仍然是花的 —— 这就是颜色无关的判据。
    med = cv2.medianBlur(sub, 15)
    key = (med // 24).astype(np.int32)
    code = key[:, :, 0] * 1024 + key[:, :, 1] * 32 + key[:, :, 2]
    code = code.astype(np.int32)
    _, idx = np.unique(code, return_inverse=True)
    idx = (idx.reshape(sub.shape[:2]) % 255).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(idx, 8)
    out = []
    h, w = med.shape[:2]
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if not (min_w <= ww <= max_w and min_h <= hh <= max_h):
            continue
        if not (0.8 <= ww / float(hh) <= 5.0):
            continue
        region = med[y:y + hh, x:x + ww]
        if region.size == 0:
            continue
        # 内部标准差足够小 = 颜色均匀（排除卡面美术）
        if float(region.reshape(-1, 3).std(axis=0).mean()) > max_std:
            continue
        out.append((int(x), int(y), int(ww), int(hh), int(area)))
    out.sort(key=lambda t: t[1])
    return out


def _bg_of(sub, box):
    """从矩形四角中位数取底色。"""
    x, y, w, h = box
    pads = []
    for dx, dy in ((3, 3), (w - 4, 3), (3, h - 4), (w - 4, h - 4)):
        px = sub[min(y + dy, sub.shape[0] - 1), min(x + dx, sub.shape[1] - 1)]
        pads.append(px.astype(np.int16))
    return np.median(np.stack(pads), axis=0)


def _glyph_mask(sub, box, bg, thr=42):
    """与底色差异明显的像素 = 字形。"""
    x, y, w, h = box
    region = sub[y:y + h, x:x + w].astype(np.int16)
    d = np.abs(region - bg.reshape(1, 1, 3)).max(axis=2)
    return (d > thr).astype(np.uint8), region


def read_bar(rgb, tpl=None):
    """读四张卡。返回长度 4 的列表，元素 {value,text,kind,color,box,bg} 或 None。"""
    if rgb is None or getattr(rgb, "size", 0) == 0:
        return [None] * 4
    if tpl is None:
        tpl = dr.load_templates()

    rects = _uniform_rects(rgb)
    picks = []
    for r in rects:
        x, y, w, h, _a = r
        bg = _bg_of(rgb, r)
        mask, region = _glyph_mask(rgb, (x, y, w, h), bg)
        frac = float(mask.mean())
        # 胶囊里字占 8%-45%；太干净=空块，太满=美术
        if not (0.05 <= frac <= 0.50):
            continue
        picks.append((y, x, w, h, bg, mask))

    # 按 y 聚合到 4 个卡位（卡是竖排的，间距 > 60）
    picks.sort()
    slots = [None, None, None, None]
    if picks:
        ys = [p[0] for p in picks]
        groups = []
        cur = [picks[0]]
        for p in picks[1:]:
            if p[0] - cur[-1][0] < 60:
                cur.append(p)
            else:
                groups.append(cur)
                cur = [p]
        groups.append(cur)
        for i, grp in enumerate(groups[:4]):
            best = max(grp, key=lambda t: t[2] * t[3])
            slots[i] = best

    out = []
    for s in slots:
        if not s:
            out.append(None)
            continue
        y, x, w, h, bg, mask = s
        # 用 digitread 的字形切分/分类（它本身是准的）
        blobs = dr.segment(mask.astype(np.float32))
        if not blobs:
            out.append({"value": None, "text": "--", "kind": "dash",
                        "color": None, "box": (x, y, w, h)})
            continue
        digits, scores = [], []
        for (bx, by, bw, bh, _a) in blobs:
            g, ar = dr.norm_glyph(mask.astype(np.float32), (bx, by, bw, bh))
            if g is None:
                continue
            lab, sc = dr.classify(g, ar, tpl) if tpl else (None, 0)
            digits.append(str(lab) if (lab is not None and sc >= dr.TPL_MATCH) else "?")
            scores.append(sc)
        text = "".join(digits)
        out.append({
            "value": int(text) if text.isdigit() else None,
            "text": text or "--",
            "kind": "num",
            "color": _color_of(bg),
            "box": (x, y, w, h),
            "bg": tuple(int(v) for v in bg),
        })
    return out


def _color_of(bg):
    """按底色粗判绿/红/白（只用于日志和兼容）。"""
    r, g, b = int(bg[0]), int(bg[1]), int(bg[2])
    if r > 190 and g > 190 and b > 190:
        return "white"
    if g > r + 20 and g > b + 20:
        return "green"
    if r > g + 20:
        return "red"
    return "gray"


if __name__ == "__main__":
    import glob
    fs = sorted(glob.glob(str(paths.ROOT / "logs/bar_partial_*.png")))[-4:]
    for f in fs:
        im = cv2.imdecode(np.fromfile(f, np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]
        res = read_bar(im)
        print(os.path.basename(f), [(r["text"], r["color"]) if r else None for r in res])
