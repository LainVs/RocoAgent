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
import json
import os
import socket
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
SWP_FRAMECHANGED = 0x0020
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
DEFAULT_W = 360
DEFAULT_H = 620


def dpi_scale(hwnd=None):
    """屏幕缩放倍数（仅用于日志/兜底，不参与窗口尺寸计算）。

    **不要在这里调 SetProcessDpiAwarenessContext** ——
    实测那会让随后 create_window 的 width/height 被当成物理像素，
    窗口只有预期的一半大小（340x620 变成 170x310），
    而且从外面完全看不出原因。让进程保持系统默认的 DPI 虚拟化即可。

    ★ 为什么优先用 GetDpiForWindow（2026-11-21 修）
    ------------------------------------------------
    `GetDpiForSystem()` 对**非 DPI-aware 进程**返回的是系统默认 DPI，
    在 200% 缩放下实测返回 **96**（即 scale=1.0），而真实是 2.0。

    这个 1.0 造成了真实故障：
        pin size 用 340 * 1.0 = 340 物理像素传给 SetWindowPos
        而窗口应该是 680 物理像素（340 CSS × 2）
        -> 窗口被砍一半，页面按 2 倍渲染，只显示左上 1/4

    `GetDpiForWindow(hwnd)` 拿的是**该窗口所在显示器**的 DPI，不受进程
    awareness 影响，才是这个场景下正确的值。
    """
    try:
        if hwnd:
            dpi = UD.GetDpiForWindow(hwnd)
            if dpi:
                return max(1.0, dpi / 96.0)
    except Exception:
        pass
    try:
        dpi = UD.GetDpiForSystem()
        if dpi and dpi != 96:
            return max(1.0, dpi / 96.0)
    except Exception:
        pass
    try:
        gdi = ctypes.windll.gdi32
        dc = UD.GetDC(0)
        try:
            dpi = gdi.GetDeviceCaps(dc, 88)     # LOGPIXELSX
        finally:
            UD.ReleaseDC(0, dc)
        if dpi and dpi != 96:
            return max(1.0, dpi / 96.0)
    except Exception:
        pass
    return 1.0


# 窗口开销（**逻辑像素**，不是物理像素）。
# 实测标定（200% DPI，create 和 resize 结果一致）：
#   create(353,637) -> inner 340x602   diff = (13, 35)
#   create(353,486) -> inner 340x451   diff = (13, 35)
#   create(353,972) -> inner 340x937   diff = (13, 35)
#   resize(353,486) -> inner 340x451   diff = (13, 35)
#   resize(340,469) -> inner 327x434   diff = (13, 35)
# 所以 create_param = css + (13, 35)。
# 之前写 FRAME_H=17 是错的（那会把窗口算矮 18px，
# 结果面板底部被裁掉，技能区和替补都看不见）。
FRAME_W = 13
FRAME_H = 35

# 屏幕高度上限（物理像素）。实测 create 高度超过屏幕会被截到 1950。
MAX_PHYS_H = 1950


def css_size(css_w, css_h):
    """把想要的 CSS 视口尺寸换算成 create_window / resize 的参数。

    两者实测换算一致，所以共用一个函数。
    """
    return (int(css_w + FRAME_W), int(css_h + FRAME_H))


# ============================================================ Win32
def _root(hwnd):
    return UD.GetAncestor(hwnd, 2) or hwnd


def add_layered(hwnd, alpha=255):
    """加 WS_EX_LAYERED 并**立即设 alpha**。

    ★ 只加标志是不够的（2026-11-21 找到的真正根因）
    ----------------------------------------------
    `WS_EX_LAYERED` 只是让窗口"可以"用分层属性，但如果不调用
    `SetLayeredWindowAttributes`，系统对这个窗口的默认行为是
    **全不透明**（GetLayeredWindowAttributes 返回 flags=0）。

    实测（修复前）：
        GetLayeredWindowAttributes -> ok=1  alpha=0  flags=0x0
        flags=0 表示 LWA_ALPHA 没启用，alpha 值根本不参与合成。

    表现：CSS 的 rgba(250,249,245,0.42) 算出来是对的，但窗口
    按"完全不透明"合成 —— 面板透出的是 WinForms 的浅灰底
    （#F0F0F0），不是游戏画面。用户看到"一整块白，拉滑块没用"。

    所以必须：加标志 + 设 LWA_ALPHA 255（完全不透明，让 CSS 自己
    控制半透明）。255 是关键 —— 设 0 会让整个窗口消失。
    """
    ex = UD.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if not (ex & WS_EX_LAYERED):
        UD.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
    try:
        UD.SetLayeredWindowAttributes(hwnd, 0, int(alpha), LWA_ALPHA)
    except Exception:
        pass


def set_click_through(hwnd, enabled):
    """开关点击穿透。

    ★ 必须调 SetWindowPos(SWP_FRAMECHANGED)
    --------------------------------------
    只改扩展样式**不够**：窗口已经创建并显示后，`SetWindowLongW(GWL_EXSTYLE)`
    不会让系统重新应用样式，WS_EX_TRANSPARENT 加了/去掉都"没反应"。

    实测（2026-11-21，用户报"关闭点击穿透的快捷键没有效果"）：
    按 Ctrl+Alt+P 之后 GetWindowLongW 里 WS_EX_TRANSPARENT 位确实变了，
    但窗口行为不变 —— 因为系统没重新算窗口的 hit-test。

    修法就是改完样式后调一次 SetWindowPos 带 SWP_FRAMECHANGED。
    """
    ex = UD.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if enabled:
        ex |= WS_EX_TRANSPARENT | WS_EX_LAYERED
    else:
        ex &= ~WS_EX_TRANSPARENT
    UD.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
    # 让系统重新应用扩展样式（关键的一步）
    UD.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                    | SWP_NOACTIVATE | SWP_FRAMECHANGED)


def hide_from_taskbar(hwnd):
    """工具窗口不进 Alt+Tab，也不显示在任务栏。悬浮窗应该这样。"""
    ex = UD.GetWindowLongW(hwnd, GWL_EXSTYLE)
    UD.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_TOOLWINDOW)


