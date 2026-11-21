# -*- coding: utf-8 -*-
"""test_overlay_pos.py —— 悬浮窗位置的口径与自愈。

这个 bug 的现场（2026-11-21，用户报"任务栏看见了，屏幕上看不到"）
--------------------------------------------------------------------
    config/overlay.json   x=2118 y=422        （物理像素，来自 GetWindowRect）
    create_window(x=2118)  被当成 CSS 像素 -> pywebview 再乘 2
    实际窗口 left = 4236    而屏幕只有 3072 宽 -> 完全跑到屏幕外

两道修正：
  1. 读配置时 / scale，把物理像素还原成 CSS
  2. clamp_to_virtual_screen() 自愈：算出来的窗口如果落在虚拟桌面之外
     （换显示器 / 分辨率变了 / 存了坏值），回左上角

★ 口径陷阱（踩了两次）
--------------------
overlay 进程**故意不是 DPI-aware**（设了会把 create_window 的尺寸当物理，
窗口只剩一半）。所以 GetSystemMetrics(SM_CXVIRTUALSCREEN) 返回的是
**逻辑值**（200% DPI 下报 1536x960，实际 3072x1920）。
拿物理坐标去比逻辑宽度，任何 x > 1536 的合法位置都会被误判成"屏幕外"。

用法：python src/test_overlay_pos.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import overlay  # noqa: E402

fails = 0


def check(cond, name, detail=""):
    global fails
    if not cond:
        fails += 1
    print("  %-4s %-50s %s" % ("OK" if cond else "FAIL", name, detail))


def main():
    # 不调用 dpi_scale()：它在本进程里可能返回 1.0
    # （GetDpiForSystem 对非 DPI-aware 进程报 96）。
    # fit_window 的注释也记了这一点："dpi_scale 在 overlay 进程里实测
    # 返回 1.0，不可靠"。
    #
    # 所以测试显式注入比例。本机是 200%，而逻辑桌面由 GetSystemMetrics
    # 给出（非 DPI-aware 进程拿到的是逻辑值）。
    SCALE = 2.0
    UD = overlay.UD
    lw = UD.GetSystemMetrics(78)
    lh = UD.GetSystemMetrics(79)
    vw = int(round(lw * SCALE))
    vh = int(round(lh * SCALE))
    print("注入 DPI 比例 = %.1f   逻辑桌面 %dx%d -> 物理 %dx%d"
          % (SCALE, lw, lh, vw, vh))

    print()
    print("[1] 屏幕内的位置原样保留")
    x, y = overlay.clamp_to_virtual_screen(40, 40, 353, 369, SCALE)
    check((x, y) == (40, 40), "左上角 (40,40) 不动", "-> (%d,%d)" % (x, y))

    print()
    print("[2] 靠右但仍在屏幕内 —— 这条专抓「逻辑 vs 物理」混用")
    # 选一个"按物理算合法、按逻辑算越界"的位置：
    #   css 1436 -> 物理 2872，窗口 706 宽，交集 200px ≥ 80 -> 应当保留
    #   如果拿 2872 去比逻辑宽度 1536 -> 会被误判越界
    css_x = float(lw - 100)
    x, y = overlay.clamp_to_virtual_screen(css_x, 40, 353, 369, SCALE)
    check(abs(x - css_x) < 1.5, "靠右的合法位置不被误判",
          "css %.0f -> %.0f（物理 %.0f，逻辑桌面 %d）"
          % (css_x, x, css_x * SCALE, lw))

    print()
    print("[3] 屏幕外的坏值回左上角")
    for bad in ((99999, 40), (-99999, 40), (40, 99999), (40, -99999)):
        x, y = overlay.clamp_to_virtual_screen(bad[0], bad[1], 353, 369, SCALE)
        check((x, y) == (40, 40), "越界 %s 被夹回" % (bad,),
              "-> (%d,%d)" % (x, y))

    print()
    print("[4] 位置往返（物理写出 -> 读回再 /scale）")
    tmp = tempfile.mkdtemp()
    old = overlay._STATE_FILE
    overlay._STATE_FILE = os.path.join(tmp, "overlay.json")
    try:
        # 模拟：窗口当前物理位置 1992,28（drag_end 就是这么存的）
        overlay.save_window_state(x=1992, y=28)
        got = overlay.load_window_state()
        check(got.get("x") == 1992 and got.get("y") == 28,
              "存的是物理像素", str(got))

        css_x = int(round(got["x"] / SCALE))
        check(int(round(css_x * SCALE)) == 1992,
              "CSS 再乘回去等于原物理值",
              "%d * %.1f = %d" % (css_x, SCALE, int(round(css_x * SCALE))))

        # 关键回归：修复前是把物理值直接当 CSS 用，于是放大成 3984
        wrong = int(round(1992 * SCALE))
        check(wrong > vw, "（对照）修复前会跑到屏幕外",
              "phys_x=%d > 物理桌面 %d" % (wrong, vw))

        print()
        print("[5] folded 标记也能往返")
        overlay.save_window_state(folded=True)
        check(overlay.load_window_state().get("folded") is True, "folded=True 读回")
        overlay.save_window_state(folded=False)
        check(overlay.load_window_state().get("folded") is False, "folded=False 读回")

        print()
        print("[6] 坏 JSON 不炸")
        with open(overlay._STATE_FILE, "w", encoding="utf-8") as f:
            f.write("{not json")
        check(overlay.load_window_state() == {}, "坏文件返回空 dict")
    finally:
        overlay._STATE_FILE = old
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        print("%d FAILURE(S)" % fails)
        return 1
    print("all overlay position cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
