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
SWP_NOZORDER = 0x0004
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
    """真实 DPI 缩放倍数。

    坑：非 DPI-aware 进程里 GetDeviceCaps / GetDpiForSystem 都返回 96（假的），
    而实际屏幕是 192 DPI（200%）。这时 GetCursorPos 返回逻辑坐标（1536x960），
    但 SetWindowPos 用物理坐标（3072x1920）—— 差 2 倍，
    直接混用会让窗口飞出去或纹丝不动（实测都遇到过）。

    最可靠的判据：物理屏幕尺寸 / 逻辑屏幕尺寸。
    这两个都用不依赖 DPI awareness 的 API 拿。
    """
    try:
        # 物理尺寸（不受 DPI 虚拟化影响）
        phys_w = UD.GetSystemMetrics(0)      # SM_CXSCREEN
        # 用 EnumDisplaySettings 拿真实分辨率
        import ctypes.wintypes as wt

        class DEVMODE(ctypes.Structure):
            _fields_ = [
                ("dmDeviceName", wt.WCHAR * 32),
                ("dmSpecVersion", wt.WORD), ("dmDriverVersion", wt.WORD),
                ("dmSize", wt.WORD), ("dmDriverExtra", wt.WORD),
                ("dmFields", wt.DWORD),
                ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long),
                ("dmDisplayOrientation", wt.DWORD),
                ("dmDisplayFixedOutput", wt.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
                ("dmFormName", wt.WCHAR * 32),
                ("dmLogPixels", wt.WORD), ("dmBitsPerPel", wt.DWORD),
                ("dmPelsWidth", wt.DWORD), ("dmPelsHeight", wt.DWORD),
                ("dmDisplayFlags", wt.DWORD), ("dmDisplayFrequency", wt.DWORD),
                ("dmICMMethod", wt.DWORD), ("dmICMIntent", wt.DWORD),
                ("dmMediaType", wt.DWORD), ("dmDitherType", wt.DWORD),
                ("dmReserved1", wt.DWORD), ("dmReserved2", wt.DWORD),
                ("dmPanningWidth", wt.DWORD), ("dmPanningHeight", wt.DWORD),
            ]

        ENUM_CURRENT_SETTINGS = -1
        dm = DEVMODE()
        dm.dmSize = ctypes.sizeof(DEVMODE)
        if UD.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS,
                                   ctypes.byref(dm)):
            real_w = dm.dmPelsWidth
            if phys_w and real_w:
                ratio = real_w / float(phys_w)
                if ratio >= 1.0:
                    return round(ratio, 2)
    except Exception:
        pass

    # 退回 GetDpiForSystem（DPI-aware 后是真值）
    try:
        dpi = UD.GetDpiForSystem()
        if dpi and dpi != 96:
            return max(1.0, dpi / 96.0)
    except Exception:
        pass
    return 1.0


# 窗口开销（物理像素）：无边框窗口仍有 26x70 的非客户区。
# 实测标定（200% DPI）：
#   create(340,620) -> window 680x1240 phys, inner 327x585 CSS
#   create(400,700) -> window 800x1400 phys, inner 387x665 CSS
#   create(500,900) -> window 1000x1800 phys, inner 487x865 CSS
# 规律：inner_css = create_param - 13（宽）/ - 17.5（高）
#       => create_param = inner_css + 13 / + 17.5
FRAME_W = 13
FRAME_H = 17

# 屏幕高度上限（物理像素）。实测 create 高度超过屏幕会被截到 1950。
MAX_PHYS_H = 1950


def css_size(css_w, css_h):
    """把想要的 CSS 视口尺寸换算成 create_window 的 width/height 参数。

    实测规律：create 参数本身会被 pywebview 乘 dpr，
    再减掉固定的边框开销。所以反推要加回去。
    """
    return (int(css_w + FRAME_W), int(css_h + FRAME_H))


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


