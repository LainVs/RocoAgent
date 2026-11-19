# -*- coding: utf-8 -*-
"""抓帧工具：把"以后要用的画面"存下来。

三种模式：
    --watch-result   结算页一出现就连拍（全分辨率），用于研究胜负判定
    --seq            常速连拍（默认 1.5 秒一张），用于找技能播报/buff 图标
    --turn           每回合一张（机器人自己已经在存 logs/shot）

为什么要它：
    结算页只出现几秒，机器人的状态机这时在忙着点跳过，很容易错过。
    这个进程不碰输入，只读画面，发现紫色段位卡就连拍 10 秒。

    --seq 用来回答"对手放的技能在哪里显示、buff 图标在哪"，这些以后
    要接进伤害计算器：对面用什么招决定我们该不该换人。
"""
from __future__ import annotations

import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "logs")


def _save(img, path, scale=1.0, q=80):
    if scale != 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, q])


def watch_result(minutes=60):
    import rank
    import robot
    out = os.path.join(LOGS, "resultshots")
    os.makedirs(out, exist_ok=True)
    scr = robot.Screen()
    t_end = time.time() + minutes * 60
    inside = False
    while time.time() < t_end:
        try:
            purple = rank._purple_frac(scr.grab(rank.RESULT_DIV_ROI))
            if purple > 0.06 and not inside:
                inside = True
                stamp = time.strftime("%H%M%S")
                print("result screen at %s (purple %.3f)" % (stamp, purple), flush=True)
                for i in range(12):
                    img = scr.grab_client((0, 0, 2560, 1600))
                    if img is not None and img.size:
                        _save(img, os.path.join(out, "%s_%02d.jpg" % (stamp, i)), 1.0, 85)
                    time.sleep(0.35)
            elif purple <= 0.02:
                inside = False
        except Exception as e:
            print("watch error: %s" % e, flush=True)
        time.sleep(0.4)


def seq(seconds=120, interval=1.5):
    import robot
    out = os.path.join(LOGS, "seq")
    os.makedirs(out, exist_ok=True)
    scr = robot.Screen()
    n = 0
    t_end = time.time() + seconds
    while time.time() < t_end:
        img = scr.grab_client((0, 0, 2560, 1600))
        if img is not None and img.size:
            n += 1
            _save(img, os.path.join(out, "%s_%03d.jpg" % (time.strftime("%H%M"), n)), 0.5, 65)
        time.sleep(interval)
    print("frames", n)


if __name__ == "__main__":
    if "--watch-result" in sys.argv:
        mins = 60
        if "--minutes" in sys.argv:
            mins = int(sys.argv[sys.argv.index("--minutes") + 1])
        watch_result(mins)
    elif "--seq" in sys.argv:
        secs = 120
        if "--seconds" in sys.argv:
            secs = int(sys.argv[sys.argv.index("--seconds") + 1])
        seq(secs)
    else:
        print(__doc__)
