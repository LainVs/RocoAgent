# -*- coding: utf-8 -*-
"""中文识别：RapidOCR + 封闭词表纠错。

为什么不是普通 OCR：
    游戏里所有文字都是中文，但**词表是封闭的**——精灵名只在 1015 个里选，
    技能名只在 1378 个里选。OCR 只要读个八九不离十，再用词表做最近邻匹配，
    结果就接近 100%。这比"提高 OCR 本身准确率"便宜得多。

用法：
    from ocr_cn import Ocr
    o = Ocr()
    o.read_region(rgb)                    -> [(text, conf), ...]
    o.pet_name(rgb)                       -> (name, score)   词表=精灵名
    o.skill_names(rgb)                    -> [(name, score), ...]  词表=技能名

速度：
    检测+识别整屏约 3-4 秒（太大，不用）；
    只裁名字条（约 260x40）一次 0.2-0.4 秒，够用——只在换精灵/新对局时调用。
"""
from __future__ import annotations

import difflib
import os
import threading

import numpy as np

import rocodata

_engine = None
_lock = threading.Lock()


def _get_engine():
    global _engine
    with _lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
        return _engine


def _to_bgr(rgb):
    """入参统一是 RGB（robot.Screen 的输出），OCR 要 BGR。"""
    if rgb is None or rgb.size == 0:
        return None
    if rgb.ndim == 2:
        return rgb
    return rgb[:, :, ::-1].copy()


def _normalize(s):
    return "".join(ch for ch in (s or "") if not ch.isspace())


class Ocr:
    def __init__(self):
        self._pet_vocab = None
        self._skill_vocab = None

    # ---------------- 词表
    def pet_vocab(self):
        if self._pet_vocab is None:
            names = {sp.get("name") for sp in rocodata.species().values()
                     if sp.get("name") and sp.get("implemented", True)}
            # 太长/带前缀的（首领-恶魔狼）留给完整匹配，短名优先
            self._pet_vocab = sorted(names, key=len)
        return self._pet_vocab

    def skill_vocab(self):
        if self._skill_vocab is None:
            names = {s.get("name") for s in rocodata.skills().values() if s.get("name")}
            self._skill_vocab = sorted(names, key=len)
        return self._skill_vocab

    # ---------------- 识别
    def read_region(self, rgb):
        """整块识别，返回 [(text, conf), ...]。"""
        bgr = _to_bgr(rgb)
        if bgr is None:
            return []
        try:
            res, _ = _get_engine()(bgr)
        except Exception:
            return []
        out = []
        for item in (res or []):
            try:
                out.append((str(item[1]), float(item[2])))
            except (IndexError, TypeError, ValueError):
                continue
        return out

    def read_line(self, rgb):
        """单行快速识别（跳过检测），失败时退回整块识别。"""
        bgr = _to_bgr(rgb)
        if bgr is None:
            return []
        try:
            engine = _get_engine()
            try:
                res, _ = engine(bgr, use_det=False, use_cls=False, use_rec=True)
            except TypeError:
                res, _ = engine(bgr)
        except Exception:
            return []
        out = []
        for item in (res or []):
            try:
                out.append((str(item[1]), float(item[2])))
            except (IndexError, TypeError, ValueError):
                continue
        return out

    # ---------------- 词表匹配
    @staticmethod
    def match(text, vocab, min_ratio=0.4):
        """在封闭词表里找最像的一个。

        评分 = 相似度，但对"完全包含"和"长度接近"加权：
        技能名多为 2-4 字，长度差太多的候选直接扣分。
        """
        t = _normalize(text)
        if not t:
            return None, 0.0
        best, best_score = None, 0.0
        for cand in vocab:
            if not cand:
                continue
            c = _normalize(cand)
            ratio = difflib.SequenceMatcher(None, t, c).ratio()
            if t == c:
                ratio = 1.0
            elif t in c or c in t:
                ratio = max(ratio, 0.85)
            # 长度差异惩罚
            ratio -= 0.02 * abs(len(t) - len(c))
            if ratio > best_score:
                best, best_score = cand, ratio
        return (best, best_score) if best_score >= min_ratio else (None, best_score)

    def pet_name(self, rgb):
        best = (None, 0.0)
        for text, _conf in self.read_line(rgb):
            name, score = self.match(text, self.pet_vocab())
            if score > best[1]:
                best = (name, score)
        return best

    def skill_names(self, rgb, limit=4):
        out = []
        for text, _conf in self.read_region(rgb):
            name, score = self.match(text, self.skill_vocab())
            if name:
                out.append((name, score))
        return out[:limit]


# --------------------------------------------------------------------------
# HUD 字段框（客户区坐标，基于 2560x1600；其他尺寸按比例缩放）
# --------------------------------------------------------------------------
BASE_W, BASE_H = 2560, 1600
# (x, y, w, h) —— 用画框截图逐像素校准过
FIELDS = {
    "name": (120, 52, 220, 36),      # 精灵名（头像右边）
    "level": (250, 52, 90, 36),      # 60级
    "hp": (125, 88, 140, 30),        # 366/366 或 84%
    "energy": (25, 1395, 110, 45),   # 左下 ★10/10
}


def field_box(key, client_w, client_h):
    x, y, w, h = FIELDS[key]
    sx, sy = client_w / float(BASE_W), client_h / float(BASE_H)
    return (int(x * sx), int(y * sy), int(w * sx), int(h * sy))


if __name__ == "__main__":
    import sys
    import time

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import robot

    scr = robot.Screen()
    cw, ch = scr.client_size
    o = Ocr()
    print("client", cw, ch)
    for key in ("name", "level", "hp", "energy"):
        box = field_box(key, cw, ch)
        img = scr.grab_client(box)
        t0 = time.time()
        if key == "name":
            val = o.pet_name(img)
        else:
            val = o.read_line(img)
        dt = time.time() - t0
        print("%-7s %5.2fs  %s" % (key, dt, val))
