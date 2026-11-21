# -*- coding: utf-8 -*-
"""grab_screen.py —— 截取游戏窗口，保存成图片供查看。

用途：没有桌面 MCP 时，用项目自带的 Screen 类抓图，
直接看游戏当前画面（队伍面板、技能栏、对手等）。

跑法：
    python scripts\\grab_screen.py              # 整窗
    python scripts\\grab_screen.py --roi team   # 只看队伍面板
    python scripts\\grab_screen.py --roi skill  # 只看技能栏
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "src"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "logs", "shots")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    import config
    from winio import Screen

    scr = Screen()
    if scr.rect is None:
        print("!! 找不到游戏窗口")
        print("   请确认游戏已启动，且窗口标题匹配：%s" % config.TITLE)
        return 1

    print("窗口 rect = %s" % (scr.rect,))
    print("客户区   = %s" % (scr.client_size,))

    os.makedirs(OUT_DIR, exist_ok=True)
    import time
    ts = time.strftime("%H%M%S")

    roi_name = None
    if "--roi" in sys.argv:
        idx = sys.argv.index("--roi")
        if idx + 1 < len(sys.argv):
            roi_name = sys.argv[idx + 1]

    rois = {
        "team": getattr(config, "TEAM_BOX", None),
        "skill": getattr(config, "SKILL_BOX", None),
        "mybar": getattr(config, "MY_BAR", None),
        "foebar": getattr(config, "FOE_BAR", None),
    }

    if roi_name and roi_name in rois and rois[roi_name]:
        box = rois[roi_name]
        img = scr.grab_s(box)
        path = os.path.join(OUT_DIR, "%s_%s.png" % (roi_name, ts))
    else:
        img = scr.grab_full()
        path = os.path.join(OUT_DIR, "full_%s.png" % ts)

    if img is None:
        print("!! 抓图失败")
        return 1

    try:
        import cv2
        cv2.imwrite(path, img)
    except Exception as e:
        print("!! 保存失败: %s" % e)
        return 1

    print("已保存: %s  (%dx%d)" % (path, img.shape[1], img.shape[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
