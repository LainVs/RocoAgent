# -*- coding: utf-8 -*-
"""hud.py —— 战斗 HUD 上的两个读数：能量与技能播报。

两者都用「封闭词表 / 固定格式 + OCR」：
    能量   ★N/10      —— 只有数字和斜杠，正则即可
    播报   XX 使出了 YY！ —— YY 是技能名，词表 1137 条，最近邻纠错

OCR 单次 1-2 秒，所以：
    能量：每回合读一次（决策时），结果缓存
    播报：只在播报区域变亮且换了内容时读一次
"""
from __future__ import annotations

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ENERGY_BOX = (85, 1450, 140, 50)     # 左下角 ★N/10（客户区）
CAST_BOX = (1400, 140, 1160, 150)    # 上方播报条（客户区）

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
    return [str(r[1]) for r in (res or [])]


def read_energy(screen):
    """读 ★N/10 -> (cur, max) 或 None。"""
    img = screen.grab_client(ENERGY_BOX)
    texts = _ocr(img, 2.5)
    joined = " ".join(texts)
    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", joined)
    if not m:
        # 只认出数字（斜杠丢了）：取第一个 1-2 位数当当前值
        m2 = re.search(r"\b(\d{1,2})\b", joined)
        if not m2:
            return None
        return int(m2.group(1)), None
    return int(m.group(1)), int(m.group(2))


_cast_last = (None, 0.0)


def read_cast(screen, vocab=None):
    """读技能播报 -> 技能名（封闭词表纠正）或 None。

    只有播报条亮起才读；读完记住，避免同一句反复 OCR。
    """
    global _cast_last
    img = screen.grab_client(CAST_BOX)
    if img is None or img.size == 0:
        return None
    if float(img.max(axis=2).mean()) < 110:
        return None
    now = time.time()
    if now - _cast_last[1] < 1.2:
        return _cast_last[0]
    texts = _ocr(img, 1.5)
    joined = " ".join(texts).strip()
    if not joined:
        return None
    name = joined
    if vocab:
        import ocr_cn
        cand, score = ocr_cn.Ocr.match(joined, vocab)
        if cand and score > 0.55:
            name = cand
    _cast_last = (name, now)
    return name


def skill_vocab(db_path=None):
    import sqlite3
    from portrait_db import DB_PATH
    db = sqlite3.connect(db_path or DB_PATH)
    try:
        return [r[0] for r in
                db.execute("SELECT DISTINCT name FROM skills WHERE name IS NOT NULL")]
    finally:
        db.close()

_last_text = (None, 0.0)


def read_cast_text(screen):
    """播报条的原始文本（缓存 1.2 秒）。分流交给调用方。"""
    global _last_text
    import time
    if time.time() - _last_text[1] < 1.2:
        return _last_text[0]
    img = screen.grab_client(CAST_BOX)
    if img is None or img.size == 0:
        return None
    if float(img.max(axis=2).mean()) < 110:
        return None
    texts = _ocr(img, 1.5)
    text = " ".join(texts).strip()
    _last_text = (text or None, time.time())
    return text or None
