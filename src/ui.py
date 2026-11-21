# -*- coding: utf-8 -*-
"""ui.py —— 前端统一入口。

一条命令把整套前端拉起来：

    python src/ui.py                # 服务 + 浏览器打开控制台
    python src/ui.py --overlay      # 服务 + 悬浮窗（盖在游戏上）
    python src/ui.py --overlay --console   # 两个都开
    python src/ui.py --port 8780

为什么不直接用 ui_server.py
---------------------------
ui_server 只管数据，不管窗口。这个文件负责：
  · 挑一个可用端口（8770 起，被占就 +1）
  · 起服务（脱离父进程，关掉终端它也活着）
  · 按需打开控制台 / 悬浮窗
  · 退出时清理（可选）

设计原则：任何一个页面挂了都不影响其它部分。
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")

NO_WINDOW = 0x08000000
DETACHED = 0x00000008


def port_free(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pick_port(start):
    """从 start 往上找一个空端口。"""
    for p in range(start, start + 40):
        if port_free(p):
            return p
    return start


def alive(port, timeout=1.5):
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/health" % port, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def start_server(port):
    """起服务；已经在跑就直接复用。"""
    if alive(port):
        print("  服务已在运行  http://127.0.0.1:%d" % port)
        return True
    subprocess.Popen(
        [sys.executable, os.path.join(SRC, "ui_server.py"), "--port", str(port)],
        cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=DETACHED | NO_WINDOW)
    for _ in range(25):
        time.sleep(0.3)
        if alive(port):
            print("  服务已启动    http://127.0.0.1:%d" % port)
            return True
    print("  服务启动失败（端口 %d）" % port)
    return False


def open_console(port):
    url = "http://127.0.0.1:%d/console.html" % port
    print("  控制台        %s" % url)
    webbrowser.open(url)


def open_overlay(port):
    """悬浮窗要独立进程 —— 它有自己的 GUI 事件循环。"""
    print("  悬浮窗        独立进程启动中…")
    subprocess.Popen(
        [sys.executable, os.path.join(SRC, "overlay.py"),
         "--port", str(port)],
        cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=DETACHED | NO_WINDOW)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--console", action="store_true", help="打开控制台（默认）")
    ap.add_argument("--overlay", action="store_true", help="打开悬浮窗")
    ap.add_argument("--serve-only", action="store_true", help="只起服务，不开窗口")
    args = ap.parse_args()

    # 先看**指定端口**上是不是已经有我们的服务在跑。
    #
    # 踩过的坑：原来直接 pick_port(args.port)，而它只判断"端口是否空闲"。
    # 桌面快捷方式连点两次时，第一次已经起了 8770，第二次发现 8770 被占
    # 就去开 8771 —— 于是同时跑着两个 ui_server（一个根本没人用）。
    # 正确做法是先探一次 /api/health，是自己的服务就复用。
    port = args.port if alive(args.port) else pick_port(args.port)
    print("RocoAgent 前端")
    print()

    if not start_server(port):
        return 2

    if args.serve_only:
        print()
        print("  只提供数据。Ctrl+C 结束。")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0

    if args.overlay:
        open_overlay(port)
    # 默认开控制台；显式要 overlay 且不要 console 时跳过
    if args.console or not args.overlay:
        open_console(port)

    print()
    print("  已就绪。服务会一直运行（关掉这个窗口也不影响）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