def begin_drag(hwnd, dpr=None, sx=None, sy=None):
    """开始拖动窗口。

    坑 1：无边框窗口（frameless=True）**没有 WS_CAPTION**，
          所以 WM_NCLBUTTONDOWN + HTCAPTION 被系统忽略。
    坑 2：坐标口径三套并存且互相差 2 倍（前端 CSS / GetCursorPos 逻辑 /
          SetWindowPos 物理），dpi_scale() 在不同线程里返回值还不一致。
    坑 3：如果这里不记录按下点坐标，第一帧的位移会被吃掉 ——
          实测固定偏移 (-40,-28)，正好是第一帧 20 CSS px × dpr。

    解法：**完全不用光标坐标**。前端传 e.screenX/Y（CSS 像素），
    这里按位移增量乘 dpr 换算成物理像素。起点用窗口 rect。
    """
    try:
        r = ctypes.wintypes.RECT()
        UD.GetWindowRect(hwnd, ctypes.byref(r))
        d = float(dpr or 1.0)
        if d <= 0:
            d = 1.0
        _DRAG["active"] = True
        _DRAG["hwnd"] = hwnd
        _DRAG["dpr"] = d
        # 用按下点做基准（前端传的），不传就等第一次 move
        _DRAG["mouse"] = ((float(sx), float(sy))
                          if sx is not None and sy is not None else None)
        _DRAG["win"] = (r.left, r.top)
        return True
    except Exception:
        return False


_DRAG = {"active": False, "hwnd": None, "mouse": None, "win": (0, 0),
         "dpr": 1.0}

# 由 overlay.main() 注册：{"win": <pywebview window>}。
# 拖动优先走 win.move() —— 它会同步 pywebview 的内部状态，
# 直接 SetWindowPos 有时会被窗口约束挡住。
_WINDOW = {}


def set_drag_pos(hwnd, screen_x=None, screen_y=None):
    """按前端给的屏幕坐标移动窗口。

    screen_x/screen_y 是 e.screenX/Y（CSS 像素）。
    第一次调用时记录基准，之后按增量 × dpr 移动窗口。
    """
    if not _DRAG["active"] or _DRAG["hwnd"] != hwnd:
        return False
    try:
        if screen_x is None or screen_y is None:
            return False
        sx, sy = float(screen_x), float(screen_y)

        if _DRAG["mouse"] is None:
            # 第一次 move：建立基准，窗口不动
            _DRAG["mouse"] = (sx, sy)
            return True

        ox, oy = _DRAG["mouse"]
        wx, wy = _DRAG["win"]
        d = _DRAG.get("dpr") or 1.0

        nx = wx + (sx - ox) * d
        ny = wy + (sy - oy) * d
        # 优先用 pywebview 自己的 move()：它会同步内部状态。
        # 直接 SetWindowPos 有时会被 pywebview 的窗口约束挡住
        # （实测传 HWND_TOP 和 HWND_TOPMOST 都不动）。
        moved = False
        try:
            w = _WINDOW.get("win")
            if w is not None:
                # pywebview 的 move 用 CSS 像素
                w.move(int(nx / d), int(ny / d))
                moved = True
        except Exception:
            moved = False
        if not moved:
            UD.SetWindowPos(hwnd, HWND_TOPMOST, int(nx), int(ny), 0, 0,
                            SWP_NOSIZE | SWP_NOACTIVATE)
        return True
    except Exception:
        return False


