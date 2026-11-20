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
_POOL = None


def _engine():
    global _eng
    if _eng is None:
        from rapidocr_onnxruntime import RapidOCR
        _eng = RapidOCR()
    return _eng


def _ocr(rgb, up=2.0, timeout=None):
    """跑一次 OCR。返回 [(text, conf), ...]。

    性能实测（2026-11-21，420x71 的特性横带）：
        up=2.0 -> 5.8~16.5s   up=1.5 -> 13.2s   up=1.0 -> 16.5s
    放大率不是瓶颈，RapidOCR 本身在这台机器上就要 5~25 秒且抖动大。

    timeout：超时后**放弃这次结果**（后台线程继续跑完但结果丢弃）。
    回合窗口只有 15 秒，等不起。
    """
    import cv2
    if rgb is None or rgb.size == 0:
        return []
    if up and abs(up - 1.0) > 0.01:
        big = cv2.resize(rgb, None, fx=up, fy=up,
                         interpolation=cv2.INTER_CUBIC)
    else:
        big = rgb
    bgr = cv2.cvtColor(big, cv2.COLOR_RGB2BGR)

    def _run():
        res, _ = _engine()(bgr)
        return [(str(r[1]), float(r[2])) for r in (res or [])]

    if not timeout:
        return _run()
    # 线程池 + 超时丢弃。注意：不能 kill 线程，但结果会被丢弃，
    # 主流程不再被拖住。
    import concurrent.futures as _cf
    global _POOL
    if _POOL is None:
        _POOL = _cf.ThreadPoolExecutor(max_workers=1)
    fut = _POOL.submit(_run)
    try:
        return fut.result(timeout=timeout)
    except Exception:
        return []


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


def read_ability(screen, vocab=None, dump=None, budget=8.0):
    """返回 {'ability': 名字或 None, 'raw': [...], 'badge': (x,y,w,h)}。

    只读"标签右边那一小段"（名字本体），不读描述行。

    性能实测（2026-11-21，panel_013401）
    -----------------------------------
        整条横带 420x71  up=2.0 -> 5.8~16.5s（含描述，检测器反复迭代）
        名字条   200x55  up=4.0 -> 5.1s  结果正确（'瞳中倒影'）
        名字条   200x55  up=2.0 -> 17.2s 结果截断（'瞳中侄'）

    所以：**只裁名字条 + 放大 4 倍**是最优路径。

    超时保护（budget）
    -----------------
    实测同一台机器上 OCR 耗时在 5~25 秒之间抖动（RapidOCR 的检测器
    对不同图像的迭代次数差异极大）。回合窗口只有 15 秒，一次 OCR 就
    可能吃掉整回合。所以：
      · budget 秒内没读完 -> 直接放弃，返回 None
      · 上层看到 None 会走"特性未知"分支，不影响出招
    """
    import cv2
    import time
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
    t_start = time.time()

    def _left():
        return budget - (time.time() - t_start)

    if badge is not None:
        x, y, w, h = badge
        y0 = max(0, y - 6)
        y1 = min(hh, y + h + 6)
        x0 = min(ww - 1, x + w + 4)
        x1 = min(ww, x0 + 200)
        strip = img[y0:y1, x0:x1]
        if _left() > 2.0:
            lines = _ocr(strip, 4.0, timeout=max(1.0, _left() - 1.0))
            got = _best(lines, vocab)
            if got:
                return {"ability": got, "raw": lines, "badge": badge}
        else:
            lines = []
        # 名字条读不出（长名字被截断）：退回整条横带，但要留够时间
        if _left() > 3.0:
            x1b = min(ww, max(420, x + w + 260))
            band = img[max(0, y - 10):min(hh, y + h + 18), 0:x1b]
            lines2 = _ocr(band, 2.0, timeout=max(1.0, _left() - 1.0))
            got2 = _best(lines2, vocab)
            return {"ability": got2, "raw": lines2 or lines, "badge": badge}
        return {"ability": None, "raw": lines, "badge": badge,
                "timeout": True}
    # 找不到黄标签：特性行永远在面板最后，整条下半部兜底。
    if _left() < 3.0:
        return {"ability": None, "raw": [], "badge": None, "timeout": True}
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
