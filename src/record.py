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


def _panel_open(img):
    """面板打开时那一行是白底黑字；没打开就是场景画面。"""
    if img is None or img.size == 0:
        return False
    mn = img.min(axis=2)
    return float((mn > 200).mean()) > 0.35


def read(screen, click, log=None):
    """点开面板读 (wins, losses)，读完关掉。失败返回 None。

    click 是 (x, y) -> bool 的硬件点击（必须走开发板，系统鼠标游戏不认）。
    不做"面板是否已经打开"的判断：那个探针在面板动画/半透明状态下会误判，
    实测导致直接返回 None。这里改成"点了就读，读不到再点一次"。
    """
    import cv2
    import ocr_cn

    got = None
    # 坐标是客户区基准分辨率，必须按当前缩放换算，否则换分辨率就点空
    px, py = screen.phys_client(*OPEN_CLICK)
    cpx, cpy = screen.phys_client(*CLOSE_CLICK)
    try:
        for attempt in range(3):
            click(px, py)
            # 面板有入场动画：实测 0.35s×4 常常还是空白（日志里大量
            # "record ocr[1]: " 空字符串）。这里改成"最多等 2.5 秒、
            # 一看到白底就多等 0.4 秒让数字画完再读"。
            img = None
            deadline = time.time() + 2.5
            while time.time() < deadline:
                time.sleep(0.25)
                img = screen.grab_client(RECORD_BOX)
                if _panel_open(img):
                    # 白底出现了，但数字可能还没画完 —— 再给它一点时间
                    time.sleep(0.4)
                    img = screen.grab_client(RECORD_BOX)
                    break
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
            # 读不到就把画面存下来，第二天能看出是没打开还是读错区域
            try:
                cv2.imwrite(os.path.join(ROOT, "logs",
                                         "record_fail_%s.png"
                                         % time.strftime("%H%M%S")),
                            img[:, :, ::-1])
            except Exception:
                pass
    finally:
        click(cpx, cpy)
    return got


def load_state():
    """读上次的战绩基线，返回 (wins, losses) 或 None。

    踩过的坑（2026-11-21 09:30，用户报"对局记录没有输赢"）
    --------------------------------------------------
    原来这里是：

        return json.load(f)          # 返回 {"wins":113,"losses":106,"at":...}

    但 diff() 期望的是 tuple：

        if after[0] > before[0] ...  # <== KeyError: 0

    dict 没有整数键 0/1，所以每次调用 diff() 都抛 KeyError，
    被外层 except 吞掉，results.jsonl 永远写不进去（全是 flat）。

    日志里能看到 record_state.json 停在 2026-11-19（基线建立那天），
    之后再没更新过 —— 因为 save_state 只在 diff 成功后才调用。

    现在统一返回 tuple，与 save_state 的参数形式对齐。
    """
    import json
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        # 兼容两种格式：dict（现在）和 list（更早的版本）
        if isinstance(d, dict):
            w, l = d.get("wins"), d.get("losses")
        elif isinstance(d, (list, tuple)) and len(d) >= 2:
            w, l = d[0], d[1]
        else:
            return None
        if w is None or l is None:
            return None
        return (int(w), int(l))
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
    """(wins, losses) 两次读数 -> 'win' / 'loss' / None。

    严格条件：赢和输只有一项变化，另一项必须完全不变。

    为什么必须严格（2026-11-21）：一局只该改变一个数字。
    如果两个都变，说明跨了多局（机器人卡住没读）或者读数错乱，
    这种情况宁可不判（返回 None），也不能记错胜负 —— 错的战绩
    会污染胜率统计，比没有更糟。
    """
    if not before or not after:
        return None
    # 兼容 dict 传入（防止调用方直接传 load_state 的原始结果）
    if isinstance(before, dict):
        before = (before.get("wins"), before.get("losses"))
    if isinstance(after, dict):
        after = (after.get("wins"), after.get("losses"))
    if before[0] is None or before[1] is None:
        return None
    if after[0] is None or after[1] is None:
        return None
    if after[0] > before[0] and after[1] == before[1]:
        return "win"
    if after[1] > before[1] and after[0] == before[0]:
        return "loss"
    return None
