# -*- coding: utf-8 -*-
"""test_standalone.py —— 独立计算器在「游戏没开」时必须优雅退化成空态。

为什么专门测这个
----------------
2026-11-21 踩到：`winio.Screen` 在找不到游戏窗口时，`refresh()` 提前 return，
于是新构造的对象**没有 `client_size` 属性**。而调用方（standalone /
foe_ident / portraits.box_for）直接读 `screen.client_size`：

    AttributeError: 'Screen' object has no attribute 'client_size'

这个错误只在「游戏没开」时触发 —— 也就是最常见的情况（机器人挂了、
用户没开游戏、修 bug 时）。所以必须用测试钉死。

跑法：
    python src\\test_standalone.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import winio


class _FakeScreen:
    """模拟「游戏没开」的 Screen：矩形和客户区都是空。"""

    def __init__(self):
        self.rect = None
        self.hwnd = None
        self.client_size = (0, 0)
        self.client_off = (0, 0)

    def grab_client(self, box):
        return None

    def grab_frame(self):
        return None, (1.0, 1.0)


def main():
    bad = 0

    def check(ok, msg):
        nonlocal bad
        print("  %s  %s" % ("ok  " if ok else "FAIL", msg))
        if not ok:
            bad += 1

    print("=== Screen 属性契约 ===")
    scr = _FakeScreen()
    check(hasattr(scr, "client_size"), "client_size 必须存在（游戏没开时是 (0,0)）")
    check(scr.client_size == (0, 0), "(0,0) 而不是缺失")
    check(hasattr(scr, "client_off"), "client_off 必须存在")
    check(scr.grab_client((0, 0, 10, 10)) is None, "空窗口时 grab 返回 None")

    print("\n=== portraits 的框函数不能崩 ===")
    import portraits
    try:
        box = portraits.foe_box_for(*scr.client_size, margin=10)
        ok = isinstance(box, tuple) and len(box) == 4
    except Exception as e:
        ok = False
        print("       ", type(e).__name__, e)
    check(ok, "foe_box_for(0,0) 返回 4 元组")

    try:
        box = portraits.box_for(*scr.client_size, margin=10)
        ok = isinstance(box, tuple) and len(box) == 4
    except Exception as e:
        ok = False
        print("       ", type(e).__name__, e)
    check(ok, "box_for(0,0) 返回 4 元组")

    print("\n=== foe_ident 在空屏上不能崩 ===")
    import foe_ident
    try:
        r = foe_ident.identify(scr, side="foe")
        ok = isinstance(r, dict) and r.get("name") is None
    except Exception as e:
        ok = False
        print("       ", type(e).__name__, e)
    check(ok, "identify(空屏) 返回 name=None 而不是抛异常")

    print("\n=== hud_ident 在空屏上不能崩 ===")
    import hud_ident
    try:
        t = hud_ident.read_types(scr, "foe")
        ok = isinstance(t, list)
    except Exception as e:
        ok = False
        print("       ", type(e).__name__, e)
    check(ok, "read_types(空屏) 返回 []")

    print("\n=== 真实 Screen（游戏可能没开）不能崩 ===")
    try:
        real = winio.Screen()
        ok = hasattr(real, "client_size")
    except Exception as e:
        ok = False
        print("       ", type(e).__name__, e)
    check(ok, "winio.Screen() 构造后 client_size 必须可访问")

    print()
    if bad:
        print("FAILED %d" % bad)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
