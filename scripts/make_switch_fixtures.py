# -*- coding: utf-8 -*-
"""从真实整帧截图里裁出「换人面板判据」用到的两块区域，拼成回归样张。

为什么不是直接入库整帧
----------------------
整帧 2560x1600 PNG 一张 3.3 MB，四张就 13 MB。但 `find_switch_rows`
只读两块区域：

    PARTY_BOX = (250, 540, 660, 780)    队伍列表
    CNT_ROI   = (1120, 55, 340, 150)    回合倒计时大字

其余部分是纯背景，对判据毫无作用。所以样张 = 全黑画布 + 这两块原样
贴回原位。判据读到的字节与整帧完全一致（`FakeScreen.grab_s` 的裁剪
逻辑相同），体积降到几百 KB。

副作用（是好事）：样张与 PARTY_BOX / CNT_ROI 的坐标绑定。谁要是挪了
这两个框，样张就对不上、测试立刻红 —— 这正是这类像素布局测试需要的。

用法：
    & $py scripts/make_switch_fixtures.py
    & $py src/test_switch_panel.py
"""
import io
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
import ui_probe  # noqa: E402

BASE_OFF = (13, 58)          # winio.Screen.BASE_OFF
BASE_W, BASE_H = 2560, 1600

OUT_DIR = os.path.join("data", "fixtures", "switch")

# (源帧, 输出名)
FRAMES = [
    ("logs/probe/switch_now.png", "panel_two_fainted.png"),
    ("logs/probe/battle_now.png", "panel_one_fainted.png"),
    ("logs/probe/live5.png", "normal_turn_with_list.png"),
    ("logs/probe/live7.png", "normal_turn_countdown.png"),
]


def client_span(box):
    """窗口坐标 box -> 客户区像素区间 (sx0, sy0, ex0, ey0)，已裁剪到画面内。"""
    x, y, w, h = box
    cx, cy = x - BASE_OFF[0], y - BASE_OFF[1]
    return (max(0, cx), max(0, cy),
            min(BASE_W, cx + w), min(BASE_H, cy + h))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    boxes = [ui_probe.PARTY_BOX, ui_probe.CNT_ROI]
    for src, name in FRAMES:
        if not os.path.exists(src):
            print("SKIP %-28s 源帧不存在: %s" % (name, src))
            continue
        im = cv2.imdecode(np.fromfile(src, dtype=np.uint8), cv2.IMREAD_COLOR)
        if im.shape[:2] != (BASE_H, BASE_W):
            im = cv2.resize(im, (BASE_W, BASE_H), interpolation=cv2.INTER_AREA)
        canvas = np.zeros((BASE_H, BASE_W, 3), np.uint8)
        for box in boxes:
            sx0, sy0, ex0, ey0 = client_span(box)
            canvas[sy0:ey0, sx0:ex0] = im[sy0:ey0, sx0:ex0]
        out = os.path.join(OUT_DIR, name)
        cv2.imwrite(out, canvas, [cv2.IMWRITE_PNG_COMPRESSION, 9])
        print("%-28s %7.1f KB" % (name, os.path.getsize(out) / 1024.0))


if __name__ == "__main__":
    main()
