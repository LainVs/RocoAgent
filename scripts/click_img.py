# -*- coding: utf-8 -*-
"""click_img.py —— 从 MCP 截图上的图片坐标直接点击（走开发板）。

坐标链路（务必分清三层）
------------------------
1. **图片坐标**：MCP Screenshot 返回的图片像素。工具会告知
   "Screenshot Coordinate Scale"，本例是 1.777778
   （原图 3072x1920，显示成 1728x1080）。

2. **屏幕坐标** = 图片坐标 * scale
   （MCP 的 Click/Move 用这一层，但我们不用它，改走开发板）

3. **窗口坐标** = 屏幕坐标 - 客户区左上角 + BASE_OFF
   = 屏幕坐标 - client_rect[0:2] + (13, 58)
   这是 `robot.click_rel` 和 config 里 *_REL 用的坐标系。

实测几何（本机）：
    client_rect_on_screen = (608, 83, 2560, 1600)
    BASE_OFF              = (13, 58)
    => 窗口坐标 = 屏幕坐标 - (595, 25)

用法：
    python scripts\\click_img.py 485 228        # 图片坐标点击
    python scripts\\click_img.py 485 228 --move # 只移动不点击
    python scripts\\click_img.py --info         # 打印几何参数
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# MCP 截图的显示缩放（Original 3072x1920 -> 显示 1728x1080）
IMG_SCALE = 3072.0 / 1728.0     # = 1.777778


def geom(scr):
    cr = scr.client_rect_on_screen()
    sx, sy = scr.scale()
    return cr, sx, sy


def img_to_screen(ix, iy, scale=IMG_SCALE):
    """图片坐标 -> 屏幕物理坐标。"""
    return int(round(ix * scale)), int(round(iy * scale))


def screen_to_win(px, py, scr):
    """屏幕坐标 -> 窗口坐标（click_rel 用的那套）。"""
    cr, sx, sy = geom(scr)
    return (int(round((px - cr[0]) / sx + scr.BASE_OFF_X)),
            int(round((py - cr[1]) / sy + scr.BASE_OFF_Y)))


def win_to_screen(wx, wy, scr):
    """窗口坐标 -> 屏幕坐标（验证用）。"""
    cr, sx, sy = geom(scr)
    return (cr[0] + int(round((wx - scr.BASE_OFF_X) * sx)),
            cr[1] + int(round((wy - scr.BASE_OFF_Y) * sy)))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from winio import Screen
    scr = Screen()
    if scr.rect is None:
        print("!! 找不到游戏窗口")
        return 1

    cr, sx, sy = geom(scr)
    print("窗口 rect             = %s" % (scr.rect,))
    print("客户区屏幕位置        = %s" % (cr,))
    print("BASE_OFF              = (%s, %s)" % (scr.BASE_OFF_X, scr.BASE_OFF_Y))
    print("客户区缩放            = (%s, %s)" % (sx, sy))
    print("图片缩放              = %.6f" % IMG_SCALE)

    if "--info" in sys.argv or len(sys.argv) < 3:
        print()
        print("窗口坐标 = 屏幕坐标 - (%d, %d)" % (cr[0] - scr.BASE_OFF_X,
                                                  cr[1] - scr.BASE_OFF_Y))
        print()
        print("用法: python scripts\\click_img.py <图片X> <图片Y> [--move]")
        return 0

    ix, iy = int(sys.argv[1]), int(sys.argv[2])
    px, py = img_to_screen(ix, iy)
    wx, wy = screen_to_win(px, py, scr)
    chk_x, chk_y = win_to_screen(wx, wy, scr)

    print()
    print("图片 (%d, %d)" % (ix, iy))
    print("  -> 屏幕 (%d, %d)" % (px, py))
    print("  -> 窗口 (%d, %d)" % (wx, wy))
    print("  校验屏幕 (%d, %d) %s" % (chk_x, chk_y,
                                      "OK" if (chk_x, chk_y) == (px, py)
                                      else "!! 不一致"))

    if "--move" in sys.argv:
        return 0

    # 走开发板点击
    import asyncio
    import esp_shim as rocs
    sess = rocs.Session(verbose=False)
    loop = asyncio.new_event_loop()
    if not loop.run_until_complete(sess.connect()):
        print("!! BLE 连接失败")
        return 1
    try:
        loop.run_until_complete(sess.click_at(px, py))
        print("已通过开发板点击 (%d, %d)" % (px, py))
        time.sleep(0.6)
    finally:
        try:
            loop.run_until_complete(sess.close())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
