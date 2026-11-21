# -*- coding: utf-8 -*-
"""coord.py —— 坐标换算工具，避免手算出错。

踩过的坑（2026-11-21 10:20）
---------------------------
我用 MCP 截图看到「退出」按钮在图片坐标 (983, 813)，
手算：983 * 1.7778 = 1748，813 * 1.7778 = 1445
然后拿屏幕坐标 (1748, 1445) 减去**窗口原点 (595, 25)** 得到客户区 (1153, 1420)。

**这里错了**。客户区原点不是窗口原点：
    window rect         = (595, 25, 2586, 1671)
    client_rect_on_screen = (608, 83, 2560, 1600)
                            ^^^^^^^^^ 差 (13, 58)

窗口有边框和标题栏，客户区整体偏移了 (13, 58)。
所以正确的客户区坐标应该是：
    1748 - 608 = 1140
    1445 - 83  = 1362

用户反馈"你每次都没移动到正确的坐标"，根因就是这个。

用法
----
    # 图片坐标 -> 客户区坐标（可直接传给 game_cli.py click）
    python scripts\\coord.py img 983 813

    # 屏幕坐标 -> 客户区坐标
    python scripts\\coord.py screen 1748 1445

    # 客户区坐标 -> 屏幕坐标
    python scripts\\coord.py client 1140 1362

    # 打印当前窗口几何
    python scripts\\coord.py info
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "src"))


def _geom():
    from winio import Screen
    s = Screen()
    if s.rect is None:
        print("!! 找不到游戏窗口")
        sys.exit(1)
    return s


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) < 2:
        print(__doc__)
        return 0

    cmd = sys.argv[1]
    s = _geom()
    cr = s.client_rect_on_screen()
    print("窗口 rect          = %s" % (s.rect,))
    print("客户区屏幕位置     = %s" % (cr,))
    print("客户区尺寸         = %s" % (s.client_size,))
    print("缩放               = %s" % (s.scale(),))
    print()

    if cmd == "info":
        return 0

    if len(sys.argv) < 4:
        print("需要两个坐标")
        return 1

    x, y = float(sys.argv[2]), float(sys.argv[3])

    if cmd == "img":
        # 图片坐标 -> 屏幕 -> 客户区
        # MCP 截图是 3072x1920（物理分辨率），图片显示尺寸由工具返回的 scale 决定
        # 这里按 3072x1920 原图算：屏幕坐标 = 图片坐标 * (3072/图片宽)
        # 但更稳妥的做法是让用户直接给屏幕坐标。
        print("图片坐标 (%g, %g)" % (x, y))
        print("  注意：图片坐标需要先乘缩放系数才是屏幕坐标。")
        print("  MCP 截图 Original Size=(3072,1920)，若图片按 1:1 显示则系数为 1。")
        print("  建议改用 `screen` 子命令直接给屏幕坐标。")
        return 0

    if cmd == "screen":
        cx = int(round(x - cr[0]))
        cy = int(round(y - cr[1]))
        print("屏幕 (%g, %g) -> 客户区 (%d, %d)" % (x, y, cx, cy))
        print()
        print("可直接用：")
        print("  python scripts\\game_cli.py click %d %d" % (cx, cy))
        return 0

    if cmd == "client":
        sx = int(round(x + cr[0]))
        sy = int(round(y + cr[1]))
        print("客户区 (%g, %g) -> 屏幕 (%d, %d)" % (x, y, sx, sy))
        return 0

    print("未知命令: %s" % cmd)
    return 1


if __name__ == "__main__":
    sys.exit(main())
