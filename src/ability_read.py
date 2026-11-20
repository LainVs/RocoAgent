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
    """找黄色"特性"标签，返回 (x, y, w, h) 或 None。

    面板从上到下的顺序是固定的：名字 → 属性徽章 → "受伤提升/降低"属性块 → 特性。
    所以只在下半部找 —— 否则上方的**属性徽章**（也是暖色小方块）会被当成标签，
    返回一个 y≈180 的错位置，后面的 OCR 落空（实测就是这样读不到"上锁"）。
    """
    import cv2
    import numpy as np
    if img is None or img.size == 0:
        return None
    h_all = img.shape[0]
    y_start = int(h_all * 0.55)          # 只搜下半部
    sub = img[y_start:]
    r = sub[:, :, 0].astype(np.int16)
    g = sub[:, :, 1].astype(np.int16)
    b = sub[:, :, 2].astype(np.int16)
    m = ((r > 175) & (g > 140) & (b < 120) & (r - b > 70) & (g - b > 50)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _lab, st, _cen = cv2.connectedComponentsWithStats(m, 8)
    best = None
    for i in range(1, n):
        x, y, w, h, area = st[i]
        # 标签可能和下面的六边形特性图标连成一块（实测 h≈130），
        # 所以放宽高度上限，但**取顶部 45px 当作标签**。
        if area < 400 or h < 18 or h > 170 or w < 30 or w > 160:
            continue
        if not (1.2 <= w / float(h) <= 6.0) and not (w >= 55 and h > 70):
            continue
        score = (area, -x)
        if best is None or score > best[0]:
            best = (score, int(x), int(y) + y_start, int(w),
                    int(min(h, 45)))
    return best[1:5] if best else None


def read_ability(screen, vocab=None, dump=None):
    """返回 {'ability': 名字或 None, 'raw': [...], 'badge': (x,y,w,h)}。

    只做**一次** OCR。裁剪框是"黄标签那一行 + 标签左margin"的横带
    (x 0..~420, y 标签-30..标签+50)，实测这条带子对两种面板布局都能一次读出：

        panel_210501  badge=(35,754) -> ['上锁', '特性', '敌方本回合使用的技能…']
        panel_211304  badge=(30,702) -> ['恶魔的晚宴', '特性', '主动击败敌方精灵时…']

    之前是"标签右边 110/260px 的窄条"，窄条会随布局切掉字头（'恶魔的明'）
    或者整条返回空（514px 宽 → []），于是退回整块面板 OCR，一次要 6~10 秒，
    两次 OCR 就把下一手技能吃掉了（实测 21:13 卡了 25 秒）。
    """
    import cv2
    img = screen.grab_client(PANEL_BOX)
    if img is None or img.size == 0:
        return None
    if dump:
        try:
            cv2.imwrite(dump, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        except Exception:
            pass
    hh, ww = img.shape[:2]
    badge = find_badge(img)
    if badge is not None:
        x, y, w, h = badge
        # 横带只取"标签那一行"：标签顶部 -10 到 底部 +18。
        # 实测（01:22 的嗜波螺面板）原来用 y-30..y+h+50 会把下面的
        # 技能行（'热砂 对敌方精灵造成魔法伤害'）也带进来，
        # 结果把技能名当成了特性。
        y0 = max(0, y - 10)
        y1 = min(hh, y + h + 18)
        # 名字在标签右边长一点：长特性名（'恶魔的晚宴'）要 200px 以上，
        # 面板整体右移时再从 x 推算，别写死。
        x1 = min(ww, max(420, x + w + 260))
        band = img[y0:y1, 0:x1]
    else:
        # 找不到黄标签：特性行永远在面板最后，整条下半部兜底。
        band = img[int(hh * 0.66):int(hh * 0.92), :]
    lines = _ocr(band, 2.0)
    return {"ability": _best(lines, vocab), "raw": lines, "badge": badge}


def _best(lines, vocab):
    """逐行匹配，取分数最高的一行。

    以前是把所有行拼成一段再匹配，描述里的字会把特性名淹掉；现在描述只用于
    落盘补词表，不参与匹配。
    """
    if not lines:
        return None
    if not vocab:
        return None
    import ocr_cn
    best, best_s = None, 0.0
    for text, _conf in lines:
        text = text.strip()
        if not text:
            continue
        cand, score = ocr_cn.Ocr.match(text, vocab)
        if cand and score > best_s:
            best, best_s = cand, score
    return best if best_s >= 0.60 else None


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
