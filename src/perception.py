"""perception.py - 屏幕 → state

用 Windows 自带 OCR（Windows.Media.Ocr），不需要装任何东西。
所有区域坐标相对游戏窗口左上角，窗口移动不影响。

用法：
  python perception.py --calibrate      保存各区域截图，用于人工核对
  python perception.py --state          输出当前 state.json
"""

import io
import json
import os
import re
import subprocess
import sys


sys.path.insert(0, os.path.dirname(__file__))
import capture

OCR_PS1 = r"D:\Tools\winocr.ps1"
LOG_DIR = r"D:\Projects\RocoAgent\logs"

# 区域（相对窗口左上角，物理像素）——首次使用需校准
REGIONS = {
    "me_card":   (150, 60, 420, 90),     # 我方名字 + 血条
    "foe_card":  (2080, 60, 420, 90),    # 敌方名字 + 血条
    "energy":    (200, 1420, 260, 70),   # 能量
    "skill_1":   (330, 600, 200, 150),
    "skill_2":   (330, 810, 200, 150),
    "skill_3":   (330, 1020, 200, 150),
    "skill_4":   (330, 1230, 200, 150),
}


def ocr(path):
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", OCR_PS1, path],
                           capture_output=True, text=True, timeout=25,
                           encoding="utf-8", errors="ignore")
        return r.stdout.strip()
    except Exception as e:
        return ""


def grab_region(name):
    dx, dy, w, h = REGIONS[name]
    arr = capture.grab_rel(dx, dy, w, h)
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, "r_%s.png" % name)
    capture.save_png(arr, path)
    return path


def nums(text):
    return [int(x) for x in re.findall(r"\d+", text)]


def calibrate():
    print("游戏窗口:", capture.game_rect())
    for name in REGIONS:
        p = grab_region(name)
        t = ocr(p)
        print("  %-10s -> %r" % (name, t[:60]))


def read_state():
    state = {"me": {}, "foe": {}, "skills": []}

    me_txt = ocr(grab_region("me_card"))
    foe_txt = ocr(grab_region("foe_card"))
    en_txt = ocr(grab_region("energy"))

    state["me"]["raw"] = me_txt
    state["foe"]["raw"] = foe_txt
    state["me"]["energy_text"] = en_txt

    n = nums(me_txt)
    if len(n) >= 2:
        state["me"]["hp_abs"], state["me"]["hp_max"] = n[0], n[1]
        state["me"]["hp_pct"] = round(100.0 * n[0] / n[1], 1) if n[1] else 0

    nf = nums(foe_txt)
    if nf:
        state["foe"]["hp_pct"] = nf[-1] if len(nf) == 1 else nf[-1]

    ne = nums(en_txt)
    if ne:
        state["me"]["energy"] = ne[0]

    for i in range(1, 5):
        t = ocr(grab_region("skill_%d" % i))
        d = nums(t)
        state["skills"].append({"key": i, "text": t, "shown": d[0] if d else None})

    return state


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "--calibrate":
        calibrate()
    elif args[0] == "--state":
        print(json.dumps(read_state(), ensure_ascii=False, indent=2))

