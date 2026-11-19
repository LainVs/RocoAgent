# -*- coding: utf-8 -*-
"""从对面特性面板里读"特性名"。

为什么不再按固定坐标切：
    面板布局有两种（有的多一栏"速度对比"），整体上下差 100+ 像素。
    但"特性"那个**黄色小标签**是唯一的黄色块，位置稳定相对它自己。
    所以：先找黄标签，再读它右边那一条。

    另外名字不能当身份键（玩家可以起昵称），特性才是不变的 ——
    读到特性后走 features.name -> feature_pets -> species.id 反查。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PANEL_BOX = (930, 300, 730, 1080)   # 整块面板（客户区，宁可给大一点）

_eng = None


def _engine():
    global _eng
    if _eng is None:
        from rapidocr_onnxruntime import RapidOCR
        _eng = RapidOCR()
    return _eng


def _ocr(rgb, up=2.0):
    import cv2
    if rgb is None or rgb.size == 0:
        return []
    big = cv2.resize(rgb, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
    res, _ = _engine()(cv2.cvtColor(big, cv2.COLOR_RGB2BGR))
    return [(str(r[1]), float(r[2])) for r in (res or [])]


def find_badge(img):
    """找黄色"特性"标签，返回 (x, y, w, h) 或 None。"""
    import cv2
    import numpy as np
    if img is None or img.size == 0:
        return None
    r = img[:, :, 0].astype(np.int16)
    g = img[:, :, 1].astype(np.int16)
    b = img[:, :, 2].astype(np.int16)
    m = ((r > 175) & (g > 140) & (b < 120) & (r - b > 70) & (g - b > 50)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _lab, st, _cen = cv2.connectedComponentsWithStats(m, 8)
    best = None
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if area < 400 or h < 18 or h > 70 or w < 30 or w > 160:
            continue
        if not (1.2 <= w / float(h) <= 6.0):
            continue
        if best is None or area > best[4]:
            best = (int(x), int(y), int(w), int(h), int(area))
    return best[:4] if best else None


def read_ability(screen, vocab=None, dump=None):
    """返回 {'ability': 名字或 None, 'raw': [...], 'badge': (x,y,w,h)}。"""
    import cv2
    img = screen.grab_client(PANEL_BOX)
    if img is None or img.size == 0:
        return None
    if dump:
        try:
            cv2.imwrite(dump, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        except Exception:
            pass
    badge = find_badge(img)
    if badge is None:
        # 找不到黄标签：退回整块 OCR，交给词表筛
        lines = _ocr(img, 1.5)
        return {"ability": _best(lines, vocab), "raw": lines, "badge": None}
    x, y, w, h = badge
    # 标签右边一条：特性名就在那里
    y0 = max(0, y - 12)
    y1 = min(img.shape[0], y + h + 14)
    x0 = min(img.shape[1] - 1, x + w + 6)
    x1 = min(img.shape[1], x + w + 520)
    strip = img[y0:y1, x0:x1]
    lines = _ocr(strip, 2.5)
    return {"ability": _best(lines, vocab), "raw": lines, "badge": badge}


def _best(lines, vocab):
    if not lines:
        return None
    text = " ".join(t for t, _c in lines).strip()
    if not text:
        return None
    if vocab:
        import ocr_cn
        cand, score = ocr_cn.Ocr.match(text, vocab)
        if cand and score >= 0.60:
            return cand
    return None


def feature_vocab(db_path=None):
    import sqlite3
    from portrait_db import DB_PATH
    db = sqlite3.connect(db_path or DB_PATH)
    try:
        return [r[0] for r in
                db.execute("SELECT DISTINCT name FROM features WHERE name IS NOT NULL")]
    finally:
        db.close()


if __name__ == "__main__":
    import robot
    scr = robot.Screen()
    v = feature_vocab()
    out = read_ability(scr, v, dump=os.path.join(robot.LOG_DIR, "ability_probe.png"))
    print("vocab", len(v))
    print(out)
