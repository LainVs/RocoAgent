# -*- coding: utf-8 -*-
"""test_action_button.py —— 底部按钮检测的坐标断言。

为什么需要这个测试
------------------
2026-11-21 用户报"点退出时鼠标一直停在按钮下面一点，机器人卡住"。

根因是 `Screen.abs_point` 的坐标系语义被改错，整体偏移 (13, 58)。
这类 bug 很难从日志发现（日志只打坐标数字，看不出对不对），
所以用**数学断言**兜住：检测点必须落在真实按钮的像素范围内。

跑法（需要游戏在结算页或大厅，底部有按钮）：
    python src\\test_action_button.py

不在那个界面时测试会跳过（找不到按钮就 skip，不算失败）。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


def find_white_button(img):
    """在客户区整图上独立找底部白色按钮，返回 (x, y, w, h) 或 None。

    刻意不复用 ui_probe 的逻辑 —— 那是待测对象，用它验证自己没意义。
    这里用最简单的颜色阈值 + 连通域。
    """
    import cv2
    h, w = img.shape[:2]
    r = img[:, :, 0].astype(np.int16)
    g = img[:, :, 1].astype(np.int16)
    b = img[:, :, 2].astype(np.int16)
    pale = (np.minimum(np.minimum(r, g), b) > 200).astype(np.uint8)
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(pale, 8)
    best = None
    for i in range(1, n):
        bx, by, bw, bh, area = stats[i]
        if area < 8000:
            continue
        if not (150 <= bw <= 450 and 40 <= bh <= 160):
            continue
        if by < h * 0.75:          # 只看底部 25%
            continue
        if abs((bx + bw // 2) - w // 2) > 260:   # 应大致居中
            continue
        if best is None or area > best[4]:
            best = (int(bx), int(by), int(bw), int(bh), int(area))
    return best[:4] if best else None


def test_abs_point_window_coords():
    """abs_point 必须按窗口坐标换算（减 BASE_OFF）。"""
    print("=== abs_point 坐标系语义 ===")
    from winio import Screen
    s = Screen()
    cr = s.client_rect_on_screen()
    if cr is None:
        print("  （找不到窗口，跳过）")
        return
    # 窗口最小化时 rect=(-32000,-32000,314,50)、client_size=(0,0)，
    # 这种状态下坐标换算没有意义，直接跳过（实测踩过）
    if cr[0] <= -10000 or cr[2] <= 0 or cr[3] <= 0:
        print("  （游戏窗口已最小化，跳过 —— 先还原窗口再测）")
        return

    # 已知：窗口坐标 (BASE_OFF_X, BASE_OFF_Y) 对应客户区 (0, 0)
    pt = s.abs_point((s.BASE_OFF_X, s.BASE_OFF_Y, 100, 100), 0, 0)
    check(pt is not None, "abs_point 返回非 None")
    if pt:
        check(pt == (cr[0], cr[1]),
              "box 原点=BASE_OFF 时映射到客户区原点 %s（实际 %s）"
              % ((cr[0], cr[1]), pt))

    # 加偏移
    pt2 = s.abs_point((s.BASE_OFF_X, s.BASE_OFF_Y, 100, 100), 50, 30)
    if pt2:
        check(pt2 == (cr[0] + 50, cr[1] + 30),
              "偏移 (50,30) 映射正确（实际 %s）" % (pt2,))


def test_button_point_inside_real_button():
    """检测点必须落在真实按钮的像素范围内。"""
    print()
    print("=== 检测点是否落在真实按钮内 ===")
    from winio import Screen
    import ui_probe

    s = Screen()
    if s.rect is None:
        print("  （找不到游戏窗口，跳过）")
        return

    img = s.grab_full()
    if img is None:
        print("  （抓图失败，跳过）")
        return

    real = find_white_button(img)
    if real is None:
        print("  （当前画面底部没有按钮，跳过 —— 先让游戏停在结算页/大厅）")
        return

    rx, ry, rw, rh = real
    print("  真实按钮（客户区）: (%d,%d) %dx%d  中心 (%d,%d)"
          % (rx, ry, rw, rh, rx + rw // 2, ry + rh // 2))

    pt = ui_probe.find_action_button(s)
    check(pt is not None, "find_action_button 检测到按钮")
    if pt is None:
        return

    cr = s.client_rect_on_screen()
    cx, cy = pt[0] - cr[0], pt[1] - cr[1]
    print("  检测返回（客户区）: (%d,%d)" % (cx, cy))

    inside_x = rx <= cx <= rx + rw
    inside_y = ry <= cy <= ry + rh
    check(inside_x, "x=%d 在按钮范围 %d..%d 内" % (cx, rx, rx + rw))
    check(inside_y, "y=%d 在按钮范围 %d..%d 内" % (cy, ry, ry + rh))

    # 还应接近中心（不是贴着边缘）
    if inside_x and inside_y:
        dx = abs(cx - (rx + rw // 2))
        dy = abs(cy - (ry + rh // 2))
        check(dx <= 30 and dy <= 30,
              "接近按钮中心（偏移 %d,%d，容差 30）" % (dx, dy))


def test_confirm_button_same_coords():
    """find_confirm_button 与 find_action_button 用同一套 band，坐标应一致或都为 None。"""
    print()
    print("=== 两个按钮函数坐标一致性 ===")
    from winio import Screen
    import ui_probe

    s = Screen()
    if s.rect is None:
        print("  （找不到窗口，跳过）")
        return

    a = ui_probe.find_action_button(s)
    b = ui_probe.find_confirm_button(s)
    print("  action  -> %s" % (a,))
    print("  confirm -> %s" % (b,))
    # 两者共用 ACTION_BTN_BAND 时应该相同；不同也正常（band 不同），
    # 但至少不应该一个有值一个偏得很远。
    if a and b:
        d = max(abs(a[0] - b[0]), abs(a[1] - b[1]))
        check(d <= 60, "两个函数返回值接近（差 %d px）" % d)
    else:
        print("  （至少一个为 None，跳过比较）")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    test_abs_point_window_coords()
    test_button_point_inside_real_button()
    test_confirm_button_same_coords()
    print()
    if FAILS:
        print("FAILED %d:" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS")
