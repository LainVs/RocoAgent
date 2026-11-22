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
import json
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


def health(port, timeout=1.5):
    """在跑的服务信息（pid / 自身代码 mtime）；没在跑返回 None。"""
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/health" % port,
                timeout=timeout) as r:
            if r.status != 200:
                return None
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _local_src_mtime():
    try:
        return os.path.getmtime(os.path.join(SRC, "ui_server.py"))
    except OSError:
        return 0.0


def _stale(h):
    """在跑的服务是不是旧代码（mtime 不一致）。"""
    if not h:
        return False
    remote = float(h.get("src_mtime") or 0)
    return bool(remote) and abs(remote - _local_src_mtime()) > 0.5


def _kill_pid(pid):
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def start_server(port, allow_restart=True):
    """起服务；已经在跑就复用 —— 但**跑的是旧代码时要重启**。

    为什么必须重启（2026-11-23 实测）
    ------------------------------
    上一版只 print 一句"服务已在运行"就复用。用户改了代码（比如修好
    UI 的启动按钮）后双击桌面快捷方式，新进程绑 8770 失败静默退出，
    页面继续连旧服务 —— 表现为"改了没用"。现在对比 `/api/health` 里
    的 `src_mtime`，不一致就杀掉旧进程重起。
    """
    h = health(port)
    if h:
        if not _stale(h):
            print("  服务已在运行  http://127.0.0.1:%d（pid %s）"
                  % (port, h.get("pid")))
            return True
        if not allow_restart:
            print("  ⚠ 端口 %d 上是**旧代码**的服务（pid %s）"
                  % (port, h.get("pid")))
            print("    请先关掉它，或加 --restart 自动重启")
            return False
        print("  端口 %d 上是旧代码的服务（pid %s）→ 重启"
              % (port, h.get("pid")))
        _kill_pid(h.get("pid"))
        for _ in range(20):
            time.sleep(0.2)
            if not alive(port):
                break
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
    """开控制台窗口。

    ★ 不用 webbrowser.open()
    -----------------------
    用户反馈："点启动应用就要打开一整个谷歌浏览器，会很卡的"。

    Chrome 一个实例起步几百 MB，还会混进用户自己的浏览会话
    （关掉时可能连带关掉用户正在看的标签页）。

    改用 WebView2 独立窗口（console_win.py）：系统自带、单独进程、
    内存低一个量级，而且和悬浮窗同一套渲染引擎。
    只有 WebView2 也不可用时才退回浏览器。
    """
    url = "http://127.0.0.1:%d/console.html" % port
    print("  控制台        %s" % url)

    exe = sys.executable
    try:
        import importlib.util
        has_wv = importlib.util.find_spec("webview") is not None
    except Exception:
        has_wv = False

    if has_wv:
        subprocess.Popen(
            [exe, os.path.join(SRC, "console_win.py"), "--port", str(port)],
            cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=DETACHED | NO_WINDOW)
        print("                （WebView2 窗口，不占用浏览器）")
        return True

    # 兜底：当前解释器没有 pywebview
    print("                当前解释器没有 pywebview，退回浏览器")
    print("                想用独立窗口请用系统 Python：")
    print("                  python src\\ui.py")
    webbrowser.open(url)
    return False


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
    ap.add_argument("--no-restart", action="store_true",
                    help="端口上已有旧代码的服务时不自动重启")
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