def end_drag():
    _DRAG["active"] = False
    return True


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
    print("  尺寸  %dx%d CSS px（DPI %.0f%%）" % (
        args.width, args.height, scale * 100))
    print("  Ctrl+Alt+O 显示/隐藏   Ctrl+Alt+P 点击穿透")
    print()

    # 窗口尺寸标定（见 css_size 注释）：
    #   想要 340x620 CSS 视口，要传 353x638。
    pw, ph = css_size(args.width, args.height)
    print("  参数  %dx%d（目标视口 %dx%d CSS）" % (
        pw, ph, args.width, args.height))

    state = {"hwnd": None, "visible": True, "pass_through": False}

    # ---- 暴露给前端的 API（window.pywebview.api.*）
    class Api:
        """前端能调的原生能力。

        为什么走 API 而不是纯 JS：
        拖动窗口必须让 Windows 接管（WM_NCLBUTTONDOWN），
        点击穿透必须改 WS_EX_TRANSPARENT —— 这两件事 JS 都做不到。
        """

        def drag(self, dpr=None, sx=None, sy=None):
            """开始拖动（前端在 pointerdown 时调）。

            必须带上按下点的 screenX/screenY —— 否则第一帧的位移会被吃掉
            （实测固定偏移 (-40,-28)，就是第一帧那 20 CSS px × dpr 的量）。
            """
            hwnd = state.get("hwnd")
            if not hwnd:
                return False
            return begin_drag(hwnd, dpr, sx, sy)

        def drag_move(self, sx=None, sy=None):
            """拖动中（前端在 pointermove 时调，传 e.screenX / e.screenY）。"""
            hwnd = state.get("hwnd")
            if not hwnd:
                return False
            return set_drag_pos(hwnd, sx, sy)

        def drag_end(self):
            """结束拖动（前端在 pointerup 时调）。"""
            return end_drag()

        def set_click_through(self, enabled):
            hwnd = state.get("hwnd")
            if not hwnd:
                return False
            state["pass_through"] = bool(enabled)
            set_click_through(hwnd, bool(enabled))
            return True

        def set_opacity(self, alpha):
            """整体透明度（0~255）。面板底色透明度由 CSS 控制，
            这个只用于"整体隐身"。"""
            hwnd = state.get("hwnd")
            if not hwnd:
                return False
            try:
                UD.SetLayeredWindowAttributes(hwnd, 0, int(alpha), LWA_ALPHA)
                return True
            except Exception:
                return False

        def hide(self):
            hwnd = state.get("hwnd")
            if hwnd:
                UD.ShowWindow(hwnd, 0)
                state["visible"] = False
            return True

        def toggle_visible(self):
            hwnd = state.get("hwnd")
            if not hwnd:
                return False
            state["visible"] = not state["visible"]
            UD.ShowWindow(hwnd, 5 if state["visible"] else 0)
            return state["visible"]

        def quit(self):
            try:
                win.destroy()
            except Exception:
                pass
            return True

        def fit_window(self):
            """让窗口高度贴合当前内容。

            折叠/展开后必须调这个 —— 否则折叠了窗口还是原来那么高，
            下面一大片透明区域会挡住鼠标（透明区域也是窗口）。

            坑：不能用 panel.scrollHeight —— 面板是 height:100% 的 flex 容器，
            scrollHeight 返回的是**容器高度**而不是内容高度，
            拿它去 resize 会让窗口越缩越大（实测 763 -> 1562）。
            正确做法：累加所有直接子元素的实际高度。
            """
            hwnd = state.get("hwnd")
            if not hwnd:
                return False
            try:
                h = win.evaluate_js("""
                  (() => {
                    const p = document.getElementById('panel');
                    if (!p) return 0;
                    let sum = 0;
                    for (const el of p.children) {
                      const st = getComputedStyle(el);
                      if (st.display === 'none') continue;
                      sum += el.getBoundingClientRect().height;
                    }
                    return Math.round(sum);
                  })()
                """)
                iw = win.evaluate_js("window.innerWidth")
                if not h or int(h) < 40:
                    return False

                css_w = int(iw or 340)
                css_h = int(h) + 2
                screen_css = int(MAX_PHYS_H / max(1.0, dpi_scale()))
                css_h = min(css_h, screen_css - 60)

                pw, ph = css_size(css_w, css_h)
                try:
                    win.resize(pw, ph)
                    return True
                except Exception:
                    pass

                s = dpi_scale()
                UD.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0,
                                int(css_w * s) + 26, int(css_h * s) + 70,
                                SWP_NOMOVE | SWP_SHOWWINDOW)
                return True
            except Exception:
                return False

    api = Api()

    win = webview.create_window(
        "RocoOverlay", url=url,
        width=pw,
        height=ph,
        x=int(40 * scale), y=int(40 * scale),
        frameless=True,      # 无边框
        transparent=True,    # 像素级透明（圆角外透出桌面）
        on_top=True,         # 置顶
        focus=False,         # 不抢焦点 —— 否则会打断游戏
        easy_drag=False,     # 拖拽自己实现（只有标题栏可拖）
        js_api=api,          # 前端可调 window.pywebview.api.*
    )

    # 注册给拖动逻辑用（见 set_drag_pos）
    _WINDOW["win"] = win

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
        """让页面知道"我在真悬浮窗里"，并让窗口贴合内容高度。

        实测面板内容只需约 360~420 CSS px，但默认窗口给 620，
        底部会留一大片透明空白 —— 透明区域虽然看不见，但会挡住鼠标。

        注意：SetWindowPos 只改尺寸，**不要动位置**（SWP_NOMOVE）。
        之前传 (0,0) 把窗口挪到屏幕左上角，和初始位置不一致，
        导致外部截图工具按旧坐标抓，看起来"面板被切"。
        """
        try:
            win.evaluate_js("document.body.classList.add('embedded')")
        except Exception:
            return
        time.sleep(0.6)
        if api.fit_window():
            print("  窗口已贴合内容")

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