def set_topmost(hwnd):
    UD.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def enable_blur_behind(hwnd):
    """给窗口加 DWM 模糊背景（毛玻璃）。

    ★ 为什么走这条路（2026-11-21 彻查后的结论）
    -----------------------------------------
    WebView2 的透明链路在 pywebview 6.x + 本机环境下**不可靠**，
    实测三种组合全失败：

        DefaultBackgroundColor = Transparent
            -> 整个网页画布被挖空，窗口只剩桌面，内容全消失
        DefaultBackgroundColor = 不透明黑
            -> 内容同样不显示
        不设
            -> 透出 Form 的浅灰底 #F0F0F0，看起来就是"纯白不透明"

    DWM blur-behind 是 Windows 原生能力，不依赖 WebView2：
      · 窗口背后区域被模糊后作为背景
      · 窗口内容（HTML）正常绘制，**文字完全不透明**
      · 视觉上就是"毛玻璃"，比清晰透出更像成品

    实现要点：
      1. DWM_BLURBEHIND 结构体，dwFlags = DWM_BB_ENABLE | DWM_BB_BLURREGION
      2. hRgnBlur 传 NULL（全窗口模糊），而不是 CreateRectRgn(-1,-1,0,0)
         —— 后者是"只模糊边缘"的老写法，Win10 之后语义变了
      3. 必须配合 WS_EX_LAYERED（pywebview 已加）
      4. Win11 可以进一步用 DWMWA_SYSTEMBACKDROP_TYPE = 3 (Acrylic)，
         但兼容性不如 blur-behind，先只用后者

    返回是否成功。
    """
    try:
        dwm = ctypes.windll.dwmapi
        wt = ctypes.wintypes

        class DWM_BLURBEHIND(ctypes.Structure):
            _fields_ = [
                ("dwFlags", wt.DWORD),
                ("fEnable", wt.BOOL),
                ("hRgnBlur", wt.HANDLE),
                ("fTransitionOnMaximized", wt.BOOL),
            ]

        DWM_BB_ENABLE = 0x1
        DWM_BB_BLURREGION = 0x2

        bb = DWM_BLURBEHIND()
        bb.dwFlags = DWM_BB_ENABLE | DWM_BB_BLURREGION
        bb.fEnable = True
        bb.hRgnBlur = None          # NULL = 整个窗口
        bb.fTransitionOnMaximized = False

        hr = dwm.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(bb))
        ok = (hr == 0)
        _log("enable_blur_behind: hr=0x%08x %s" % (hr, "OK" if ok else "FAIL"))
        return ok
    except Exception as e:
        _log("enable_blur_behind 失败: %s: %s" % (type(e).__name__, e))
        return False


def set_acrylic(hwnd):
    """Win11 亚克力（acrylic）。失败返回 False，不抛异常。

    DWMWA_SYSTEMBACKDROP_TYPE = 38
    取值：0=Auto 1=None 2=Mica 3=Acrylic 4=Tabbed

    Win10 不支持这个属性，会返回非 0，调用方忽略即可。
    """
    try:
        dwm = ctypes.windll.dwmapi
        DWMWA_SYSTEMBACKDROP_TYPE = 38
        val = ctypes.c_int(3)      # Acrylic
        hr = dwm.DwmSetWindowAttribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE,
                                       ctypes.byref(val), 4)
        ok = (hr == 0)
        _log("set_acrylic: hr=0x%08x %s" % (hr, "OK" if ok else "FAIL"))
        return ok
    except Exception as e:
        _log("set_acrylic 失败: %s: %s" % (type(e).__name__, e))
        return False


def make_form_transparent(win):
    """把 WinForms Form 和 WebView2 控件的底色设成透明，让 CSS 的 rgba 生效。

    ★ 三次踩坑记录（2026-11-21，全部实测确认）
    -----------------------------------------

    坑 1：pywebview 的 transparent=True **不设 Form 的 BackColor**
        if window.transparent and self.browser:
            self.SetStyle(SupportsTransparentBackColor, True)
            self.browser.DefaultBackgroundColor = Color.Transparent
        else:
            self.BackColor = ColorTranslator.FromHtml(window.background_color)
        注意 else —— transparent 时 Form.BackColor 保持 WinForms 默认
        的 Control 色（#F0F0F0 浅灰）。
        实测：Form.BackColor = A255 R240 G240 B240

    坑 2：`DefaultBackgroundColor = Transparent` 会把**整个网页画布挖空**
        实测：设成 Transparent 后窗口只剩桌面，面板内容全没了。
        正确的组合是：
            Form/WebView2.BackColor   = Transparent   控件底色透明
            DefaultBackgroundColor    = 不透明         网页照常绘制
            CSS 的 rgba(...)                           半透明效果

    坑 3：`DefaultBackgroundColor` **只能在 UI 线程访问**
        在后台线程读写会抛：
            InvalidOperationException: CoreWebView2Controller members
            can only be accessed from the UI thread
        所以这个函数必须从 UI 线程调用 —— 用 pywebview 的 loaded 事件，
        它在主线程（UI 线程）触发。

    返回值：成功设到 WebView2 时 True。
    """
    try:
        from System.Drawing import Color

        form = getattr(win, "native", None)
        if form is None:
            return False

        # 1. Form：先开样式再设颜色（顺序不能反，否则 WinForms 忽略）
        try:
            from System.Windows.Forms import ControlStyles
            form.SetStyle(ControlStyles.SupportsTransparentBackColor, True)
        except Exception:
            pass
        form.BackColor = Color.Transparent

        # 2. 只设 BackColor（不碰 DefaultBackgroundColor）
        #
        # ★ 为什么不用 DefaultBackgroundColor（2026-11-21 实测）
        # -----------------------------------------------------
        # 它是 CoreWebView2Controller 的属性，**只能在 UI 线程访问**。
        # 从后台线程调用会：
        #   1. 抛 InvalidOperationException（UI 线程检查）
        #   2. 或者在 pythonnet 的 COM 编组里**死锁**
        #      （实测日志停在调用之后，page_ready 永不返回）
        #
        # 而设成 Transparent 会把整个网页画布挖空（内容全消失），
        # 设成不透明又覆盖页面 —— 两头都不对。
        #
        # 所以完全放弃这条路，改用 DWM blur-behind（见 enable_blur_behind）。
        touched = False
        try:
            n = form.Controls.Count
            for i in range(n):
                c = form.Controls[i]
                try:
                    c.BackColor = Color.Transparent
                    touched = True
                except Exception:
                    pass
        except Exception:
            pass

        _log("make_form_transparent: Form 透明=%s  WebView2.BackColor=%s"
             % (True, touched))
        return touched
    except Exception as e:
        _log("make_form_transparent 失败: %s: %s" % (type(e).__name__, e))
        return False


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


# ---------------------------------------------------------------- 位置持久化
# 为什么要自己存：pywebview 不记住无边框窗口的位置。
# 用户把面板拖到屏幕右侧，关掉重开又回到左上角 —— 每次都要重新摆。
# 存在 config/overlay.json，跟着仓库走，换机器也能带走。
_STATE_FILE = os.path.join(ROOT, "config", "overlay.json")

# 调试日志：写到 logs/overlay.log。
# 为什么需要：overlay 是脱离父进程跑的（creationflags=DETACHED），
# stdout 拿不到，出问题时完全看不到内部状态。
# 实测踩过：resize 明明该生效却没生效，加了日志才发现是别的原因。
_LOG_FILE = os.path.join(ROOT, "logs", "overlay.log")


