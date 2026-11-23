# -*- coding: utf-8 -*-
"""S23 游戏画面状态探测（对战时人工运行，采集 UI 数据）。

用法：
    python scripts/probe_game_state.py [输出前缀]

输出：当前画面状态判定 + 动作按钮/换人行位置 + 截图（logs/）。
用途：为魔法/进化按钮的 UI 探测常量采集实测数据（S23 后续）。
"""
import sys

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

import cv2  # noqa: E402
import winio  # noqa: E402
import ui_probe  # noqa: E402


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else "probe"
    h = winio.find_game()
    if not h:
        print("game window not found")
        return 1
    win = h[0]
    scr = winio.Screen(win)
    box = (h[1], h[2], h[3], h[4])
    img = scr.grab(box)
    out_png = "logs/%s_state.png" % prefix
    cv2.imwrite(out_png, img)

    print("window:", h)
    print("has_vs_screen:", ui_probe.has_vs_screen(scr))
    print("is_result_screen:", ui_probe.is_result_screen(scr))
    print("has_start_button:", ui_probe.has_start_button(scr))
    ab = ui_probe.find_action_button(scr)
    print("find_action_button:", ab)
    rows = ui_probe.find_switch_rows(scr)
    print("find_switch_rows:", len(rows) if rows else 0)
    print("saved:", out_png)
    print("\n备注：魔法/进化按钮只在战斗内出现——"
          "对战进行中运行本脚本，从截图量取按钮坐标后")
    print("把常量补进 ui_probe（MAGIC_BTN / EVOLVE_BTN），"
          "即可接通 S22 的决策层。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
