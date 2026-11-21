# -*- coding: utf-8 -*-
"""窗口查找 / 焦点 / 硬件点击 / Screen 截图与坐标换算

从 robot.py 拆出（第 226-517 行），纯搬移，逻辑未改。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time
import mss
import numpy as np
import cv2

from config import (  # noqa: F401
    LOG_DIR, TITLE, log,
)


# ------------------------------------------------------------------ window
_UD = ctypes.windll.user32

# 窗口几何缓存（rect / hwnd / client_size / client_off）。
#
# 为什么需要：`standalone` 在 HTTP 服务里按线程各建一个 Screen，
# 而 HTTP 是秒级轮询 —— 等于每秒探测好几次窗口，甚至 ShowWindow。
# 对一个独占全屏的 UE 游戏反复做这些是危险的。
#
# 缓存后重复构造 Screen 变成纯读内存，不再碰游戏窗口。
_GEOM = {"at": 0.0, "rect": None, "hwnd": None,
         "client_size": (0, 0), "client_off": (0, 0)}
_GEOM_TTL = 1.0

_SCT_LOCAL = threading.local()


def _thread_sct():
    """每线程一个 mss 实例。

    mss 的对象绑定在创建它的线程上，跨线程用会报
    `ScreenShotError: BitBlt: 系统找不到指定的文件`。
    """
    s = getattr(_SCT_LOCAL, "sct", None)
    if s is None:
        s = mss.mss()
        _SCT_LOCAL.sct = s
    return s


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

    def __init__(self, restore_iconic=True):
        """restore_iconic: 窗口最小化时是否自动还原。

        **读屏路径必须传 False**（2026-11-21 实盘教训）
        --------------------------------------------
        `standalone`（伤害计算器）只是读屏，不该改变游戏窗口状态。
        但 refresh() 原来一发现窗口 IsIconic 就调 `ShowWindow(RESTORE)`：

            实测 client_size 在 1280x800 <-> 2560x1600 之间反复横跳，
            而每次跳都会让所有固定像素框（HUD 头像/属性图标/名字条）错位。

        对一个独占全屏的 UE 游戏反复做"还原窗口 -> 切全屏"是危险的，
        gamelet 日志里确实看到 3 分钟内重启了 3 次。

        机器人仍然需要还原（它要点击，窗口必须在场），所以默认 True
        保持原行为；只有读屏的调用方显式传 False。
        """
        self.restore_iconic = bool(restore_iconic)
        self.rect = None
        self.hwnd = None
        # client_size / client_off 必须**先给默认值**。
        #
        # 为什么（2026-11-21 踩到）：`refresh()` 在找不到游戏窗口时提前 return，
        # 于是新构造的 Screen 没有 client_size 属性。而调用方
        # （standalone / foe_ident / portraits.box_for）是直接读
        # `screen.client_size` 的，游戏不在就抛 AttributeError：
        #
        #     AttributeError: 'Screen' object has no attribute 'client_size'
        #
        # 这类错误发生在"游戏没开"这种最常见的情况，必须让它优雅退化成
        # 空尺寸，而不是崩掉。
        self.client_size = (0, 0)
        self.client_off = (0, 0)
        self.last_refresh = 0.0
        self.refresh()

    # ---- mss 实例必须是线程本地的
    #
    # mss 的对象绑定在创建它的线程上。`ui_server` 是 ThreadingHTTPServer，
    # 每个请求在**不同线程**里跑，跨线程用同一个 mss 会报：
    #
    #     ScreenShotError: Windows graphics function failed:
    #     BitBlt: 系统找不到指定的文件。
    #
    # 所以这里用 property + threading.local，调用方写法保持不变。
    @property
    def sct(self):
        return _thread_sct()

    def is_covered(self):
        """游戏窗口是否被别的窗口盖住。

        为什么重要（2026-11-21 实盘踩到）
        ------------------------------
        `grab_client` 走 mss **屏幕抓取** —— 抓到的是"屏幕上看得见的东西"，
        不是"窗口里的内容"。实测游戏在后面、OpenCode 窗口盖在前面时，
        抓回来的是 OpenCode 的界面；所有识别都读垃圾，但界面仍显示
        "战斗中 · 未知"，用户完全看不出是被遮挡了。

        判据：取窗口中心点，看那里最上层的顶层窗口是不是游戏自己。
        """
        if not self.rect or not self.hwnd:
            return True
        x, y, w, h = self.rect
        top = _window_at_point(x + w // 2, y + h // 2)
        return top != self.hwnd

    def refresh(self, force=False):
        """探测窗口几何。

        为什么要 rate limit（2026-11-21）
        --------------------------------
        `standalone` 原来按线程各建一个 Screen，而 HTTP 是秒级轮询 ——
        等于每秒钟探测好几次窗口：GetWindowRect + GetClientRect，
        甚至 IsIconic 时会 ShowWindow(RESTORE)。

        对一个**独占全屏的 UE 游戏**反复做这些操作是危险的（设备丢失/被打断）。
        实测那段时间游戏在 3 分钟内重启了 3 次（gamelet 日志）。

        现在几何信息放进模块级缓存，最多每秒探一次，重复构造 Screen 变成
        纯读缓存（几乎零成本，也不碰游戏窗口）。
        """
        global _GEOM
        now = time.time()
        if not force and _GEOM["at"] and (now - _GEOM["at"]) < _GEOM_TTL:
            self.rect = _GEOM["rect"]
            self.hwnd = _GEOM["hwnd"]
            self.client_size = _GEOM["client_size"]
            self.client_off = _GEOM["client_off"]
            self.last_refresh = _GEOM["at"]
            return

        g = find_game()
        if not g:
            self.rect = None
            self.hwnd = None
            self.client_size = (0, 0)
            self.client_off = (0, 0)
            _GEOM.update(at=now, rect=None, hwnd=None,
                         client_size=(0, 0), client_off=(0, 0))
            return
        self.hwnd, x, y, w, h = g
        # A minimised game reports a tiny placeholder rect, so every window-
        # relative coordinate silently becomes garbage (this caused a 10-minute
        # stuck loop where the bot clicked random spots on the desktop).
        if _UD.IsIconic(self.hwnd):
            if not self.restore_iconic:
                # 只读屏的调用方：不碰窗口，直接当成"读不到"。
                # 注意**不写缓存** —— 否则机器人紧接着读缓存会拿到"不可用"，
                # 而它其实是可以还原窗口继续干活的。
                self.rect = None
                self.hwnd = None
                self.client_size = (0, 0)
                self.client_off = (0, 0)
                return
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
        self.last_refresh = now
        _GEOM.update(at=now, rect=self.rect, hwnd=self.hwnd,
                     client_size=self.client_size, client_off=self.client_off)
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

    # ---- 单帧多切：把 5 次抓屏压成 1 次 -------------------------------
    #
    # 实测（2026-11-21，2560x1600 客户端）：mss 每次 grab 都有 ~16ms 的
    # **固定开销**，跟区域大小几乎无关 —— 抓 244x20 的血条和抓 530x1040
    # 的技能栏都是 17ms。Perc.observe() 要抓 MY_BAR / FOE_BAR /
    # SKILL_BOX / CNT_ROI / PIP_BAND 五个区域 = 85ms，占了感知耗时的一半。
    #
    # 一次整窗 grab 只要 ~52ms，切出来的 5 块完全一样。
    # 另外整帧**不转 RGB**：4MP 的 cvtColor 要 ~20ms，而切片是小图，
    # 每块转一次总共不到 1ms。
    #
    # 为什么抓"整窗"而不是"客户区"：grab_s(box) 的 box 是**窗口坐标**，
    # sbox() 减掉 BASE_OFF(13,58) 再经 _grab_at_client 加回 client_off，
    # 净效果就是按窗口坐标 (x,y) 取像素。CNT_ROI 的 y=55 < 58，
    # 换到客户区就是负坐标 —— 只抓客户区会把倒计时读丢。
    #
    # 实测：85.7ms -> 51.8ms（省 34ms / 40%）。

    def grab_frame(self):
        """整窗（含边框/标题栏），BGRA 原样。返回 (frame, (sx, sy))。

        失败返回 (None, (1.0, 1.0))，调用方按"抓不到"处理。
        """
        if self.rect is None or time.time() - self.last_refresh > 2.0:
            self.refresh()
            if self.rect is None:
                return None, (1.0, 1.0)
        try:
            shot = self.sct.grab({"left": self.rect[0], "top": self.rect[1],
                                  "width": self.rect[2], "height": self.rect[3]})
        except Exception:
            return None, (1.0, 1.0)
        cw, ch = getattr(self, "client_size", None) \
            or (self.BASE_CLIENT_W, self.BASE_CLIENT_H)
        return np.asarray(shot), (cw / float(self.BASE_CLIENT_W),
                                  ch / float(self.BASE_CLIENT_H))

    def crop_frame(self, frame, box, scale=None, client=False):
        """从 grab_frame() 的整窗帧切一块 -> RGB，缩回基准尺寸。

        box 默认是**窗口坐标**（和 grab_s 一致）；client=True 时按
        客户区坐标（和 grab_client 一致）。
        """
        if frame is None:
            return None
        sx, sy = scale or self.scale()
        x0, y0, w0, h0 = box
        ox, oy = getattr(self, "client_off", (0, 0))
        if client:
            px = ox + int(round(x0 * sx))
            py = oy + int(round(y0 * sy))
        else:
            px = ox + int(round((x0 - self.BASE_OFF_X) * sx))
            py = oy + int(round((y0 - self.BASE_OFF_Y) * sy))
        pw = max(1, int(round(w0 * sx)))
        ph = max(1, int(round(h0 * sy)))
        if py < 0 or px < 0:
            return None
        if py + ph > frame.shape[0] or px + pw > frame.shape[1]:
            return None
        sub = frame[py:py + ph, px:px + pw]
        if sub.size == 0:
            return None
        rgb = cv2.cvtColor(sub, cv2.COLOR_BGRA2RGB)
        if abs(sx - 1.0) >= 0.004 or abs(sy - 1.0) >= 0.004:
            rgb = cv2.resize(rgb, (max(1, int(w0)), max(1, int(h0))),
                             interpolation=cv2.INTER_AREA)
        return rgb

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

        坐标系对照（务必分清，混用必错）
        --------------------------------
            config.*_BAND / *_BOX / *_REL   窗口坐标（含边框偏移 BASE_OFF）
            grab_s(box)                     窗口坐标 -> 内部走 sbox() 减 BASE_OFF
            grab_client(box)                客户区坐标
            abs_point(box, cx, cy)          **窗口坐标**（本函数）
            click_rel(x, y)                 窗口坐标
            click_abs(x, y)                 屏幕物理坐标

        换算链：
            crop 内像素 (cx, cy)
            -> 客户区 (box[0] - BASE_OFF_X + cx, box[1] - BASE_OFF_Y + cy)
            -> 屏幕   client_rect_on_screen() + 上面的客户区坐标

        踩过的坑（2026-11-21，两次，方向相反）
        ------------------------------------
        1) 最初写成 cr + sbox(box) + cx：sbox 已经减过 BASE_OFF，
           而 cr 本身就是客户区屏幕位置 -> 重复减，偏左上 (13,58)。

        2) 我为了修自己写的 scripts/click_img.py（它按客户区算），
           把本函数改成"box 按客户区" -> 反而少了减 BASE_OFF，
           导致 find_action_button / find_confirm_button 全部偏右下 (13,58)。
           表现：点结算页「退出」时光标停在按钮下方一点，机器人卡住。

        结论：**不要为了让调试脚本好写而改库函数语义**。
        config 里所有 band 都是窗口坐标，这个函数必须跟着它们走。
        实测：按钮客户区 (1151,1438) 258x73，本函数修复前返回 (1901,1615)，
        正确值 (1888,1557)，差正好 (13,58)。
        """
        sx, sy = self.scale()
        cr = self.client_rect_on_screen()
        if cr is None:
            return None
        return (cr[0] + int(round((box[0] - self.BASE_OFF_X + cx) * sx)),
                cr[1] + int(round((box[1] - self.BASE_OFF_Y + cy) * sy)))

    def client_rect_on_screen(self):
        if self.rect is None:
            return None
        ox, oy = getattr(self, "client_off", (0, 0))
        cw, ch = getattr(self, "client_size", (self.rect[2], self.rect[3]))
        return (self.rect[0] + ox, self.rect[1] + oy, cw, ch)

# ------------------------------------------------------------------ perception
_SPECIES_VOCAB = None

