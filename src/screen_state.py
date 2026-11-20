# -*- coding: utf-8 -*-
"""screen_state.py - 分辨率无关的画面状态识别。

设计原则
--------
1. **不用固定颜色阈值**：同一类按钮在不同场景下颜色会变（黄色/白色/灰色），
   但"形状 + 亮度 + 在画面中的相对位置"是稳定的。
2. **不用固定像素坐标**：所有判定框都按**画面比例**给（0~1），
   运行时乘当前客户区尺寸。这样 1280x800 和 2560x1600 都能用。
3. **多信号投票**：单个判据会误判，三个以上同时成立才下结论。

状态枚举
--------
LOBBY     大厅（右下角有金色"开始挑战"）
VS        对局确认页（左青右橙 + 底部"确认首发"）
BATTLE    战斗中（双血条 + 技能栏/倒计时）
RESULT    结算页（"我方战况"六行 + 退出按钮）
SWITCH    强制换人面板（左侧五张卡片）
LOADING   加载/黑屏
UNKNOWN   其它
"""
from __future__ import annotations

import cv2
import numpy as np

LOBBY = "lobby"
VS = "vs"
BATTLE = "battle"
RESULT = "result"
SWITCH = "switch"
LOADING = "loading"
UNKNOWN = "unknown"


def _region(img, rel):
    """按比例取块。rel = (x0, y0, x1, y1)，都是 0~1。"""
    h, w = img.shape[:2]
    x0 = max(0, min(w - 1, int(rel[0] * w)))
    y0 = max(0, min(h - 1, int(rel[1] * h)))
    x1 = max(x0 + 1, min(w, int(rel[2] * w)))
    y1 = max(y0 + 1, min(h, int(rel[3] * h)))
    return img[y0:y1, x0:x1]


def _mean(img, rel):
    r = _region(img, rel)
    return r.reshape(-1, 3).mean(axis=0)


def _bright_ratio(img, rel, lo=190):
    r = _region(img, rel)
    return float((r.min(axis=2) > lo).mean())


def _sat_ratio(img, rel, lo=45):
    r = _region(img, rel).astype(np.int16)
    mx = r.max(axis=2)
    mn = r.min(axis=2)
    return float(((mx - mn) > lo).mean())


def find_big_button(img, band=(0.30, 0.86, 0.70, 0.99),
                    min_w=0.06, max_w=0.22, ar=(2.2, 5.0), min_fill=0.35):
    """在指定比例带里找"大圆角按钮"，**不看颜色**。

    返回 (cx, cy, w, h) 相对比例，或 None。

    判据：
      · 内部亮度高且均匀（按钮都是亮底 + 深字）
      · 宽高比 2.2~5.0（游戏里的按钮都是扁的）
      · 宽度占画面 6%~22%
      · 填充率 > 0.35（实心块，不是边框）
    """
    h, w = img.shape[:2]
    sub = _region(img, band)
    sh, sw = sub.shape[:2]
    if sh < 20 or sw < 50:
        return None

    mn = sub.min(axis=2)
    mx = sub.max(axis=2)
    # 亮且不太花：按钮底色是纯色亮面，场景是花的
    bright = ((mn > 150) & ((mx - mn) < 80)).astype(np.uint8)
    if bright.sum() < 500:
        return None

    # 闭运算把文字孔洞补上，避免按钮被字切成几块
    k = max(3, int(min(sw, sh) * 0.03) | 1)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))

    n, lab, st, _ = cv2.connectedComponentsWithStats(bright, 8)
    best = None
    for i in range(1, n):
        bx, by, bw, bh, area = st[i]
        if bw < sw * min_w or bw > sw * max_w:
            continue
        r = bw / float(bh) if bh else 0
        if not (ar[0] <= r <= ar[1]):
            continue
        fill = area / float(bw * bh)
        if fill < min_fill:
            continue
        score = area * fill
        if best is None or score > best[0]:
            best = (score, bx, by, bw, bh)

    if best is None:
        return None
    _, bx, by, bw, bh = best
    bx0, by0 = band[0], band[1]
    bxw, byh = band[2] - band[0], band[3] - band[1]
    cx = bx0 + (bx + bw / 2.0) / sw * bxw
    cy = by0 + (by + bh / 2.0) / sh * byh
    return (cx, cy, bw / float(sw) * bxw, bh / float(sh) * byh)


