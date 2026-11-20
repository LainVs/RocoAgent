# -*- coding: utf-8 -*-
"""overlay.py —— 悬浮窗启动器（pywebview + WebView2）。

为什么需要这个文件
------------------
ui/index.html 本身是纯前端，浏览器也能开。但要做成"盖在游戏上的悬浮窗"，
需要这几件事，而且顺序不能错：

    1. 无边框 + 透明 + 置顶 + 不抢焦点
    2. 加载完成后**手动 ShowWindow(0) / ShowWindow(5) 一次**
       —— pywebview 的透明是延迟生效的，不做这步首帧是白底
    3. 自己加上 WS_EX_LAYERED
       —— pywebview 开出来的窗口没有这个标志，
          SetLayeredWindowAttributes 会直接返回 0（失败）
    4. DWM 关圆角、关边框，用自己的圆角

这四条都是实测踩出来的，见 docs/FRONTEND.md §七。

用法
----
    python src/overlay.py                     # 自带服务（推荐）
    python src/overlay.py --port 8770         # 指定端口
    python src/overlay.py --url http://...    # 连已有的服务

热键（全局，不需要窗口焦点）
    Ctrl+Alt+O   显示 / 隐藏
    Ctrl+Alt+P   点击穿透开 / 关
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
UI = os.path.join(ROOT, "ui")

UD = ctypes.windll.user32

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080

LWA_ALPHA = 0x2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1

DWMWA_NCRENDERING_POLICY = 2
DWMWA_TRANSITIONS_FORCEDISABLED = 3
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34

NO_WINDOW = 0x08000000
DETACHED = 0x00000008

# 默认尺寸（**逻辑/CSS 像素**）。
# 注意 DPI：实测 pywebview 把 width 当物理像素传给窗口，
# 但 WebView2 又按 devicePixelRatio 缩放，所以：
#     width=340, DPI=192 -> 窗口 680 物理px, 但 innerWidth 只有 327 CSS px
# 结果面板（设计宽 320 CSS px）几乎撑满，边距全没了。
# 正确做法：传物理像素 = 逻辑 × 缩放，这样 innerWidth 才等于设计尺寸。
DEFAULT_W = 340
DEFAULT_H = 620


def dpi_scale():
    """当前系统缩放倍数（192 DPI -> 2.0）。"""
    try:
        UD.SetProcessDPIAware()
    except Exception:
        pass
    try:
        gdi = ctypes.windll.gdi32
        dc = UD.GetDC(0)
        try:
            dpi = gdi.GetDeviceCaps(dc, 88)     # LOGPIXELSX
        finally:
            UD.ReleaseDC(0, dc)
        if dpi:
            return max(1.0, dpi / 96.0)
    except Exception:
        pass
    return 1.0


# ============================================================ Win32
def _root(hwnd):
    return UD.GetAncestor(hwnd, 2) or hwnd


def add_layered(hwnd):
    """必须自己加 WS_EX_LAYERED，否则 SetLayeredWindowAttributes 返回 0。"""
    ex = UD.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if not (ex & WS_EX_LAYERED):
        UD.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)


def set_click_through(hwnd, enabled):
    ex = UD.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if enabled:
        ex |= WS_EX_TRANSPARENT | WS_EX_LAYERED
    else:
        ex &= ~WS_EX_TRANSPARENT
    UD.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)


def hide_from_taskbar(hwnd):
    """工具窗口不进 Alt+Tab，也不显示在任务栏。悬浮窗应该这样。"""
    ex = UD.GetWindowLongW(hwnd, GWL_EXSTYLE)
    UD.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_TOOLWINDOW)


def set_topmost(hwnd):
    UD.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def strip_dwm_frame(hwnd):
    """关掉系统圆角和边框，用自己的圆角（面板 CSS 里的 14px）。"""
    try:
        dwm = ctypes.windll.dwmapi
        val = ctypes.c_int(1)     # DONOTROUND
        dwm.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                                  ctypes.byref(val), 4)
        val2 = ctypes.c_int(1)    # DWMNCRP_DISABLED
        dwm.DwmSetWindowAttribute(hwnd, DWMWA_NCRENDERING_POLICY,
                                  ctypes.byref(val2), 4)
        # COLOR_NONE
        dwm.DwmSetWindowAttribute(hwnd, DWMWA_BORDER_COLOR,
                                  ctypes.byref(ctypes.c_uint(0xFFFFFFFE)), 4)
    except Exception:
        pass


def fix_transparent(hwnd, ready_check=None, timeout=12.0):
    """pywebview 的透明是延迟生效的。

    它自己的源码注释写的是 "no idea why this works"。实测：光靠它还不够。
    正确的顺序是：

        1. 等页面真的加载完（ready_check 返回 True）
        2. ShowWindow(0) 隐藏
        3. 等 0.5 秒（让 Chromium 完成一次隐藏帧的合成）
        4. ShowWindow(5) 显示 —— 这一帧开始才是真正透明的

    如果不等页面就绪就切，WebView2 还没画第一帧，
    隐藏/显示等于什么都没发生，首帧就是白底（实测）。
    """
    # 1. 等页面就绪
    if ready_check is not None:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if ready_check():
                    break
            except Exception:
                pass
            time.sleep(0.25)

    # 2-4. 隐藏再显示
    UD.ShowWindow(hwnd, 0)     # SW_HIDE
    time.sleep(0.55)
    UD.ShowWindow(hwnd, 5)     # SW_SHOW
    time.sleep(1.4)


# ============================================================ 服务
def port_alive(port, timeout=1.5):
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/health" % port, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_server(port):
    """没有服务就自己起一个（脱离父进程，壳关了它也活着）。"""
    if port_alive(port):
        return True
    subprocess.Popen(
        [sys.executable, os.path.join(SRC, "ui_server.py"), "--port", str(port)],
        cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=DETACHED | NO_WINDOW)
    for _ in range(20):
        time.sleep(0.4)
        if port_alive(port):
            return True
    return False


# ============================================================ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--url", help="直接连已有服务，不起新服务")
    ap.add_argument("--width", type=int, default=DEFAULT_W)
    ap.add_argument("--height", type=int, default=DEFAULT_H)
    ap.add_argument("--page", default="index.html")
    args = ap.parse_args()

    if args.url:
        url = args.url
    else:
        if not ensure_server(args.port):
            print("无法启动 ui_server（端口 %d）" % args.port)
            return 2
        url = "http://127.0.0.1:%d/%s" % (args.port, args.page)

    try:
        import webview
    except ImportError:
        print("缺少 pywebview：python -m pip install pywebview")
        return 2

    print("悬浮窗")
    print("  地址  %s" % url)
    scale = dpi_scale()
    print("  尺寸  %dx%d CSS px（DPI %.0f%% -> 物理 %dx%d）" % (
        args.width, args.height, scale * 100,
        int(args.width * scale), int(args.height * scale)))
    print("  Ctrl+Alt+O 显示/隐藏   Ctrl+Alt+P 点击穿透")
    print()

    win = webview.create_window(
        "RocoOverlay", url=url,
        # 传物理像素：这样 innerWidth 才等于设计尺寸（见 dpi_scale 注释）
        width=int(args.width * scale),
        height=int(args.height * scale),
        x=int(40 * scale), y=int(40 * scale),
        frameless=True,      # 无边框
        transparent=True,    # 像素级透明（圆角外透出桌面）
        on_top=True,         # 置顶
        focus=False,         # 不抢焦点 —— 否则会打断游戏
        easy_drag=False,     # 拖拽自己实现（只有标题栏可拖）
    )

    state = {"hwnd": None, "visible": True, "pass_through": False}

    def page_ready():
        """页面真的画出来了才算就绪。

        判据是面板元素的宽度 —— CSS 加载完成、布局跑过，它才 > 0。
        比 readyState 靠谱：readyState=complete 时可能还没第一次绘制。
        """
        try:
            w = win.evaluate_js(
                "(document.getElementById('panel')||{}).offsetWidth||0")
            return bool(w and int(w) > 100)
        except Exception:
            return False

    def enter_embedded():
        """告诉页面"我在真悬浮窗里"，让它贴左上角、不留预览边距。

        然后按面板实际高度把窗口调成正好包住它 —— 这样不会有大片
        透明空白（透明区域虽然看不见，但会挡住鼠标事件）。
        """
        try:
            win.evaluate_js("document.body.classList.add('embedded')")
        except Exception:
            return
        time.sleep(0.5)
        try:
            h = win.evaluate_js(
                "(document.getElementById('panel')||{}).offsetHeight||0")
            if h and int(h) > 100:
                scale = dpi_scale()
                UD.SetWindowPos(
                    state["hwnd"], -1, 0, 0,
                    int(args.width * scale), int((int(h) + 2) * scale),
                    SWP_NOMOVE | SWP_SHOWWINDOW)
        except Exception:
            pass

    def setup():
        # 轮询等 native 就绪
        hwnd = None
        for _ in range(80):
            time.sleep(0.25)
            try:
                if win.native is not None:
                    hwnd = _root(win.native.Handle.ToInt32())
                    break
            except Exception:
                pass
        if not hwnd:
            print("窗口句柄拿不到，退出")
            return
        state["hwnd"] = hwnd

        # 顺序很重要：先等页面画出来，再修透明
        fix_transparent(hwnd, page_ready)
        add_layered(hwnd)
        hide_from_taskbar(hwnd)
        strip_dwm_frame(hwnd)
        set_topmost(hwnd)
        enter_embedded()
        state["ready"] = True
        print("窗口就绪 hwnd=%d" % hwnd)

    threading.Thread(target=setup, daemon=True).start()

    # ---- 热键（轮询 GetAsyncKeyState，不需要装全局钩子）
    def hotkeys():
        VK_CONTROL, VK_MENU = 0x11, 0x12
        VK_O, VK_P = 0x4F, 0x50
        held = set()
        while True:
            time.sleep(0.06)
            hwnd = state["hwnd"]
            if not hwnd:
                continue
            ctrl = UD.GetAsyncKeyState(VK_CONTROL) & 0x8000
            alt = UD.GetAsyncKeyState(VK_MENU) & 0x8000
            if not (ctrl and alt):
                held.clear()
                continue
            for vk, name in ((VK_O, "toggle"), (VK_P, "pass")):
                down = UD.GetAsyncKeyState(vk) & 0x8000
                if down and name not in held:
                    held.add(name)
                    if name == "toggle":
                        state["visible"] = not state["visible"]
                        UD.ShowWindow(hwnd, 5 if state["visible"] else 0)
                        print("显示" if state["visible"] else "隐藏")
                    else:
                        state["pass_through"] = not state["pass_through"]
                        set_click_through(hwnd, state["pass_through"])
                        print("点击穿透 %s" % (
                            "开" if state["pass_through"] else "关"))
                elif not down:
                    held.discard(name)

    threading.Thread(target=hotkeys, daemon=True).start()

    webview.start()


if __name__ == "__main__":
    sys.exit(main() or 0)
