# -*- coding: utf-8 -*-
"""历史战绩：游戏自己记的累计胜负，是唯一不依赖推断的胜负来源。

面板位置：大厅左侧那个带"L"角标的圆形图标（客户区 191,943），
点开是一张"历史战绩"卡片，中间一行：

    胜利 89  失败 76

只要在每场前后各读一次，比较这两个数字：

    胜利 +1 -> 这场赢了
    失败 +1 -> 这场输了
   都没变 -> 这场没打完 / 读错了

比"星数差""段位名"都可靠：星级会一次加 1 也会加 2、升段还会清零，
而这组计数是单调累加的。
"""
from __future__ import annotations

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "logs", "record_state.json")

OPEN_CLICK = (191, 943)          # 客户区：历史战绩按钮
CLOSE_CLICK = (2012, 422)        # 客户区：面板右上角 X
RECORD_BOX = (900, 470, 800, 90)  # 客户区："胜利 N 失败 M" 那一行


def read(screen, click, log=None):
    """点开面板读 (wins, losses)，读完关掉。失败返回 None。

    click 是 (x, y) -> bool 的硬件点击（必须走开发板，系统鼠标游戏不认）。
    不做"面板是否已经打开"的判断：那个探针在面板动画/半透明状态下会误判，
    实测导致直接返回 None。这里改成"点了就读，读不到再点一次"。
    """
    import cv2
    import ocr_cn

    cx, cy = screen.client_rect_on_screen()[:2]
    got = None
    try:
        for attempt in range(2):
            click(cx + OPEN_CLICK[0], cy + OPEN_CLICK[1])
            time.sleep(0.9)
            img = screen.grab_client(RECORD_BOX)
            if img is None or img.size == 0:
                continue
            big = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            lines = ocr_cn.Ocr().read_region(big)
            text = " ".join(t for t, _c in lines)
            m = re.search(r"(\d+)\D{0,6}?(\d+)", text)
            if log:
                log("record ocr[%d]: %s" % (attempt + 1, text.strip()[:40]))
            if m:
                got = (int(m.group(1)), int(m.group(2)))
                break
    finally:
        click(cx + CLOSE_CLICK[0], cy + CLOSE_CLICK[1])
    return got


def load_state():
    import json
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_state(wins, losses):
    import json
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump({"wins": wins, "losses": losses,
                       "at": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
    except OSError:
        pass


def diff(before, after):
    """(wins, losses) 两次读数 -> 'win' / 'loss' / None。"""
    if not before or not after:
        return None
    if after[0] > before[0] and after[1] == before[1]:
        return "win"
    if after[1] > before[1] and after[0] == before[0]:
        return "loss"
    return None