def _log(msg):
    try:
        os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def _rect_str(hwnd):
    """窗口当前 rect，日志用。"""
    try:
        r = ctypes.wintypes.RECT()
        UD.GetWindowRect(hwnd, ctypes.byref(r))
        return "%dx%d" % (r.right - r.left, r.bottom - r.top)
    except Exception:
        return "?"


def clamp_to_virtual_screen(css_x, css_y, css_w, css_h, scale=None):
    """把 CSS 坐标的窗口夹回虚拟桌面里。返回 (x, y)（CSS）。

    `scale` 参数保留只为兼容旧调用方 —— 当前实现**不需要**它，
    因为 GetWindowRect 和 GetSystemMetrics 在这台机器上都是逻辑像素，
    同一口径直接比即可（详见下方口径说明）。

    为什么要自愈
    ------------
    位置是从 `config/overlay.json` 恢复的。换显示器、改分辨率、或者
    上一次存了个坏值，都会让窗口落在**虚拟桌面之外** —— 表现是
    「任务栏里看得到、屏幕上找不到」，用户点任务栏也调不出来。
    与其指望用户去删配置文件，不如自己挪回来。

    判定：把窗口换算成物理像素，要求它和虚拟桌面有至少 80px 的交集。

    ★ 口径（实测确认，2026-11-21）
    ------------------------------
    这台机器上，`GetWindowRect` 和 `GetSystemMetrics(SM_CXVIRTUALSCREEN)`
    **都返回逻辑像素**，两个视角（DPI-aware / unaware）结果完全一致：

        rect.left = -286        （逻辑；物理是 -572）
        SM_CXVIRTUALSCREEN = 1536   （逻辑；物理是 3072）

    所以两边**同一口径，直接比，不要乘 scale**。
    早期版本乘了 scale，把逻辑 1536 变成 3072，于是合法的 x=2000
    也被当成"在屏幕内"放过去了；反向的错也犯过（拿物理比逻辑，
    把合法位置判成越界）。

    参数 css_w/css_h 是窗口的 CSS 尺寸，和 rect 同口径。
    """
    try:
        SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
        SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
        vx = UD.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = UD.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = UD.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = UD.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if vw <= 0 or vh <= 0:
            return css_x, css_y
        px, py = int(css_x), int(css_y)
        pw = max(1, int(css_w))
        ph = max(1, int(css_h))
        # 要求窗口至少有 margin×margin 落在屏幕里，**且左上角不能为负太多**
        #
        # 只判"有交集"不够：实测配置里存过 (-6,-8)，窗口贴着屏幕左上角
        # 外面一点点 —— 有交集所以放行，但标题栏被切掉，拖动体验很差。
        margin = 80
        overlap_x = min(px + pw, vx + vw) - max(px, vx)
        overlap_y = min(py + ph, vy + vh) - max(py, vy)
        # 左上角最多允许露出 -8px（视觉上贴边，但不切标题栏）
        edge_ok = (px >= vx - 8) and (py >= vy - 8)
        if overlap_x >= margin and overlap_y >= margin and edge_ok:
            return css_x, css_y
        _log("pos clamp: (%d,%d) 不可用（逻辑桌面 %dx%d @%d,%d，"
             "交集 %dx%d），回到左上角"
             % (px, py, vw, vh, vx, vy, overlap_x, overlap_y))
        return max(vx + 40, 40), max(vy + 40, 40)
    except Exception:
        return css_x, css_y


def load_window_state():
    """读窗口状态。

    ★ 必须用 utf-8-sig
    -------------------
    用户（或脚本）手写这个文件时很容易带 BOM —— 实测 PowerShell 的
    `Set-Content -Encoding UTF8`（5.1）就会写 BOM。

    用 `encoding="utf-8"` 读带 BOM 的文件，`json.load` 会抛
    `Unexpected UTF-8 BOM`，而这里以前是 `except: return {}` ——
    表现是**配置被静默忽略**，窗口位置永远回到默认值。

    utf-8-sig 能读带 BOM 和不带 BOM 两种，没有副作用。
    """
    try:
        with open(_STATE_FILE, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        out = {}
        for k in ("x", "y", "w", "h"):
            v = d.get(k)
            if isinstance(v, (int, float)):
                out[k] = int(v)
        if d.get("folded") is not None:
            out["folded"] = bool(d["folded"])
        return out
    except Exception:
        return {}


def save_window_state(**kw):
    """写窗口状态。原子替换，失败静默（不能因为存不了位置就让程序出错）。"""
    try:
        cur = load_window_state()
        cur.update({k: v for k, v in kw.items() if v is not None})
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _STATE_FILE)
        return True
    except Exception:
        return False


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

        # ★ 不做 dpr 换算
        #
        # 前端传的是 e.screenX/Y（CSS 像素），窗口 rect 也是同一口径
        # （实测：SetWindowPos(400) -> GetWindowRect 读回 400，
        #  两个视角一致；屏幕 1536x960 也是逻辑值）。
        # 所以位移直接相加即可。
        #
        # 以前这里写 `w.move(int(nx / d), int(ny / d))`，除以 dpr=2，
        # 结果是窗口只走鼠标的一半距离 —— 这也是"拖动诡异"的一部分。
        nx = wx + (sx - ox)
        ny = wy + (sy - oy)

        # 直接 SetWindowPos：pywebview 的 move() 会同步内部状态但
        # 实测在有约束时不动。SetWindowPos 是唯一可靠的方式。
        # hWndInsertAfter 传 0（HWND_TOP）而不是 HWND_TOPMOST ——
        # 传 -1 且已是 TOPMOST 时尺寸/位置参数会被忽略。
        UD.SetWindowPos(hwnd, 0, int(nx), int(ny), 0, 0,
                        SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOZORDER)
        return True
    except Exception:
        return False


def end_drag():
    _DRAG["active"] = False
    return True


