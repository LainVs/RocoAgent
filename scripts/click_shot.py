# -*- coding: utf-8 -*-
"""click_shot.py —— 用**截图坐标**点击（走开发板 BLE）。

## 三层坐标系（这是本脚本存在的全部理由）

之前用 game_cli.py click 一直点偏，根因是**把截图坐标当成窗口坐标**，
多减了一次 BASE_OFF(13,58)，于是每次偏 (10, 56) 像素。

    A. 截图坐标   `Screen.grab_full()` 返回 2560x1600 图像上的像素
                  它 == **客户区基准坐标**（不含 BASE_OFF）
    B. 客户区物理 screen = client_rect + A * scale
    C. 窗口坐标   = A + BASE_OFF   （config 的 *_REL / click_rel 用这套）

`game_cli.py click` 收的是 C（会减 BASE_OFF）；而我们从截图上量出来的是 A。
两者差 (13,58)，在 0.8/0.96 缩放下就是 (10,56) 像素 —— 全部点偏。

## 用法

    & $py scripts\\click_shot.py 215 235            # 截图坐标，直接点
    & $py scripts\\click_shot.py 215 235 --mark     # 只把点画在截图上，不点
    & $py scripts\\click_shot.py 215 235 --verify   # 点完再截图对比（高亮是否移动）
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

OUT_DIR = os.path.join(ROOT, "logs", "shots")


def shot_to_screen(scr, sx_px, sy_px):
    """截图坐标 -> 屏幕物理坐标。

    ⚠ 截图坐标 == 客户区基准坐标，**不减 BASE_OFF**。
    基准 (2560x1600) 与当前客户区 (2048x1536) 之间用 scale 换算。
    """
    cr = scr.client_rect_on_screen()
    if cr is None:
        raise RuntimeError("客户区位置取不到")
    scx, scy = scr.scale()
    return (cr[0] + int(round(sx_px * scx)),
            cr[1] + int(round(sy_px * scy)))


def screen_to_shot(scr, px, py):
    """屏幕物理坐标 -> 截图坐标（校验用）。

    ⚠ 不能用 int() 截断：正向 shot_to_screen 用了 round，反向若截断，
    0.8 缩放下会出现 1px 往返误差（实测 (397,250) -> (398,250)）。
    对称的 round 才能保证 round(round(x*s)/s) 回到 x。
    """
    cr = scr.client_rect_on_screen()
    scx, scy = scr.scale()
    return (int(round((px - cr[0]) / scx)),
            int(round((py - cr[1]) / scy)))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from winio import Screen
    import cv2

    if len(sys.argv) < 3:
        print(__doc__)
        return 0

    sx_px, sy_px = int(sys.argv[1]), int(sys.argv[2])
    mark_only = "--mark" in sys.argv
    verify = "--verify" in sys.argv

    scr = Screen()
    if scr.rect is None:
        print("!! 找不到游戏窗口")
        return 1

    cr = scr.client_rect_on_screen()
    scx, scy = scr.scale()
    ax, ay = shot_to_screen(scr, sx_px, sy_px)
    bx, by = screen_to_shot(scr, ax, ay)

    print("客户区屏幕位置 = %s" % (cr,))
    print("缩放           = (%.2f, %.2f)" % (scx, scy))
    print("BASE_OFF       = (%d, %d)  ← **不再参与换算**"
          % (scr.BASE_OFF_X, scr.BASE_OFF_Y))
    print("截图坐标 (%d, %d)" % (sx_px, sy_px))
    print("  -> 屏幕 (%d, %d)" % (ax, ay))
    print("  校验回截图 (%d, %d)  %s"
          % (bx, by, "OK" if (bx, by) == (sx_px, sy_px) else "!! 不一致"))

    if mark_only:
        img = scr.grab_full().copy()
        cv2.drawMarker(img, (sx_px, sy_px), (0, 0, 255),
                       cv2.MARKER_CROSS, 40, 2)
        cv2.circle(img, (sx_px, sy_px), 28, (0, 255, 0), 2)
        os.makedirs(OUT_DIR, exist_ok=True)
        p = os.path.join(OUT_DIR, "mark_%d_%d.png" % (sx_px, sy_px))
        cv2.imwrite(p, img)
        print("\n标注图 -> %s" % p)
        return 0

    before = scr.grab_full() if verify else None

    import esp_shim as rocs
    loop = asyncio.new_event_loop()
    sess = rocs.Session(verbose=False)
    if not loop.run_until_complete(sess.connect()):
        print("!! BLE 连接失败（板子插好？espfast 在跑？）")
        return 1
    try:
        loop.run_until_complete(sess.click_at(ax, ay))
        print("\n已通过开发板点击 (%d, %d)" % (ax, ay))
        time.sleep(0.9)
    finally:
        try:
            loop.run_until_complete(sess.close())
        except Exception:
            pass

    if verify:
        after = scr.grab_full()
        band = (100, 160, 1200, 170)
        x0, y0, w, h = band
        d = cv2.absdiff(before[y0:y0 + h, x0:x0 + w],
                        after[y0:y0 + h, x0:x0 + w])
        changed = int((d.max(axis=2) > 30).sum())
        print("队伍栏变化像素: %d  %s"
              % (changed, "OK（高亮有移动）" if changed > 500
                 else "!! 几乎无变化（可能没点中）"))
        os.makedirs(OUT_DIR, exist_ok=True)
        p = os.path.join(OUT_DIR, "after_click.png")
        cv2.imwrite(p, after)
        print("事后整图 -> %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())