# -*- coding: utf-8 -*-
"""calib_cursor.py —— 用**系统光标实际位置**校准点击换算（只读，不控制）。

## 为什么这样做

用户要求"截图时看鼠标位置自己修正"。但 `Screen.grab_full()` 走 PrintWindow，
**截图里不含系统光标**（实测多次确认），所以没法从图像上直接量。

改为直接读 OS 光标坐标（`GetCursorPos`）—— 这是**只读**调用，不移动鼠标、
不注入事件，不违反"必须走开发板"的约束（那条约束针对的是**控制**）。

## 校准逻辑

点击后光标会停在我们让它去的位置。于是：

    期望物理坐标 P  = cr + shot * scale            （我现在的公式）
    实测光标坐标 C  = GetCursorPos()
    偏差 D = C - P

若 D 恒定 -> 换算里少/多了常量项；若 D 随位置线性变化 -> scale 不对。

## 用法

    & $py scripts\\calib_cursor.py            # 自动跑 3 个点并给出结论
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def get_cursor_pos():
    """读系统光标位置（只读，不控制）。"""
    import ctypes
    from ctypes import wintypes
    pt = wintypes.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
        raise OSError("GetCursorPos failed")
    return int(pt.x), int(pt.y)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from winio import Screen
    import esp_shim as rocs

    scr = Screen()
    if scr.rect is None:
        print("!! 找不到游戏窗口")
        return 1
    cr = scr.client_rect_on_screen()
    scx, scy = scr.scale()
    print("客户区屏幕位置 = %s" % (cr,))
    print("缩放 = (%.4f, %.4f)" % (scx, scy))
    print("BASE_OFF = (%d, %d)\n" % (scr.BASE_OFF_X, scr.BASE_OFF_Y))

    def shot_to_screen(x, y):
        return (cr[0] + int(round(x * scx)), cr[1] + int(round(y * scy)))

    # 三个分布较开的点（截图坐标）：x 小/中/大，都在队伍栏上方的安全空白
    POINTS = [(80, 120), (640, 120), (1200, 120)]

    loop = asyncio.new_event_loop()
    sess = rocs.Session(verbose=False)
    if not loop.run_until_complete(sess.connect()):
        print("!! BLE 连接失败（板子插好？espfast 在跑？）")
        return 1

    rows = []
    try:
        for sx_px, sy_px in POINTS:
            tx, ty = shot_to_screen(sx_px, sy_px)
            # 复刻 esp_shim.click_at 的闭环移动，但**不点击**
            # （click_at 里 3.5 px/单位 的换算依赖板子，这里先量它准不准）
            for _ in range(8):
                cx0, cy0 = get_cursor_pos()
                ddx, ddy = tx - cx0, ty - cy0
                if abs(ddx) <= 2 and abs(ddy) <= 2:
                    break
                ex, ey = round(ddx / 3.5), round(ddy / 3.5)
                if ex == 0 and ey == 0:
                    ex = 1 if ddx > 0 else -1
                    ey = 1 if ddy > 0 else -1
                dur = 100 + min(120, max(abs(ex), abs(ey)) // 4)
                loop.run_until_complete(
                    sess._asend("MOVESLOW:%d,%d,%d" % (ex, ey, dur)))
                time.sleep(0.05 + dur / 1000.0)
            time.sleep(0.3)
            cx, cy = get_cursor_pos()
            dx, dy = cx - tx, cy - ty
            rows.append((sx_px, sy_px, tx, ty, cx, cy, dx, dy))
            print("目标截图(%4d,%3d) -> 期望物理(%4d,%3d)"
                  "  实测光标(%4d,%3d)  偏差(%+d,%+d)"
                  % (sx_px, sy_px, tx, ty, cx, cy, dx, dy))
    finally:
        try:
            loop.run_until_complete(sess.close())
        except Exception:
            pass

    print("\n=== 结论 ===")
    if not rows:
        return 1
    dxs = [r[6] for r in rows]
    dys = [r[7] for r in rows]
    print("x 偏差范围: %+d ~ %+d    y 偏差范围: %+d ~ %+d"
          % (min(dxs), max(dxs), min(dys), max(dys)))

    if max(dxs) - min(dxs) <= 2 and max(dys) - min(dys) <= 2:
        ox, oy = dxs[0], dys[0]
        print("\n=> 偏差**恒定** (%+d, %+d) 物理像素" % (ox, oy))
        if ox == 0 and oy == 0:
            print("   **当前换算完全正确**，之前的点偏不是公式问题。")
        else:
            print("   说明换算需要加常量修正：")
            print("   screen = cr + shot*scale + (%d, %d)" % (ox, oy))
            print("   对应截图坐标修正: (%+.1f, %+.1f) px"
                  % (ox / scx, oy / scy))
    else:
        print("\n=> 偏差随位置变化 -> scale 有问题，需要用两点反解")

    print("\n=== 逐点明细 ===")
    for r in rows:
        print("  shot(%4d,%3d) 期望(%4d,%3d) 实测(%4d,%3d) 偏差(%+d,%+d)"
              % r)
    return 0


if __name__ == "__main__":
    sys.exit(main())