def fix_transparent(hwnd, ready_check=None, timeout=12.0, on_ready=None):
    """pywebview 的透明是延迟生效的。

    它自己的源码注释写的是 "no idea why this works"。实测：光靠它还不够。
    正确的顺序是：

        1. 等页面真的加载完（ready_check 返回 True）
        2. ShowWindow(0) 隐藏
        3. 等 0.5 秒（让 Chromium 完成一次隐藏帧的合成）
        4. ShowWindow(5) 显示 —— 这一帧开始才是真正透明的

    如果不等页面就绪就切，WebView2 还没画第一帧，
    隐藏/显示等于什么都没发生，首帧就是白底（实测）。

    on_ready: 页面就绪时的回调。用来设 WebView2 控件的透明背景 ——
              实测那时控件才真正创建出来（之前 form.Controls 是空的）。
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

    # ★ 页面就绪后立刻回调 —— 此时 WebView2 控件才创建好
    if on_ready is not None:
        try:
            on_ready()
        except Exception as e:
            _log("fix_transparent on_ready 失败: %s: %s"
                 % (type(e).__name__, e))

    # 2-4. 隐藏再显示
    UD.ShowWindow(hwnd, 0)     # SW_HIDE
    time.sleep(0.55)
    UD.ShowWindow(hwnd, 5)     # SW_SHOW
    time.sleep(1.4)


# ============================================================ 服务
def port_alive(port, timeout=1.5):
    """服务活着，并且静态资源真的能拿到。

    只看 /api/health 不够：实测服务进程在但 index.html 404/超时的情况
    （端口被别的进程占了），此时 overlay 会白屏。
    """
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/health" % port, timeout=timeout) as r:
            if r.status != 200:
                return False
    except Exception:
        return False
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/index.html" % port,
                timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _port_free(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def ensure_server(port):
    """没有服务就自己起一个（脱离父进程，壳关了它也活着）。

    端口被别的进程占着（而且不是我们的服务）时换一个 ——
    实测这种情况会让 overlay 白屏，而且从外面完全看不出原因。
    返回实际使用的端口，失败返回 None。
    """
    if port_alive(port):
        _log("ensure_server: already up on %d" % port)
        return port

    if not _port_free(port):
        for p in range(port + 1, port + 20):
            if _port_free(p):
                _log("ensure_server: %d busy, using %d" % (port, p))
                port = p
                break
        else:
            _log("ensure_server: no free port near %d" % port)
            return None

    _log("ensure_server: starting on %d" % port)
    try:
        subprocess.Popen(
            [sys.executable, os.path.join(SRC, "ui_server.py"),
             "--port", str(port)],
            cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=DETACHED | NO_WINDOW)
    except Exception as e:
        _log("ensure_server: spawn FAIL %s" % e)
        return None
    for i in range(40):
        time.sleep(0.4)
        if port_alive(port):
            _log("ensure_server: up after %.1fs on %d"
                 % ((i + 1) * 0.4, port))
            return port
    _log("ensure_server: TIMEOUT after 16s")
    return None


# ============================================================ 主流程
def _pid_alive(pid):
    """进程是否还活着（Windows 安全版）。

    为什么不用 os.kill(pid, 0)
    ---------------------------
    CPython 在 Windows 上把 os.kill 实现成
        OpenProcess(...) -> TerminateProcess(handle, sig)
    也就是说 `os.kill(pid, 0)` 会**真的把那个进程杀掉**（退出码 0）。
    所以这里必须走 OpenProcess + GetExitCodeProcess。
    """
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    try:
        k = ctypes.windll.kernel32
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    except Exception:
        return False


def _single_instance(pidfile):
    """保证只有一个悬浮窗。

    桌面快捷方式很容易被连点两次，以前的结果是**开出两个悬浮窗**叠在一起
    （ui.py 只复用服务，不复用 overlay）。这里用 pid 文件挡住第二次。

    返回 None 表示可以继续；返回 pid 表示已经有一个在跑。
    """
    try:
        with open(pidfile, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except Exception:
        pid = 0
    if pid > 0 and _pid_alive(pid):
        return pid
    try:
        os.makedirs(os.path.dirname(pidfile), exist_ok=True)
        with open(pidfile, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--url", help="直接连已有服务，不起新服务")
    ap.add_argument("--width", type=int, default=DEFAULT_W)
    ap.add_argument("--height", type=int, default=DEFAULT_H)
    ap.add_argument("--page", default="index.html")
    ap.add_argument("--alpha", type=int, default=0,
                    help="调试用：启动时直接应用透明度（40~100），"
                         "0 表示用页面保存的值")
    args = ap.parse_args()

    pidfile = os.path.join(ROOT, "logs", "overlay.pid")
    running = _single_instance(pidfile)
    if running:
        print("悬浮窗已在运行（pid %d），不重复启动。" % running)
        print("  要重启就先结束它，或删除 %s" % pidfile)
        return 0
    try:
        return _run(args)
    finally:
        try:
            # 只删自己写的，避免误删另一个实例的
            with open(pidfile, encoding="utf-8") as f:
                if int(f.read().strip()) == os.getpid():
                    os.remove(pidfile)
        except Exception:
            pass


def _run(args):
    if args.url:
        url = args.url
    else:
        port = ensure_server(args.port)
        if not port:
            print("无法启动 ui_server（端口 %d）" % args.port)
            return 2
        url = "http://127.0.0.1:%d/%s" % (port, args.page)

    try:
        import webview
    except ImportError:
        # 本机实测（2026-11-21）：这两个解释器的能力是**分裂**的 ——
        #   .venv（AGENTS.md 指定的 $py）有 bleak，但**没有** pywebview
        #   系统 Python 的用户 site-packages 有 pywebview，但**没有** bleak
        # 所以用 $py 跑悬浮窗会走到这里。不写清楚的话，表现是
        # "命令跑完了，可是没有窗口"，很难判断是崩了还是没启动。
        sp = os.path.join(os.environ.get("APPDATA", ""), "Python",
                          "Python%d%d" % sys.version_info[:2], "site-packages")
        print("缺少 pywebview，悬浮窗起不来。")
        print("  当前解释器: %s" % sys.executable)
        print("  pywebview 装在: %s" % sp)
        print("  两种解法：")
        print("    1) 界面用系统 Python 开：  python src\\ui.py")
        print("       （机器人仍然必须用 .venv，因为它需要 bleak）")
        print("    2) 给 venv 补上：          <venv>\\Scripts\\python.exe "
              "-m pip install pywebview")
        return 2

    print("悬浮窗")
    print("  地址  %s" % url)
    # 这里还没创建窗口，拿不到 GetDpiForWindow。dpi_scale() 会退回
    # GetDpiForSystem，对非 DPI-aware 进程可能报 96（显示 100%）。
    # 所以只在真拿到非 1.0 时才报 DPI，否则不瞎猜。
    scale = dpi_scale()
    if abs(scale - 1.0) > 0.01:
        print("  尺寸  %dx%d CSS px（DPI %.0f%%）" % (
            args.width, args.height, scale * 100))
    else:
        print("  尺寸  %dx%d CSS px" % (args.width, args.height))
    print("  Ctrl+Alt+O 显示/隐藏   Ctrl+Alt+P 点击穿透")
    print()

    # 窗口尺寸标定（见 css_size 注释）：
    #   想要 340x620 CSS 视口，要传 353x638。
    #
    # 高度给足：运行时 resize 在这台机器上口径混乱（实测越调越乱），
    # 所以 create 时就按"4 张技能卡 + 替补"的最大可能高度算好，
    # 之后只允许缩小（fit_window 里 min(需要, create_h)）。
    pw, ph = css_size(args.width, args.height)
    state_h_max = ph

    # 恢复上次的窗口位置和尺寸（pywebview 自己不记无边框窗口的位置）
    #
    # ★ 坐标口径（踩过两次，2026-11-21）
    #   config/overlay.json 里的 x/y 是 **CSS 像素**（= 逻辑像素）。
    #
    #   第一次踩：以为是物理像素，读的时候 /scale —— 结果又缩了一次。
    #   第二次踩：drag_end 存 GetWindowRect，而这个进程**不是 DPI-aware**，
    #             GetWindowRect 报的已经是逻辑值，却被当成物理值存下来，
    #             启动时再 /scale 就偏了一半。
    #
    #   现在统一：**存什么读什么，不乘不除**。宽度也一样（css_w + FRAME_W）。
    saved = load_window_state()
    pos_x = saved.get("x", 40)
    pos_y = saved.get("y", 40)
    if saved.get("w"):
        pw = saved["w"]
    if saved.get("h"):
        ph = saved["h"]
    clamped_x, clamped_y = clamp_to_virtual_screen(pos_x, pos_y, pw, ph,
                                                   scale)
    if (clamped_x, clamped_y) != (pos_x, pos_y):
        # 自愈的结果要**写回去**，否则坏值永远留在配置里，
        # 每次启动都要再夹一次（而且用户拖到新位置后又会混进旧坏值）。
        pos_x, pos_y = clamped_x, clamped_y
        save_window_state(x=pos_x, y=pos_y)
        _log("pos clamp: 已把修正后的位置 (%d,%d) 写回配置"
             % (pos_x, pos_y))
    else:
        pos_x, pos_y = clamped_x, clamped_y

    print("  参数  %dx%d（目标视口 %dx%d CSS）" % (
        pw, ph, args.width, args.height))
    if saved:
        print("  恢复  位置 (%d,%d)  尺寸 %dx%d" % (pos_x, pos_y, pw, ph))
    _log("start: pos=(%d,%d) size=(%d,%d) saved=%s"
         % (pos_x, pos_y, pw, ph, bool(saved)))

    state = {"hwnd": None, "visible": True, "pass_through": False,
             "css_w": args.width, "dpr": 1.0, "create_h": state_h_max,
             "folded": bool(saved.get("folded"))}

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
            """结束拖动，把窗口位置存下来（下次启动恢复）。"""
            hwnd = state.get("hwnd")
            ok = end_drag()
            if not hwnd:
                return ok
            try:
                # ★ 只在**真的移动过**才保存
                #
                # 实测（2026-11-21）：drag_end 在一次会话里被触发了 5 次，
                # 而用户根本没拖窗口 —— 前端 pointerup 只要经过页头就会调过来。
                # 每次都写盘，位置被写成各种中间值（78,316 / 80,288 / 138,296）。
                r = ctypes.wintypes.RECT()
                UD.GetWindowRect(hwnd, ctypes.byref(r))
                start = _DRAG.get("win")
                moved = True
                if start and _DRAG.get("active") is False and start != (0, 0):
                    moved = (abs(r.left - start[0]) > 2
                             or abs(r.top - start[1]) > 2)
                if moved:
                    # 存的是 CSS（= 逻辑）像素，和启动时读取口径一致，
                    # 不乘不除。详见 clamp_to_virtual_screen 的口径说明。
                    save_window_state(x=r.left, y=r.top)
                    _log("drag_end: saved css pos (%d,%d)" % (r.left, r.top))
                else:
                    _log("drag_end: 没移动，不保存")
            except Exception:
                pass
            return ok
            return ok

        def set_click_through(self, enabled):
            hwnd = state.get("hwnd")
            if not hwnd:
                return False
            state["pass_through"] = bool(enabled)
            set_click_through(hwnd, bool(enabled))
            return True

        def get_state(self):
            """前端用来同步原生侧的状态。

            为什么需要：全局热键 Ctrl+Alt+O / Ctrl+Alt+P 是在**原生线程**
            里处理的，页面完全不知道。不同步的话会出现"穿透已经关了，
            但按钮还写着已穿透"这种自相矛盾的显示。

            顺带返回**页面侧的诊断信息**（dom），排查"透明度没效果"时
            从外部就能看到 --alpha 到底是多少、embedded 类有没有加上。
            """
            hwnd = state.get("hwnd")
            ex = 0
            if hwnd:
                try:
                    ex = UD.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
                except Exception:
                    ex = 0
            out = {
                "visible": bool(state.get("visible", True)),
                "pass_through": bool(ex & WS_EX_TRANSPARENT),
                "opacity": int(state.get("opacity", 255)),
                "folded": bool(state.get("folded")),
            }
            # 诊断：读页面真实状态
            try:
                w = _WINDOW.get("win")
                if w is not None:
                    out["dom"] = w.evaluate_js("""
                      (() => {
                        const root = document.documentElement;
                        const cs = getComputedStyle(root);
                        const p = document.getElementById('panel');
                        return {
                          bodyClass: document.body.className,
                          alpha: cs.getPropertyValue('--alpha').trim(),
                          bgRgb: cs.getPropertyValue('--bg-rgb').trim(),
                          panelBg: p ? getComputedStyle(p).backgroundColor : null,
                          stageDisplay: (() => {
                            const s = document.getElementById('stage');
                            return s ? getComputedStyle(s).display : null;
                          })(),
                          innerW: window.innerWidth,
                          innerH: window.innerHeight,
                        };
                      })()
                    """)
            except Exception as e:
                out["dom_error"] = "%s: %s" % (type(e).__name__, e)
            return out

        def set_opacity(self, alpha):
            """整体透明度（0~255）。

            ★ 这是用户真正感知到的"透明度"
            ---------------------------------
            之前只有 CSS 的 `--alpha` 在变，而它只作用于 `.panel` 的
            background —— 窗口本身仍然完全不透明，所以用户把滑块拉到
            20% 也看不出变化（实测确认：整窗 alpha 一直是 255）。

            真正让"整窗"变透明必须走分层窗口：
                SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)

            前提是窗口带 WS_EX_LAYERED。transparent=True 建的窗口一般
            已经带了，但这里仍然兜底加一次（add_layered），否则
            SetLayeredWindowAttributes 会直接返回 0 失败。

            下限保护：alpha 太低会让窗口完全看不见、连热键都不知道该
            按什么。夹到 40（约 16%）。
            """
            hwnd = state.get("hwnd")
            if not hwnd:
                return False
            try:
                # 下限 180（约 71%）。
                #
                # 实测（2026-11-21，浅色主题压深色背景）：
                #   alpha=255 -> 面板 246（不透明）
                #   alpha=200 -> 面板 198（半透明，背后清晰可辨）✓
                #   alpha=180 -> 面板 181（较透明，文字仍可读）
                #   alpha=150 -> 面板 154（过度透明，文字糊在背景里）✗
                #
                # 150 那张截图里面板几乎消失，所以下限提到 180。
                a = max(180, min(255, int(alpha)))
                add_layered(hwnd)
                ok = UD.SetLayeredWindowAttributes(hwnd, 0, a, LWA_ALPHA)
                state["opacity"] = a
                _log("set_opacity %d -> %s" % (a, "ok" if ok else "FAIL"))
                return bool(ok)
            except Exception as e:
                _log("set_opacity error: %s" % e)
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

        def set_folded(self, folded):
            """记录折叠状态，下次启动直接恢复（避免展开态闪一下再折叠）。"""
            state["folded"] = bool(folded)
            save_window_state(folded=bool(folded))
            return True

        def quit(self):
            try:
                win.destroy()
            except Exception:
                pass
            return True

        def fit_window(self, force=False):
            """让窗口高度贴合当前内容（force=True 跳过去抖）。

            坑 1：不能用 panel.scrollHeight —— 面板是 height:100% 的 flex 容器，
                  scrollHeight 返回**容器高度**而不是内容高度。
                  正确做法：累加所有直接子元素的实际高度。
            坑 2：SetWindowPos 改尺寸会被 pywebview 的窗口约束挡住，
                  必须用 win.resize()。
            坑 3：不能每帧都 resize（会闪）。但也不能只用阈值去抖 ——
                  内容从"无技能"变"4 张卡"时高度变化很大，
                  而窗口当时可能已经偏小，去抖会把调整吞掉。
                  所以 force=True 时无条件重算。
            """
            hwnd = state.get("hwnd")
            if not hwnd:
                return False
            # 拖动中不重算尺寸：页面在动，测量会量到半截（实测 153）。
            if _DRAG.get("active"):
                _log("fit_window skipped (dragging)")
                return False
            _log("fit_window(force=%s) in, rect=%s"
                 % (force, _rect_str(hwnd)))
            try:
                # ★ 测量前必须临时解除"面板撑满视口"的约束
                #
                # 踩过的坑（2026-11-21，用户报"透明度无效果"+ 内容被裁）：
                # body.embedded .panel 是 { min-height:100%; display:flex;
                # flex-direction:column }，而且 .side/.skills/.verdict 都设了
                # flex:none —— 但 panel 的 min-height:100% 会让它**跟着窗口高度**。
                #
                # 于是形成死循环：
                #   窗口矮 -> panel 只有那么高 -> 子元素被压扁 -> 量出 327
                #   -> 窗口保持 327 -> 下次还是 327
                #
                # 必须先把 panel 的高度约束去掉、改成 auto，让它按内容自然撑开，
                # 量到的才是"内容真实需要多高"。
                h = win.evaluate_js("""
                  (() => {
                    const p = document.getElementById('panel');
                    if (!p) return 0;
                    // 临时解除约束
                    const prevMin = p.style.minHeight;
                    const prevH = p.style.height;
                    const prevFlex = p.style.flex;
                    p.style.minHeight = '0';
                    p.style.height = 'auto';
                    p.style.flex = '0 0 auto';

                    let sum = 0;
                    for (const el of p.children) {
                      const st = getComputedStyle(el);
                      if (st.display === 'none') continue;
                      sum += el.getBoundingClientRect().height;
                    }
                    // 加上 panel 自己的 padding/border（当前是 0，但别假设）
                    const cs = getComputedStyle(p);
                    sum += parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
                    sum += parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);

                    // 还原
                    p.style.minHeight = prevMin;
                    p.style.height = prevH;
                    p.style.flex = prevFlex;
                    return Math.round(sum);
                  })()
                """)
                if not h or int(h) < 40:
                    # embedded 刚加时布局可能还没完成，等一帧再试一次。
                    time.sleep(0.25)
                    h = win.evaluate_js("""
                      (() => {
                        const p = document.getElementById('panel');
                        if (!p) return 0;
                        const prevMin = p.style.minHeight;
                        p.style.minHeight = '0';
                        p.style.height = 'auto';
                        let sum = 0;
                        for (const el of p.children) {
                          const st = getComputedStyle(el);
                          if (st.display === 'none') continue;
                          sum += el.getBoundingClientRect().height;
                        }
                        p.style.minHeight = prevMin;
                        p.style.height = '';
                        return Math.round(sum);
                      })()
                    """)
                if not h or int(h) < 40:
                    _log("fit: measured=%s (too small, skip)" % h)
                    return False

                # ★ 合理下限：面板最矮也有页头 + 一张卡 ≈ 200 CSS px。
                #
                # 实测（2026-11-21）：拖动过程中页面会重排，某次 fit 量到
                # **153**，窗口被缩成 153 高，整块内容被裁掉。
                # 40 这个下限只挡住"完全没渲染"，挡不住这种"量到半截"。
                #
                # 这里不直接拒绝（那样遇到真的紧凑布局会永远调不下来），
                # 而是记下来并**保持当前高度**，等下一次测量。
                if int(h) < 200 and state.get("css_h"):
                    _log("fit: measured=%s 明显偏小（当前 %s），跳过"
                         % (h, state.get("css_h")))
                    return False

                # 宽度固定用启动时的值，**绝不能读 window.innerWidth**：
                # resize 之后 innerWidth 会变，再拿它去算会形成正反馈，
                # 窗口越来越宽（实测 509 -> 639 -> … 无限增长）。
                css_w = state.get("css_w") or args.width
                css_h = int(h) + 2
                # 倍率来源优先级（单一事实来源原则）：
                #   1. winprobe 实测的 device/css（启动时用 CSS 视口标定）
                #   2. 页面 devicePixelRatio
                #   3. 1.0
                #
                # 为什么不用 dpi_scale()：它在 overlay 进程里实测返回 1.0
                # （GetDpiForSystem 对非 DPI-aware 进程报 96），不可靠。
                s = 0.0
                try:
                    import winprobe
                    s = float(winprobe.device_scale(
                        winprobe.probe(hwnd)) or 0)
                except Exception:
                    s = 0.0
                if s < 1.5:
                    try:
                        s = float(win.evaluate_js(
                            "window.devicePixelRatio || 1") or 1.0)
                        _log("fit: lazy dpr=%.1f" % s)
                    except Exception:
                        s = 1.0
                if s < 1.0:
                    s = 1.0
                screen_css = int(MAX_PHYS_H / max(1.0, s))
                css_h = min(css_h, screen_css - 60)

                prev = state.get("css_h")
                if not force and prev is not None and abs(css_h - prev) <= 4:
                    return True
                state["css_h"] = css_h

                # 用 SetWindowPos 而不是 win.resize()：
                # 实测 win.resize() 在透明+无焦点的窗口上会**阻塞**。
                #
                # SetWindowPos 的尺寸是**物理像素**：
                #   传 340  -> 窗口 340 物理 = 170 CSS（只有一半，面板被裁）
                #   传 680  -> 窗口 680 物理 = 340 CSS（正确）
                # 之前误以为它和 create_window 一样是 CSS 口径，
                # 结果每次 fit 都把窗口砍一半（实测 680x1239 -> 400x503）。
                pw = int(css_w * s)
                ph = int(css_h * s)
                try:
                    cur = ctypes.wintypes.RECT()
                    UD.GetWindowRect(hwnd, ctypes.byref(cur))
                    # 关键：hWndInsertAfter 必须传 0（HWND_TOP）而不是
                    # HWND_TOPMOST —— 传 -1 且窗口已是 TOPMOST 时，
                    # 实测尺寸参数被系统忽略（窗口纹丝不动）。
                    # 置顶已经在 set_topmost() 里做过了，这里只管尺寸。
                    UD.SetWindowPos(hwnd, 0, cur.left, cur.top, pw, ph,
                                    SWP_NOZORDER | SWP_NOACTIVATE
                                    | SWP_SHOWWINDOW)
                    save_window_state(w=int(css_w) + FRAME_W,
                                      h=css_h + FRAME_H)
                    _log("fit: css_w=%s css_h=%s dpr=%s -> SetWindowPos(%d,%d) -> rect=%s"
                         % (css_w, css_h, s, pw, ph, _rect_str(hwnd)))
                    return True
                except Exception as e:
                    _log("SetWindowPos FAIL: %s: %s" % (type(e).__name__, e))
                    return False
            except Exception:
                return False

    api = Api()

    win = webview.create_window(
        "RocoOverlay", url=url,
        width=pw,
        height=ph,
        x=pos_x, y=pos_y,
        frameless=True,      # 无边框
        transparent=True,    # 像素级透明（圆角外透出桌面）
        on_top=True,         # 置顶
        focus=False,         # 不抢焦点 —— 否则会打断游戏
        easy_drag=False,     # 拖拽自己实现（只有标题栏可拖）
        js_api=api,          # 前端可调 window.pywebview.api.*
        # ★ background_color 必须是 6 位 hex triplet
        #
        # 踩过的坑（2026-11-21）：
        #   1. 默认值是 '#FFFFFF' —— WebView2 在页面下面画白底。
        #      面板 rgba(9,11,15,0.62) 压在白底上变成均匀中灰
        #      （0.62×9 + 0.38×255 ≈ 105），用户看到的就是"一整块灰"。
        #   2. 想写 8 位带 alpha 的 '#00000000' -> pywebview 直接抛
        #      ValueError: not a valid hex triplet。
        #
        # 所以只能用 6 位。用纯黑而不是白色：黑色作为"透明层"的底色，
        # 即使有极少量不透明像素残留也不会显眼（白色会变成一圈白边）。
        # 真正的透明靠 transparent=True + CSS 的 rgba 背景。
        background_color="#000000",
    )

    # 注册给拖动逻辑用（见 set_drag_pos）
    _WINDOW["win"] = win

    def page_ready():
        """页面加载完成（panel 元素存在且渲染函数跑过）。

        判据：panel 存在 + 有 app.js 的痕迹（renderEmpty 或 render 已定义）。
        不能等"名字有内容" —— 机器人没在写状态时名字一直是"等待连接"，
        那样 page_ready 永远 False，窗口尺寸也就永远不钉（实测踩过）。
        """
        try:
            r = win.evaluate_js("""
              (() => {
                const p = document.getElementById('panel');
                if (!p) return 'no-panel';
                if (typeof render !== 'function') return 'no-script';
                return 'ready';
              })()
            """)
            okv = str(r) == "ready"
            _log("page_ready: %s" % r)
            return okv
        except Exception as e:
            _log("page_ready ERR: %s" % e)
            return False

    def enter_embedded():
        """让页面知道"我在真悬浮窗里"，并让窗口贴合内容高度。

        实测面板内容只需约 325 CSS px（展开）/ 149（折叠），
        但默认窗口给 620，底部会留一大片透明空白 —— 透明区域虽然
        看不见，但会挡住鼠标。

        注意：SetWindowPos 只改尺寸，**不要动位置**（SWP_NOMOVE）。
        之前传 (0,0) 把窗口挪到屏幕左上角，和初始位置不一致，
        导致外部截图工具按旧坐标抓，看起来"面板被切"。

        ★ 折叠态必须在这里恢复（2026-11-21）
        ------------------------------------
        config/overlay.json 里的 folded 之前只写进 state，**从没告诉前端**。
        而前端 bindFold 是从 localStorage 读的 —— 两套存储不同步，
        所以重启后永远按展开态开（实测配置 folded=true 但窗口 329 高，
        那是展开态的 325，不是折叠态的 149）。
        """
        try:
            win.evaluate_js("document.body.classList.add('embedded')")
        except Exception:
            return

        # 把原生侧记住的折叠态同步给页面
        if state.get("folded"):
            try:
                win.evaluate_js("""
                  (() => {
                    const ov = document.getElementById('overlay');
                    const btn = document.getElementById('btnFold');
                    if (ov) ov.classList.add('folded');
                    if (btn) btn.textContent = '展开';
                    try { localStorage.setItem('overlay.folded', '1'); } catch (e) {}
                  })()
                """)
                _log("enter_embedded: 恢复折叠态")
            except Exception as e:
                _log("enter_embedded: 恢复折叠态失败 %s" % e)

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

        # ★ Form/WebView2 的透明必须在**任何显示操作之前**设好
        #
        # fix_transparent 里有 ShowWindow(0) -> ShowWindow(5)，
        # 会触发一次完整重绘。如果在那之后才设 BackColor，
        # 第一次重绘已经用浅灰底画过一遍了（实测会残留白底）。
        make_form_transparent(win)

        # ★ Form/WebView2 的 BackColor 不在这里设
        #
        # 实测（2026-11-21）：从后台线程访问 WinForms 控件属性
        # （哪怕只是 BackColor）在 pythonnet 下可能死锁 ——
        # 日志停在调用之后，page_ready 永不返回。
        #
        # 完全放弃 WebView2 层的透明，改用 DWM blur-behind：
        # 窗口内容照常绘制（文字不透明），背景由系统模糊后填充。

        # 顺序很重要：先等页面画出来，再修透明
        fix_transparent(hwnd, page_ready)
        add_layered(hwnd)
        hide_from_taskbar(hwnd)
        strip_dwm_frame(hwnd)
        enable_blur_behind(hwnd)
        set_acrylic(hwnd)
        set_topmost(hwnd)

        # ★ 第三次：等页面真正加载完再设一次
        #
        # 实测（2026-11-21）：前两次调用时 WebView2 控件**还没创建**
        # （日志显示 "Form + 0 个子控件"），所以只设到了 Form 本身。
        # 而真正画白色的是 WebView2 控件（BackColor=A255 R240 G240 B240）。
        #
        # 这里在后台线程里等 page_ready，再设一次。
        def _late_transparent():
            end = time.time() + 20.0
            while time.time() < end:
                try:
                    if page_ready():
                        break
                except Exception:
                    pass
                time.sleep(0.3)
            time.sleep(0.8)          # 让 WebView2 完成首帧
            for _ in range(3):
                make_form_transparent(win)
                time.sleep(0.4)

        threading.Thread(target=_late_transparent, daemon=True).start()

        # 关键：用 url 加载时，create_window 的 width/height 会被
        # WebView2 按页面内容"自适应"。而且 win.resize() 在透明+无焦点
        # 窗口上会阻塞。所以用 SetWindowPos 显式钉尺寸。
        #
        # ★ 尺寸口径由**启动自检**决定，不再猜（winprobe）
        #
        # 踩过三次，每次都是"某一层以为是物理、另一层以为是逻辑"：
        #   1. 存物理、读当 CSS   -> 窗口跑到 4236（屏幕 3072 宽）
        #   2. 读时又 /scale       -> 窗口跑到 -572
        #   3. pin size 乘错倍率   -> 窗口砍一半（340x620 变 170x310）
        #
        # 关键坑：只做"设 400 读回 400"这个实验**不足以定口径**。
        # 实测 SetWindowPos(680) -> rect 680，看起来"同口径"，
        # 但同一时刻页面 innerWidth=340 —— 也就是说 680 设备像素 = 340 CSS。
        # 必须拿 CSS 视口交叉验证才能算出真实倍率。
        try:
            import winprobe
            # reader 会被 probe 调用两次（设尺寸前后各一次），
            # 所以这里只传一个纯读取函数。
            def _read_vp():
                w_css = win.evaluate_js("window.innerWidth || 0")
                h_css = win.evaluate_js("window.innerHeight || 0")
                return (int(w_css or 0), int(h_css or 0))

            time.sleep(0.8)
            p = winprobe.probe(hwnd, reader=_read_vp)
            _log("winprobe: %s" % winprobe.describe(probe_result=p))
            mul = winprobe.device_scale(p)
            if p.get("ratio_suspect"):
                _log("pin: 倍率可疑（%.2f vs dpi %.2f），退回 dpi 比例"
                     % (mul, p["scale"]))
                mul = p["scale"]
            _log("pin: device/css=%.2f（css_viewport=%s）"
                 % (mul, p.get("css_viewport")))
            cur = ctypes.wintypes.RECT()
            UD.GetWindowRect(hwnd, ctypes.byref(cur))
            UD.SetWindowPos(hwnd, 0, cur.left, cur.top,
                            int(args.width * mul), int(args.height * mul),
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)
            time.sleep(0.4)
            _log("pin size (%dx%d css, mul=%.2f) -> rect=%s"
                 % (args.width, args.height, mul, _rect_str(hwnd)))
        except Exception as e:
            _log("initial SetWindowPos FAIL: %s" % e)

        enter_embedded()

        # 调试：--alpha N 直接应用整窗透明度（150~255），绕过滑块。
        # 排查"透明度没效果"时用：python src\overlay.py --alpha 200
        if getattr(args, "alpha", 0):
            try:
                win.evaluate_js(
                    "(window.__setAlpha||function(){})(%d)" % args.alpha)
                time.sleep(0.4)
                _log("--alpha %d 已应用" % args.alpha)
            except Exception as e:
                _log("--alpha 应用失败: %s" % e)

        state["ready"] = True
        print("窗口就绪 hwnd=%d" % hwnd)

        # 诊断：把页面真实状态写进日志。
        #
        # 排查"透明度完全无效果"时，从外部完全看不到页面内部状态：
        #   · PrintWindow 抓的是缓存帧
        #   · 浏览器 tab 连不上（服务端口可能被占）
        #   · 没法从外部改 CSS 变量
        # 所以启动后主动把关键值打到 logs/overlay.log，下次一看就知道。
        try:
            dom = win.evaluate_js("""
              (() => {
                const root = document.documentElement;
                const cs = getComputedStyle(root);
                const p = document.getElementById('panel');
                const s = document.getElementById('stage');
                return {
                  body: document.body.className,
                  alpha: cs.getPropertyValue('--alpha').trim(),
                  bgRgb: cs.getPropertyValue('--bg-rgb').trim(),
                  panelBg: p ? getComputedStyle(p).backgroundColor : null,
                  stage: s ? getComputedStyle(s).display : null,
                  vw: window.innerWidth, vh: window.innerHeight,
                };
              })()
            """)
            _log("DOM %s" % json.dumps(dom, ensure_ascii=False))
        except Exception as e:
            _log("DOM dump failed: %s: %s" % (type(e).__name__, e))

        # ★ 此时 WebView2 控件已创建，但 DefaultBackgroundColor 只能在
        # UI 线程设 —— 这里仍是后台线程，交给 setup 开头的调用。
        # 只做无害的 Form.BackColor（那部分不限线程）。

        # 监视：每 3 秒记一次窗口尺寸，抓"被谁改回去了"。
        # 实测踩过：fit 明明设成 680x942，几秒后变成 340x471，
        # 但从日志看不出是谁改的。
        def watch():
            for _ in range(10):
                time.sleep(3.0)
                _log("watch: rect=%s" % _rect_str(hwnd))

        threading.Thread(target=watch, daemon=True).start()

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

    # ★ setup 必须**在后台线程**跑，不能用 webview.start(setup)
    #
    # 踩过的坑（2026-11-21）：
    #   一开始是 threading.Thread(target=setup)，能跑但设不了
    #   WebView2 的 DefaultBackgroundColor（只能在 UI 线程访问）。
    #
    #   改成 webview.start(setup) 后，setup 占住了 UI 线程，
    #   里面的 evaluate_js 需要消息循环 -> **死锁**
    #   （日志停在 make_form_transparent 之后，page_ready 永不返回）。
    #
    # 正确做法：setup 留在后台线程，需要 UI 线程的操作（比如
    # DefaultBackgroundColor）用 win.events.shown / loaded 回调，
    # 那些由 pywebview 在 UI 线程触发。
    threading.Thread(target=setup, daemon=True).start()

    webview.start()


if __name__ == "__main__":
    sys.exit(main() or 0)
