# -*- coding: utf-8 -*-
"""game_cli.py —— 手动操作游戏的命令行工具（调试用）。

用途
----
没有桌面 MCP 时，用项目自带的 BLE/鼠标能力直接操作游戏，
用来人工确认队伍配置、面板内容等。

用法
----
    python scripts\\game_cli.py shot                    # 整窗截图
    python scripts\\game_cli.py shot --roi team         # 只截队伍面板
    python scripts\\game_cli.py click 1290 1238         # 点击（客户区坐标）
    python scripts\\game_cli.py key space               # 按键
    python scripts\\game_cli.py move 1290 1238          # 只移动鼠标

注意
----
坐标是**基准分辨率**（2560x1600）的客户区坐标，脚本会自动按当前
窗口尺寸缩放。这与 config 里的 *_REL 常量是同一套坐标系。

跑法前先确保：
    1. 游戏窗口在前台（脚本会尝试聚焦）
    2. BLE 板子已连接（点击/按键走 BLE）
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

OUT_DIR = os.path.join(ROOT, "logs", "shots")


def _screen():
    from winio import Screen
    scr = Screen()
    if scr.rect is None:
        print("!! 找不到游戏窗口")
        sys.exit(1)
    return scr


def cmd_shot(scr, args):
    import cv2
    roi = None
    if "--roi" in args:
        i = args.index("--roi")
        if i + 1 < len(args):
            roi = args[i + 1]

    import config
    rois = {
        "team": getattr(config, "TEAM_BOX", None),
        "skill": getattr(config, "SKILL_BOX", None),
        "mybar": getattr(config, "MY_BAR", None),
        "foebar": getattr(config, "FOE_BAR", None),
        "foename": getattr(config, "FOE_NAME_BOX", None),
        "panel": getattr(config, "PANEL_BOX", None),
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    ts = time.strftime("%H%M%S")

    if roi and roi in rois and rois[roi]:
        img = scr.grab_s(rois[roi])
        path = os.path.join(OUT_DIR, "%s_%s.png" % (roi, ts))
    else:
        if roi:
            print("!! 未知 ROI: %s（可用：%s）" % (
                roi, [k for k, v in rois.items() if v]))
        img = scr.grab_full()
        path = os.path.join(OUT_DIR, "full_%s.png" % ts)

    if img is None:
        print("!! 抓图失败")
        return 1
    cv2.imwrite(path, img)
    print("%s  (%dx%d)" % (path, img.shape[1], img.shape[0]))
    return 0


def _ble_session():
    """建立 BLE 会话（走开发板）。所有鼠标/按键都必须经过它。"""
    import asyncio
    import esp_shim as rocs
    sess = rocs.Session(verbose=True)
    loop = asyncio.new_event_loop()
    ok = loop.run_until_complete(sess.connect())
    if not ok:
        raise RuntimeError("BLE 连接失败（板子插好了吗？espfast 在跑吗？）")
    return sess, loop


def cmd_click(scr, args):
    if len(args) < 2:
        print("用法: click X Y   （客户区坐标，基准 2560x1600）")
        return 1
    x, y = int(args[0]), int(args[1])
    pt = scr.abs_point((0, 0, 2560, 1600), x, y)
    if pt is None:
        print("!! 坐标换算失败")
        return 1
    ax, ay = pt
    print("点击 客户区(%d,%d) -> 物理(%d,%d)" % (x, y, ax, ay))
    # **必须走开发板**：BLE 硬件鼠标
    sess, loop = _ble_session()
    try:
        loop.run_until_complete(sess.click_at(ax, ay))
        print("  已通过开发板点击")
    finally:
        try:
            loop.run_until_complete(sess.close())
        except Exception:
            pass
    return 0


def cmd_move(scr, args):
    if len(args) < 2:
        print("用法: move X Y")
        return 1
    x, y = int(args[0]), int(args[1])
    pt = scr.abs_point((0, 0, 2560, 1600), x, y)
    if pt is None:
        print("!! 坐标换算失败")
        return 1
    ax, ay = pt
    print("移动 客户区(%d,%d) -> 物理(%d,%d)" % (x, y, ax, ay))
    sess, loop = _ble_session()
    try:
        loop.run_until_complete(sess.move_to(ax, ay))
        print("  已通过开发板移动")
    finally:
        try:
            loop.run_until_complete(sess.close())
        except Exception:
            pass
    return 0


def cmd_key(scr, args):
    if not args:
        print("用法: key space|esc|enter|1|2|f")
        return 1
    k = args[0]
    print("按键: %s" % k)
    sess, loop = _ble_session()
    try:
        loop.run_until_complete(sess.key(str(k)))
        print("  已通过开发板发送")
    finally:
        try:
            loop.run_until_complete(sess.close())
        except Exception:
            pass
    return 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) < 2:
        print(__doc__)
        return 0

    cmd = sys.argv[1]
    args = sys.argv[2:]
    scr = _screen()
    print("窗口 rect=%s 客户区=%s" % (scr.rect, scr.client_size))

    if cmd == "shot":
        return cmd_shot(scr, args)
    if cmd == "click":
        return cmd_click(scr, args)
    if cmd == "move":
        return cmd_move(scr, args)
    if cmd == "key":
        return cmd_key(scr, args)

    print("未知命令: %s" % cmd)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
