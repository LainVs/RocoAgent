# -*- coding: utf-8 -*-
"""test_switch_panel.py - 强制换人面板判据的回归测试。

判据见 `src/ui_probe.py` 里 PARTY_BOX 上方那段长注释：用名牌里的
**金星（★N）** 当锚点，行目标 = (金星x - 16, 金星y)，正好满足
`robot.Bot._living_rows` 的读数带 (tx+6, ty-16, 175, 30) 落在血条上。

样张是真实整帧截图裁出来的（只保留判据读的两块区域），生成方式：
    & $py scripts/make_switch_fixtures.py
每个样张对应一个真实场景，文件名即场景：

    panel_two_fainted.png      强制换人面板，5 张卡，前两张空血
    panel_one_fainted.png      强制换人面板，第 2 张空血（列表整体上移 40px）
    normal_turn_with_list.png  正常回合：队伍列表在，但不该判成换人面板
    normal_turn_countdown.png  同上（顶部有回合倒计时）

这四张把三次真实事故钉住了：
  * 判不出面板 -> 死了不换人，等到游戏自动换（丢 15~45 秒）
  * 判出面板但行目标偏 -> 血条读成 0%，把活着的精灵当成尸体跳过
  * 正常回合误判成面板 -> 反复点卡片，白白丢回合

usage: python test_switch_panel.py
"""
import os
import sys
import types

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import paths  # noqa: E402,F401  (配置 paths.TOOLS 等)
sys.path.insert(0, str(paths.TOOLS))

import robot      # noqa: E402
import ui_probe   # noqa: E402

FIX = os.path.join(ROOT, "data", "fixtures", "switch")
BASE_OFF = (13, 58)
BASE_W, BASE_H = 2560, 1600


class FakeScreen:
    """模拟 winio.Screen.grab_s：box 是**窗口坐标**，返回基准尺寸的 RGB 图。"""

    def __init__(self, path):
        im = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if im is None:
            raise IOError("cannot read " + path)
        self.im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)

    def grab_s(self, box):
        x, y, w, h = box
        cx, cy = x - BASE_OFF[0], y - BASE_OFF[1]
        out = np.zeros((h, w, 3), np.uint8)
        sx0, sy0 = max(0, cx), max(0, cy)
        ex0, ey0 = min(BASE_W, cx + w), min(BASE_H, cy + h)
        if ex0 > sx0 and ey0 > sy0:
            out[sy0 - cy:ey0 - cy, sx0 - cx:ex0 - cx] = \
                self.im[sy0:ey0, sx0:ex0]
        return out


# (样张, 每行的"还活着"期望；None = 只要求行数)
CASES = [
    ("panel_two_fainted.png", [False, False, True, True, True], True),
    ("panel_one_fainted.png", [True, False, True, True, True], True),
    ("normal_turn_with_list.png", None, False),
    ("normal_turn_countdown.png", None, False),
]


def check_panel(name, want_alive, expect_rows):
    fs = FakeScreen(os.path.join(FIX, name))
    rows = robot.find_switch_rows(fs, bar_visible=False)
    fails = []

    if expect_rows:
        if len(rows) < 3:
            fails.append("只认到 %d 行（应 >=3）" % len(rows))
            return fails
        if len(rows) != len(want_alive):
            fails.append("行数 %d != 期望 %d" % (len(rows), len(want_alive)))
        # 行间距要像真实的卡片列（实测 111~150），否则可能是零星误检
        ys = [p[1] for p in rows]
        gaps = [b - a for a, b in zip(ys, ys[1:])]
        if any(not (90 <= g <= 175) for g in gaps):
            fails.append("行间距异常: %s" % gaps)
        # 行目标必须落在 PARTY_BOX 里（否则 grab_s 会读到别的区域）
        x0, y0, w, h = ui_probe.PARTY_BOX
        for tx, ty in rows:
            if not (x0 - 40 <= tx <= x0 + w and y0 <= ty <= y0 + h):
                fails.append("行目标越界: (%d,%d)" % (tx, ty))
                break
        # 死活判定：走 robot 的真实读数带，确认它确实落在血条上
        bot = types.SimpleNamespace(screen=fs)
        cand = robot.Bot._living_rows(bot, rows)
        if len(cand) != len(want_alive):
            fails.append("_living_rows 只给出 %d 行" % len(cand))
        else:
            for slot, _pos, fill in cand:
                got = fill >= 0.06
                if got != want_alive[slot - 1]:
                    fails.append("slot%d fill=%.2f 判成 %s（应 %s）"
                                 % (slot, fill,
                                    "alive" if got else "fainted",
                                    "alive" if want_alive[slot - 1]
                                    else "fainted"))
    else:
        if len(rows) >= 3:
            fails.append("误判成换人面板（%d 行）" % len(rows))
    return fails


def check_e_path():
    """按 E 主动换人时倒计时还在，必须能传 countdown=False 绕过否决。"""
    p = os.path.join(FIX, "normal_turn_countdown.png")
    fs = FakeScreen(p)
    rows = robot.find_switch_rows(fs, bar_visible=False, countdown=False)
    if len(rows) < 3:
        return ["countdown=False 仍只认到 %d 行，主动换人会失效" % len(rows)]
    return []


def main():
    if not os.path.isdir(FIX):
        print("FAILED 1: 样张目录不存在: %s" % FIX)
        print("先跑: & $py scripts/make_switch_fixtures.py")
        return 1

    bad = 0
    missing = 0
    print("--- 换人面板判据 ---")
    for name, want_alive, expect_rows in CASES:
        if not os.path.exists(os.path.join(FIX, name)):
            missing += 1
            print("%-28s MISSING" % name)
            continue
        fails = check_panel(name, want_alive, expect_rows)
        bad += 1 if fails else 0
        print("%-28s %s" % (name, "OK" if not fails else "**FAIL**"))
        for f in fails:
            print("      - " + f)

    print()
    print("--- 主动换人（E）路径 ---")
    fails = check_e_path()
    bad += 1 if fails else 0
    print("%-28s %s" % ("countdown=False 仍能认面板",
                        "OK" if not fails else "**FAIL**"))
    for f in fails:
        print("      - " + f)

    print()
    if missing:
        print("(%d 张样张缺失，按失败计)" % missing)
    if bad:
        print("FAILED %d" % bad)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