def _hp_bars(img):
    """左右两块血条的填充率，用来判断"在战斗中"。

    血条位置按比例：左上 (0.04~0.16, 0.06~0.10)，右上 (0.86~0.97, 0.06~0.10)
    判据是"有彩色填充"，不认具体颜色（绿/黄/红都算）。
    """
    out = []
    for rel in ((0.04, 0.060, 0.17, 0.095), (0.86, 0.060, 0.97, 0.095)):
        r = _region(img, rel).astype(np.int16)
        mx = r.max(axis=2)
        mn = r.min(axis=2)
        filled = ((mx - mn) > 55) & (mx > 110)
        out.append(float(filled.mean()))
    return out


def classify(img):
    """返回 (state, detail)。detail 带各判据的原始值，方便离线调。"""
    if img is None or img.size == 0:
        return UNKNOWN, {}

    h, w = img.shape[:2]
    d = {"size": (w, h)}

    # ---- 加载/黑屏 ----
    d["brightness"] = float(img.mean())
    if d["brightness"] < 18:
        return LOADING, d

    # ---- VS 页：左青右橙 + 底部按钮 ----
    lm = _mean(img, (0.06, 0.20, 0.22, 0.50))
    rm = _mean(img, (0.78, 0.20, 0.94, 0.50))
    d["left_rgb"] = tuple(int(v) for v in lm)
    d["right_rgb"] = tuple(int(v) for v in rm)
    teal = (lm[1] - lm[0] > 25) and (lm[2] - lm[0] > 35)
    orange = (rm[0] - rm[2] > 55)
    d["teal"], d["orange"] = bool(teal), bool(orange)
    if teal and orange:
        btn = find_big_button(img, band=(0.28, 0.84, 0.72, 0.99))
        d["vs_btn"] = btn
        return VS, d

    # ---- 强制换人面板：左侧一列卡片 ----
    cards = _count_light_plates(img, (0.10, 0.24, 0.36, 0.86))
    d["switch_cards"] = cards
    if cards >= 3:
        return SWITCH, d

    # ---- 战斗：双血条 ----
    my_hp, foe_hp = _hp_bars(img)
    d["hp"] = (round(my_hp, 2), round(foe_hp, 2))
    if my_hp > 0.30 and foe_hp > 0.30:
        return BATTLE, d

    # ---- 结算：底部白色"退出"按钮 + 左侧文字行 ----
    btn = find_big_button(img, band=(0.30, 0.86, 0.70, 0.99),
                          ar=(2.5, 6.0), min_fill=0.55)
    d["result_btn"] = btn
    if btn is not None and d["brightness"] > 60:
        return RESULT, d

    # ---- 大厅：右下角金色块 ----
    gold = _gold_ratio(img, (0.72, 0.84, 0.94, 0.97))
    d["gold"] = round(gold, 3)
    if gold > 0.05:
        return LOBBY, d

    # ---- 兜底：底部有按钮就按"可推进"处理 ----
    btn = find_big_button(img, band=(0.28, 0.82, 0.72, 0.99))
    if btn is not None:
        d["fallback_btn"] = btn
        return RESULT, d

    return UNKNOWN, d


def _gold_ratio(img, rel):
    r = _region(img, rel).astype(np.int16)
    rr, gg, bb = r[:, :, 0], r[:, :, 1], r[:, :, 2]
    gold = (rr > 170) & (gg > 120) & (gg < 225) & (bb < 130) & (rr - bb > 70)
    return float(gold.mean())


def _count_light_plates(img, rel):
    """数"浅色圆角小卡"的个数（换人面板 / 技能卡名条）。"""
    r = _region(img, rel)
    if r.size == 0:
        return 0
    mn = r.min(axis=2)
    mx = r.max(axis=2)
    light = (((mx - mn) < 45) & (mn > 175)).astype(np.uint8)
    if light.sum() < 500:
        return 0
    hh = r.shape[0]
    k = max(3, int(hh * 0.01) | 1)
    light = cv2.morphologyEx(light, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(light, 8)
    cnt = 0
    for i in range(1, n):
        bx, by, bw, bh, area = st[i]
        if area < 1500:
            continue
        if bw / float(bh) < 1.2:      # 卡片是横的
            continue
        if bw < r.shape[1] * 0.25:    # 至少占这块区域四分之一宽
            continue
        cnt += 1
    return cnt
