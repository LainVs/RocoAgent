# -*- coding: utf-8 -*-
"""winprobe.py —— 启动时实测 Windows 坐标口径，固化成单一事实来源。

为什么需要
----------
overlay 的坐标 bug 已经踩了三次，每次都是"某一层以为是物理、另一层
以为是逻辑"：

    第一次  存物理、读当 CSS  -> 窗口跑到屏幕外 4236（屏幕 3072 宽）
    第二次  读时又 /scale      -> 窗口跑到 -572
    第三次  pin size 乘 scale  -> 窗口被砍一半（340x620 变 170x310）

根因不是某一处写错，而是**口径没有单一事实来源**：5 处代码各自猜。
注释和测试只能兜住"已经踩过的坑"，挡不住新写的代码再猜一次。

做法
----
启动时做一次**实验**，而不是读文档或猜：

    SetWindowPos(hwnd, w=400, h=400)
    GetWindowRect(hwnd)  -> 读回多少？

如果读回 400，说明两者**同口径**，任何换算都不需要。
如果读回 200（200% DPI 下），说明 GetWindowRect 被虚拟化了，需要 ×2。

结果记进 `probe()` 的返回值，后面所有地方都引用它。

这台机器的实测结果（2026-11-21，DPI 200%）
------------------------------------------
    SetWindowPos(400) -> GetWindowRect 读回 400
    => setpos_is_physical = True, 两者同口径, 不需要换算

    GetSystemMetrics(SM_CXVIRTUALSCREEN) = 1536  （逻辑值, 物理是 3072）
    => metrics_is_logical = True, 和 GetWindowRect **不同**口径!

这最后一条是最容易搞错的：窗口 rect 和屏幕尺寸**不是同一个口径**。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

UD = ctypes.windll.user32

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

_cache = {}


def _rect(hwnd):
    r = wt.RECT()
    UD.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def probe(hwnd=None, settle=0.25, reader=None):
    """实测口径。返回 dict（结果缓存在模块级，只探一次）。

    hwnd   : 要探测的窗口（None 时只读屏幕尺寸）
    reader : 无参函数，返回页面当前的 (innerWidth, innerHeight)。
             **必须能被调用两次**：一次在设尺寸前，一次在设完之后。

    ★ 为什么是 reader 而不是直接传值
    -------------------------------
    视口必须**在窗口被改成 probe_size 之后**读。如果调用方在探测前
    就把视口读好传进来，页面还没跟着 reflow，算出来的比值是错的
    （实测 400 / 340 = 1.18，毫无意义）。

    只做"设 400 读回 400"这个实验也**不足以定口径**：它只能说明
    SetWindowPos 和 GetWindowRect 互相一致，但没法区分 400 到底是
    CSS 还是设备像素。

    实测反例（2026-11-21）：
        SetWindowPos(680)  -> rect 680
        同一时刻页面 innerWidth = 340
    => 680 设备像素 = 340 CSS，**SetWindowPos 吃设备像素**。
    只看 rect 会得出"不需要换算"的错误结论。
    """
    key = (hwnd, id(reader))
    if key in _cache:
        return _cache[key]

    out = {
        "dpi": 96,
        "scale": 1.0,
        "setpos_same_as_rect": None,
        "device_per_css": None,
        "virtual": (0, 0, 0, 0),
    }
    try:
        if hwnd:
            out["dpi"] = int(UD.GetDpiForWindow(hwnd) or 96)
    except Exception:
        pass
    out["scale"] = max(1.0, out["dpi"] / 96.0)

    out["virtual"] = (UD.GetSystemMetrics(76), UD.GetSystemMetrics(77),
                      UD.GetSystemMetrics(SM_CXVIRTUALSCREEN),
                      UD.GetSystemMetrics(SM_CYVIRTUALSCREEN))

    if hwnd:
        try:
            before = _rect(hwnd)
            probe_size = 400
            UD.SetWindowPos(hwnd, 0, before[0], before[1],
                            probe_size, probe_size,
                            SWP_NOZORDER | SWP_NOACTIVATE)
            time.sleep(settle)
            after = _rect(hwnd)
            out["probe_set"] = probe_size
            out["probe_got"] = after[2]
            out["setpos_same_as_rect"] = abs(after[2] - probe_size) <= 4

            # ★ 倍率必须用「**探测后**页面报的 CSS 宽度」去比
            #
            # 踩过的坑（2026-11-21）：一开始拿探测**前**的视口比，
            # 得到 device/css=1.18 —— 因为窗口已改成 400，而 innerWidth
            # 还是旧的 340。400/340 = 1.18，毫无意义。
            #
            # 所以这里重新读一次。WebView2 的 reflow 有延迟，多等一会儿。
            if reader is not None:
                try:
                    time.sleep(max(0.35, settle))
                    vp = reader()
                    if vp and vp[0]:
                        cw = float(vp[0]) or 1.0
                        out["css_viewport"] = (int(vp[0]), int(vp[1]))
                        out["device_per_css"] = round(after[2] / cw, 3)
                        # sanity check：和 DPI 比例差太多说明页面没跟上
                        if abs(out["device_per_css"] - out["scale"]) > 0.35:
                            out["ratio_suspect"] = True
                except Exception as e:
                    out["reader_error"] = "%s: %s" % (
                        type(e).__name__, e)

            UD.SetWindowPos(hwnd, 0, before[0], before[1],
                            before[2], before[3],
                            SWP_NOZORDER | SWP_NOACTIVATE)
            time.sleep(settle)
        except Exception as e:
            out["error"] = "%s: %s" % (type(e).__name__, e)

    _cache[key] = out
    return out


def device_scale(probe_result):
    """把 CSS 尺寸换算成 SetWindowPos 该传的值，用这个倍率。

    优先用实测的 device_per_css；拿不到才退回 DPI 比例。
    """
    r = probe_result or {}
    v = r.get("device_per_css")
    if v and v > 0:
        return float(v)
    return float(r.get("scale") or 1.0)


def describe(hwnd=None, probe_result=None):
    """给日志用的一行结论。"""
    p = probe_result or probe(hwnd)
    bits = ["dpi=%d" % p.get("dpi", 96),
            "dpi_scale=%.2f" % p.get("scale", 1.0)]
    if p.get("probe_set"):
        bits.append("设%d读回%d" % (p["probe_set"], p["probe_got"]))
    if p.get("device_per_css"):
        bits.append("device/css=%.2f" % p["device_per_css"])
    bits.append("virtual=%s" % (p.get("virtual"),))
    return "  ".join(bits)
