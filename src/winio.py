# -*- coding: utf-8 -*-
"""窗口查找 / 焦点 / 硬件点击 / Screen 截图与坐标换算

从 robot.py 拆出（第 226-517 行），纯搬移，逻辑未改。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
import mss
import numpy as np
import cv2

from config import (  # noqa: F401
    LOG_DIR, TITLE, log,
)


# ------------------------------------------------------------------ window
_UD = ctypes.windll.user32


class _PT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


def _window_at_point(x, y):
    """返回屏幕坐标点上的顶层窗口。用来在点击前确认"点下去会不会点到别的程序"。"""
    try:
        _UD.WindowFromPoint.restype = wt.HWND
        _UD.WindowFromPoint.argtypes = [_PT]
        h = _UD.WindowFromPoint(_PT(int(x), int(y)))
        if not h:
            return None
        root = _UD.GetAncestor(h, 2)      # GA_ROOT
        return root or h
    except Exception:
        return None



def find_game():
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, lp):
        n = ctypes.create_unicode_buffer(256)
        _UD.GetWindowTextW(hwnd, n, 256)
        if TITLE in n.value and _UD.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            _UD.GetClassNameW(hwnd, cls, 256)
            if "Unreal" in cls.value:
                r = wt.RECT()
                _UD.GetWindowRect(hwnd, ctypes.byref(r))
                found.append((hwnd, r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    _UD.EnumWindows(cb, 0)
    return found[0] if found else None


def _raise_topmost(hwnd):
    """Raise the game above whatever covers it, then drop the always-on-top flag.

    Returns the foreground state. Kept separate because both focus() and
    _click_to_focus() need exactly this step before anything else can work.
    """
    try:
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP = 0x0002 | 0x0001 | 0x0040      # NOMOVE | NOSIZE | SHOWWINDOW
        _UD.ShowWindow(hwnd, 9)             # SW_RESTORE
        _UD.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP)
        _UD.SetForegroundWindow(hwnd)
        _UD.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP)
    except Exception:
        pass
    return _UD.GetForegroundWindow() == hwnd


def focus(hwnd):
    """Bring the game to the foreground.

    Plain SetForegroundWindow is refused when the caller is not already the
    foreground process, which is how the bot ended up firing keys into the
    editor window instead of the game. Attaching to the foreground thread's
    input queue lifts that restriction.
    """
    if _UD.GetForegroundWindow() == hwnd:
        return True
    _UD.ShowWindow(hwnd, 9)          # SW_RESTORE
    fg = _UD.GetForegroundWindow()
    try:
        cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        fg_tid = _UD.GetWindowThreadProcessId(fg, None)
        tgt_tid = _UD.GetWindowThreadProcessId(hwnd, None)
        attached = []
        for tid in {fg_tid, tgt_tid}:
            if tid and tid != cur_tid:
                if _UD.AttachThreadInput(cur_tid, tid, True):
                    attached.append(tid)
        _UD.BringWindowToTop(hwnd)
        _UD.SetForegroundWindow(hwnd)
        _UD.SetFocus(hwnd)
        for tid in attached:
            _UD.AttachThreadInput(cur_tid, tid, False)
    except Exception:
        pass
    if _UD.GetForegroundWindow() != hwnd:
        # Last resort: raise the game above whatever window is covering it (the
        # editor was fully covering the game, so even hardware clicks landed on
        # the editor and focus never moved).
        _raise_topmost(hwnd)
    time.sleep(0.08)
    return _UD.GetForegroundWindow() == hwnd


# ------------------------------------------------------------------ capture
class Screen:
    """Region grabber. Full-window grabs cost ~100 ms; region grabs ~10 ms.

    We only ever need three small regions, so the poll loop stays at ~10 Hz.
    """

    def __init__(self):
        self.sct = mss.mss()
        self.rect = None
        self.hwnd = None
        self.last_refresh = 0.0
        self.refresh()

    def refresh(self):
        g = find_game()
        if not g:
            self.rect = None
            self.hwnd = None
            return
        self.hwnd, x, y, w, h = g
        # A minimised game reports a tiny placeholder rect, so every window-
        # relative coordinate silently becomes garbage (this caused a 10-minute
        # stuck loop where the bot clicked random spots on the desktop).
        if _UD.IsIconic(self.hwnd):
            _UD.ShowWindow(self.hwnd, 9)        # SW_RESTORE
            time.sleep(0.4)
            g = find_game()
            if g:
                self.hwnd, x, y, w, h = g
        self.rect = (x, y, w, h)
        # Client-area origin relative to the window rect. Screenshots (and the
        # HUD boxes measured on them) are client-relative, so portrait work
        # goes through grab_client(); everything else stays window-relative.
        r = wt.RECT()
        _UD.GetClientRect(self.hwnd, ctypes.byref(r))
        pt = wt.POINT(0, 0)
        _UD.ClientToScreen(self.hwnd, ctypes.byref(pt))
        self.client_off = (pt.x - x, pt.y - y)
        new_size = (r.right, r.bottom)
        old_size = getattr(self, "client_size", None)
        if old_size and old_size != new_size:
            # 分辨率：窗口大小变了：所有固定框会自动等比缩放，这里留一条记录，
            # 方便回看"某次识别异常是不是因为中途换了分辨率"。
            try:
                log("WINDOW client %sx%s -> %sx%s (fixed boxes rescale)"
                    % (old_size[0], old_size[1], new_size[0], new_size[1]))
            except Exception:
                pass
        self.client_size = new_size
        self.last_refresh = time.time()
    def grab(self, box):
        """box 用**窗口坐标**（含边框/标题栏），按原始分辨率抓，不缩放。"""
        if self.rect is None or time.time() - self.last_refresh > 2.0:
            self.refresh()
            if self.rect is None:
                return None
        gx, gy = self.rect[0], self.rect[1]
        x, y, w, h = box
        shot = self.sct.grab({"left": gx + x, "top": gy + y, "width": w, "height": h})
        return cv2.cvtColor(np.asarray(shot), cv2.COLOR_BGRA2RGB)

    def _grab_at_client(self, box):
        """box 用客户区像素坐标，原样抓。"""
        ox, oy = getattr(self, "client_off", (0, 0))
        x, y, w, h = box
        return self.grab((ox + x, oy + y, w, h))

    def _fit(self, img, box):
        """把缩放后抓到的图还原成基准尺寸，保证阈值/模板不用改。"""
        if img is None or img.size == 0:
            return img
        sx, sy = self.scale()
        if abs(sx - 1.0) < 0.004 and abs(sy - 1.0) < 0.004:
            return img
        w, h = box[2], box[3]
        return cv2.resize(img, (max(1, int(w)), max(1, int(h))),
                          interpolation=cv2.INTER_AREA)

    def grab_client(self, box):
        """box 用**客户区坐标**（基准分辨率）-> 基准尺寸图。

        头像库、特性面板、HUD 文字这些框都是在客户区截图上量的，
        和带边框的窗口坐标差着 (13, 58)。
        """
        return self._fit(self._grab_at_client(self.sbox_client(box)), box)

    def grab_s(self, box):
        """box 用**窗口坐标**（基准分辨率）-> 基准尺寸图。

        sbox() 返回的是**客户区像素坐标**，所以必须走 _grab_at_client，
        不能走 grab()（grab 用窗口原点，会再多偏移 client_off=(13,58)）。

        这个 bug 的表现：所有 grab_s 抓到的区域整体**偏右下 (13,58)**，
        于是 band 检测里按钮的 y 偏移变成 74（真实 16），
        abs_point 算出来的点击位置就偏下 58px —— 按钮永远点不到。
        """
        return self._fit(self._grab_at_client(self.sbox(box)), box)

    def grab_full(self):
        """整块客户区，基准尺寸。"""
        return self.grab_client((0, 0, self.BASE_CLIENT_W, self.BASE_CLIENT_H))

    def phys(self, x, y):
        """窗口坐标（基准）-> 屏幕物理坐标。"""
        cr = self.client_rect_on_screen()
        if cr is None:
            return None
        sx, sy = self.scale()
        return (cr[0] + int(round((x - self.BASE_OFF_X) * sx)),
                cr[1] + int(round((y - self.BASE_OFF_Y) * sy)))

    def phys_client(self, x, y):
        """客户区坐标（基准）-> 屏幕物理坐标。"""
        cr = self.client_rect_on_screen()
        if cr is None:
            return None
        sx, sy = self.scale()
        return (cr[0] + int(round(x * sx)), cr[1] + int(round(y * sy)))

    # ---- 分辨率自适应 -------------------------------------------------
    # 工程里同时存在两套坐标，历史原因。
    #   ?窗口坐标（含 13px 边框 + 58px 标题栏）：技能栏、血条、大厅按钮，
    #      这些是从整窗截图量的，用 grab()/grab_s()。?
    #   ?客户区坐标（截图原点）：头像、特性面板、HUD 文字，用 grab_client()。?
    # 两套都必须按客户区比例缩放，但只??需要先减掉边框。?
    # 混用就会?20px 高的血条整块错开。?
    BASE_CLIENT_W, BASE_CLIENT_H = 2560, 1600
    BASE_OFF_X, BASE_OFF_Y = 13, 58

    def sbox(self, box):
        """窗口坐标（基准）-> 客户区像素坐标。"""
        if self.rect is None:
            self.refresh()
            if self.rect is None:
                return box
        sx, sy = self.scale()
        x, y, w, h = box
        return (int(round((x - self.BASE_OFF_X) * sx)),
                int(round((y - self.BASE_OFF_Y) * sy)),
                max(1, int(round(w * sx))), max(1, int(round(h * sy))))

    def sbox_client(self, box):
        """客户区坐标（基准）-> 客户区像素坐标。"""
        if self.rect is None:
            self.refresh()
            if self.rect is None:
                return box
        sx, sy = self.scale()
        x, y, w, h = box
        return (int(round(x * sx)), int(round(y * sy)),
                max(1, int(round(w * sx))), max(1, int(round(h * sy))))

    def scale(self):
        """当前**客户区**相对基准客户区(2560x1600)的缩放比。"""
        if self.rect is None:
            self.refresh()
        if self.rect is None:
            return 1.0, 1.0
        cw, ch = getattr(self, "client_size", None) or (self.rect[2], self.rect[3])
        return (cw / float(self.BASE_CLIENT_W), ch / float(self.BASE_CLIENT_H))

    def abs_point(self, box, cx, cy):
        """box 是**窗口坐标**（基准分辨率），cx/cy 是 box 内的基准像素偏移
        -> 屏幕物理坐标。

        grab_s(box) 抓到的图已经缩回基准尺寸，所以图内像素 (cx, cy)
        对应的客户区像素 = sbox(box) 原点 + (cx, cy) * scale。

        以前这里直接走 phys()（会再减一次 BASE_OFF），
        导致所有用 abs_point 定位的按钮**偏下 58px**，
        机器人一直点在按钮外面 —— 结算页「退出」点不到、
        「确认首发」点不到，都源于此。
        """
        sx, sy = self.scale()
        cr = self.client_rect_on_screen()
        if cr is None:
            return None
        sb = self.sbox(box)
        return (cr[0] + int(round(sb[0] + cx * sx)),
                cr[1] + int(round(sb[1] + cy * sy)))

    def client_rect_on_screen(self):
        if self.rect is None:
            return None
        ox, oy = getattr(self, "client_off", (0, 0))
        cw, ch = getattr(self, "client_size", (self.rect[2], self.rect[3]))
        return (self.rect[0] + ox, self.rect[1] + oy, cw, ch)

# ------------------------------------------------------------------ perception
_SPECIES_VOCAB = None

