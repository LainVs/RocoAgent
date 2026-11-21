# -*- coding: utf-8 -*-
"""console_win.py —— 用 WebView2 独立窗口打开控制台（不开浏览器）。

为什么不用 webbrowser.open()
----------------------------
用户反馈："点启动应用就要打开一整个谷歌浏览器，会很卡的"。

Chrome 一个实例起步就是几百 MB，而且和用户的浏览记录、扩展、同步
混在一起，关掉时还可能连带关掉用户正在看的标签页。

WebView2 是**系统自带**的（Win10/11 随 Edge 安装），而且：
  · 单独进程，不碰用户的浏览器会话
  · 内存占用比 Chrome 低一个量级（约 60~120MB）
  · 和悬浮窗用同一套渲染引擎，样式表现一致

用法
----
    python src/console_win.py                # 起服务 + 开窗口
    python src/console_win.py --port 8770    # 连已有服务

注意：这个文件**必须用装了 pywebview 的解释器**（本机是系统 Python
C:\\Python314），不是项目 .venv。见 docs/STARTUP.md §1.1。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)


def alive(port, timeout=1.5):
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/health" % port,
                timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--width", type=int, default=1180)
    ap.add_argument("--height", type=int, default=820)
    ap.add_argument("--page", default="console.html")
    args = ap.parse_args()

    try:
        import webview
    except ImportError:
        print("缺少 pywebview，控制台窗口起不来。")
        print("  当前解释器: %s" % sys.executable)
        print("  请用系统 Python 运行： python src\\console_win.py")
        return 2

    # 服务可能已经在跑（ui.py 起的），直接复用
    port = args.port
    if not alive(port):
        import ui
        port = ui.pick_port(args.port)
        if not ui.start_server(port):
            print("无法启动 ui_server（端口 %d）" % port)
            return 2

    url = "http://127.0.0.1:%d/%s" % (port, args.page)
    print("控制台窗口")
    print("  地址  %s" % url)
    print("  尺寸  %dx%d" % (args.width, args.height))
    print()

    # 记住窗口位置，下次开在同一个地方（和悬浮窗同一套思路）
    cfg = os.path.join(ROOT, "config", "console_window.json")
    pos = {}
    try:
        import io
        import json
        pos = json.load(io.open(cfg, encoding="utf-8-sig"))
    except Exception:
        pos = {}

    win = webview.create_window(
        "RocoAgent · 控制台",
        url=url,
        width=args.width,
        height=args.height,
        x=pos.get("x"),
        y=pos.get("y"),
        resizable=True,
        min_size=(900, 600),
        text_select=True,
    )

    def _save(*_a):
        try:
            import io
            import json
            # 关闭时窗口可能已经销毁，用最后一次已知位置
            r = getattr(win, "native", None)
            if r is None:
                return
            import ctypes
            import ctypes.wintypes as wt
            hwnd = ctypes.windll.user32.FindWindowW(None,
                                                   "RocoAgent · 控制台")
            if not hwnd:
                return
            rc = wt.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc))
            os.makedirs(os.path.dirname(cfg), exist_ok=True)
            with io.open(cfg, "w", encoding="utf-8") as f:
                json.dump({"x": rc.left, "y": rc.top}, f)
        except Exception:
            pass

    try:
        win.events.closed += _save
    except Exception:
        pass

    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
