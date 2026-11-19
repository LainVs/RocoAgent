# -*- coding: utf-8 -*-
"""每 N 秒截一张客户区画面，用来事后核对机器人当时到底看到了什么。

机器人只在日志里写"我做了什么"，不写"我看到什么"。出问题时要靠画面：
比如"四个技能都灰了还在点"这种，日志只有 lit=1111，必须看帧才能判断。

输出：logs/mon/HHMMSS.jpg（默认 30 秒一张，JPEG 质量 70，约 150-250 KB）
轮转：超过 400 张就删最旧的，一晚上约 60-100 MB。
"""
from __future__ import annotations

import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "logs", "mon")
KEEP = 400
INTERVAL = float(os.environ.get("MON_INTERVAL", "30"))


def main():
    import robot
    os.makedirs(OUT, exist_ok=True)
    scr = robot.Screen()
    while True:
        try:
            img = scr.grab_client((0, 0, 2560, 1600))
            if img is not None and img.size:
                name = time.strftime("%H%M%S") + ".jpg"
                small = cv2.resize(img, None, fx=0.5, fy=0.5,
                                   interpolation=cv2.INTER_AREA)
                cv2.imwrite(os.path.join(OUT, name),
                            cv2.cvtColor(small, cv2.COLOR_RGB2BGR),
                            [cv2.IMWRITE_JPEG_QUALITY, 70])
                try:
                    with open(os.path.join(ROOT, "logs", "monitor.beat"), "w") as f:
                        f.write(str(time.time()))
                except OSError:
                    pass
                files = sorted(os.listdir(OUT))
                for f in files[:-KEEP]:
                    try:
                        os.remove(os.path.join(OUT, f))
                    except OSError:
                        pass
        except Exception as e:
            print("monitor error: %s: %s" % (type(e).__name__, e), flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
