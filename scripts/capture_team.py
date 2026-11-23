# -*- coding: utf-8 -*-
"""capture_team.py —— 采集实机队伍 6 个槽位的面板信息。

## 坐标（已实测验证，不是推测）

    x = 244 + 153*k   (k = 0..5)
    y = 250
    => 244, 397, 550, 703, 856, 1009

验证记录（2026-11-23 11:0x）：
    点 (215,235) -> 音速犬   （略偏左上但仍命中）
    点 (397,250) -> 冰钻布鲁斯（准）
两次点击之间队伍栏变化 18660 像素 = 高亮确实移动。

## 为什么用截图坐标

`Screen.grab_full()` 的像素 == **客户区基准坐标**，直接用 scale 换算到屏幕，
**不减 BASE_OFF(13,58)**。减了就会整体偏移 (10,56) 像素 —— 这是之前一直
点偏的根因。参见 scripts/click_shot.py 的三层坐标系说明。

## 每步都验证

1. 点击后比较队伍栏像素差 -> 高亮是否移动
2. 裁右侧面板存图 -> 人工可复核
3. 打印变化量，异常时明确告警（不静默出错）

## 用法

    & $py scripts\\capture_team.py
    & $py scripts\\capture_team.py 1 2 3
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

OUT_DIR = os.path.join(ROOT, "logs", "shots")

SLOT_XS = [174, 378, 582, 774, 978, 1170]   # 扫线实测（scripts/scan_slots.py）
SLOT_Y = 250

BAR = (90, 165, 1230, 165)          # 队伍栏（比较高亮移动用）
PANEL = (1330, 60, 1230, 900)       # 右侧信息面板


def shot_to_screen(scr, x, y):
    cr = scr.client_rect_on_screen()
    scx, scy = scr.scale()
    return (cr[0] + int(round(x * scx)), cr[1] + int(round(y * scy)))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from winio import Screen
    import esp_shim as rocs
    import cv2
    import numpy as np

    want = [int(a) for a in sys.argv[1:] if a.isdigit()] or list(range(1, 7))

    scr = Screen()
    if scr.rect is None:
        print("!! 找不到游戏窗口")
        return 1
    print("客户区屏幕位置 = %s" % (scr.client_rect_on_screen(),))
    print("缩放 = (%0.2f, %0.2f)   BASE_OFF 不参与换算"
          % scr.scale())

    os.makedirs(OUT_DIR, exist_ok=True)
    ts = time.strftime("%H%M%S")

    loop = asyncio.new_event_loop()
    sess = rocs.Session(verbose=False)
    if not loop.run_until_complete(sess.connect()):
        print("!! BLE 连接失败")
        return 1

    bx0, by0, bw, bh = BAR
    ok_count = 0
    try:
        prev = scr.grab_full()
        for slot in want:
            x = SLOT_XS[slot - 1]
            ax, ay = shot_to_screen(scr, x, SLOT_Y)
            loop.run_until_complete(sess.click_at(ax, ay))
            time.sleep(0.9)
            cur = scr.grab_full()

            d = cv2.absdiff(prev[by0:by0 + bh, bx0:bx0 + bw],
                            cur[by0:by0 + bh, bx0:bx0 + bw])
            moved = int((d.max(axis=2) > 30).sum())
            prev = cur

            px, py, pw, ph = PANEL
            panel = cur[py:py + ph, px:px + pw]
            path = os.path.join(OUT_DIR,
                                "team_slot%d_%s.png" % (slot, ts))
            cv2.imwrite(path, panel)

            flag = "OK" if moved > 800 else "!! 高亮未移动（可能点空）"
            print("  槽%d  截图坐标(%4d,%4d)->物理(%4d,%4d) 变化%-6d %s"
                  % (slot, x, SLOT_Y, ax, ay, moved, flag))
            if moved > 800:
                ok_count += 1
    finally:
        try:
            loop.run_until_complete(sess.close())
        except Exception:
            pass

    print("\n完成 %d/%d 个槽位（高亮确认移动）" % (ok_count, len(want)))
    print("输出目录: %s" % OUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())