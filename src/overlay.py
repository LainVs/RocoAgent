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
    """屏幕缩放倍数（仅用于日志/兜底，不参与窗口尺寸计算）。

    **不要在这里调 SetProcessDpiAwarenessContext** ——
    实测那会让随后 create_window 的 width/height 被当成物理像素，
    窗口只有预期的一半大小（340x620 变成 170x310），
    而且从外面完全看不出原因。让进程保持系统默认的 DPI 虚拟化即可。
    """
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


def load_window_state():
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
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
        port = ensure_server(args.port)
        if not port:
            print("无法启动 ui_server（端口 %d）" % args.port)
            return 2
        url = "http://127.0.0.1:%d/%s" % (port, args.page)

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
    #
    # 高度给足：运行时 resize 在这台机器上口径混乱（实测越调越乱），
    # 所以 create 时就按"4 张技能卡 + 替补"的最大可能高度算好，
    # 之后只允许缩小（fit_window 里 min(需要, create_h)）。
    pw, ph = css_size(args.width, args.height)
    state_h_max = ph

    # 恢复上次的窗口位置和尺寸（pywebview 自己不记无边框窗口的位置）
    saved = load_window_state()
    pos_x = saved.get("x", int(40 * scale))
    pos_y = saved.get("y", int(40 * scale))
    if saved.get("w"):
        pw = saved["w"]
    if saved.get("h"):
        ph = saved["h"]

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
            if hwnd:
                try:
                    r = ctypes.wintypes.RECT()
                    UD.GetWindowRect(hwnd, ctypes.byref(r))
                    save_window_state(x=r.left, y=r.top)
                except Exception:
                    pass
            return ok

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
            _log("fit_window(force=%s) in, rect=%s"
                 % (force, _rect_str(hwnd)))
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
                if not h or int(h) < 40:
                    # embedded 刚加时布局可能还没完成，等一帧再试一次。
                    time.sleep(0.25)
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
                if not h or int(h) < 40:
                    _log("fit: measured=%s (too small, skip)" % h)
                    return False

                # 宽度固定用启动时的值，**绝不能读 window.innerWidth**：
                # resize 之后 innerWidth 会变，再拿它去算会形成正反馈，
                # 窗口越来越宽（实测 509 -> 639 -> … 无限增长）。
                css_w = state.get("css_w") or args.width
                css_h = int(h) + 2
                # 缩放倍数用页面读到的 dpr（dpi_scale 在 overlay 进程里
                # 实测返回 1.0，不可靠）。
                # 如果还没拿到（第一次 fit 可能早于 pin size），现读一次。
                s = state.get("dpr") or 0
                if s < 1.5:
                    try:
                        s = float(win.evaluate_js(
                            "window.devicePixelRatio || 1") or 1.0)
                        state["dpr"] = s
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

        # 关键：用 url 加载时，create_window 的 width/height 会被
        # WebView2 按页面内容"自适应"。而且 win.resize() 在透明+无焦点
        # 窗口上会阻塞。所以用 SetWindowPos 显式钉尺寸。
        #
        # 缩放倍数不能靠 dpi_scale()：它在 overlay 进程里实测返回 1.0，
        # 而页面里的 devicePixelRatio 是 2。用页面读出来的才可靠。
        try:
            # SetWindowPos 尺寸 = 物理像素。create_window 已经给了正确值，
            # 这里只是把它钉死（防止 WebView2 按内容自适应）。
            s = dpi_scale()
            time.sleep(0.8)
            cur = ctypes.wintypes.RECT()
            UD.GetWindowRect(hwnd, ctypes.byref(cur))
            UD.SetWindowPos(hwnd, 0, cur.left, cur.top,
                            int(args.width * s), int(args.height * s),
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)
            time.sleep(0.4)
            _log("pin size (%dx%d css x%.1f) -> rect=%s"
                 % (args.width, args.height, s, _rect_str(hwnd)))
        except Exception as e:
            _log("initial SetWindowPos FAIL: %s" % e)

        enter_embedded()
        state["ready"] = True
        print("窗口就绪 hwnd=%d" % hwnd)

        # 监视：每 3 秒记一次窗口尺寸，抓"被谁改回去了"。
        # 实测踩过：fit 明明设成 680x942，几秒后变成 340x471，
        # 但从日志看不出是谁改的。
        def watch():
            for _ in range(10):
                time.sleep(3.0)
                _log("watch: rect=%s" % _rect_str(hwnd))

        threading.Thread(target=watch, daemon=True).start()

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
