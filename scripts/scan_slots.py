# -*- coding: utf-8 -*-
"""scan_slots.py —— 精确扫描队伍栏 6 个槽位的 x 坐标（闭环验证）。

## 前提（已验证）

`calib_cursor.py` 实测三个点，偏差恒为 (+2, +0) 物理像素 ——
换算 `screen = client_rect + shot*scale` **是对的**，公式无需修正。
之前点偏纯粹是**头像 x 坐标估错**（目测误差 20~40px）。

## 做法

在队伍栏 y=250 上**扫一条线**，每 12px 点一次，读右侧面板是否变化：
  - 面板基准图（未点前）与每步比对
  - 变化显著 -> 命中一个头像（因为选中项会换）
  - 收集所有命中 x，按间距归组，得到 6 个槽位中心

好处：不依赖任何图像检测，只依赖"点击后界面真的变了"。
代价：要点击约 100 次；每次 0.55s -> 约 1 分钟。可接受，且只跑一次。

## 用法

    & $py scripts\\scan_slots.py            # 全范围扫描
    & $py scripts\\scan_slots.py --dry      # 只打印计划，不点击
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

SCAN_Y = 250
SCAN_X0, SCAN_X1, SCAN_STEP = 150, 1250, 12
PANEL = (1330, 60, 1230, 620)      # 右侧面板上半（名字+属性，变化最明显）


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from winio import Screen
    import esp_shim as rocs
    import cv2
    import numpy as np

    dry = "--dry" in sys.argv

    scr = Screen()
    if scr.rect is None:
        print("!! 找不到游戏窗口")
        return 1
    cr = scr.client_rect_on_screen()
    scx, scy = scr.scale()
    print("客户区=%s scale=(%.2f,%.2f)" % (cr, scx, scy))

    def to_screen(x, y):
        return (cr[0] + int(round(x * scx)), cr[1] + int(round(y * scy)))

    xs = list(range(SCAN_X0, SCAN_X1 + 1, SCAN_STEP))
    print("计划扫描 %d 个 x: %d..%d 步长 %d  y=%d"
          % (len(xs), SCAN_X0, SCAN_X1, SCAN_STEP, SCAN_Y))
    if dry:
        return 0

    px, py, pw, ph = PANEL

    loop = asyncio.new_event_loop()
    sess = rocs.Session(verbose=False)
    if not loop.run_until_complete(sess.connect()):
        print("!! BLE 连接失败")
        return 1

    hits = []
    try:
        base = scr.grab_full()[py:py + ph, px:px + pw]
        for x in xs:
            sx_, sy_ = to_screen(x, SCAN_Y)
            loop.run_until_complete(sess.click_at(sx_, sy_))
            time.sleep(0.42)
            cur = scr.grab_full()[py:py + ph, px:px + pw]
            d = cv2.absdiff(base, cur)
            changed = int((d.max(axis=2) > 40).sum())
            tag = ""
            if changed > 6000:
                hits.append(x)
                tag = "  <<< 命中"
                base = cur          # 命中后基准更新，继续找下一个
            print("  x=%-5d 变化%-7d%s" % (x, changed, tag), flush=True)
    finally:
        try:
            loop.run_until_complete(sess.close())
        except Exception:
            pass

    print("\n=== 命中 x ===")
    print(hits)
    if len(hits) > 1:
        d = [hits[i + 1] - hits[i] for i in range(len(hits) - 1)]
        print("相邻间距: %s" % d)
    return 0


if __name__ == "__main__":
    sys.exit(main())