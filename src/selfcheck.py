"""selfcheck.py - 启动前的静态检查，不碰游戏、不出招。

用法：
    python src/selfcheck.py            # 窗口 + OCR + DLL 检查
    python src/selfcheck.py --ble      # 额外连一次 ESP32（会占用板子几秒）

返回码 0 = 全绿；1 = 有 FAIL（会打印是哪一项）。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import platform
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

TOOLS = str(paths.TOOLS)

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"
_results = []


def check(name, status, detail=""):
    _results.append((status, name, detail))
    print("[%s] %-22s %s" % (status, name, detail), flush=True)
    return status == OK


# ---------------------------------------------------------------- native dlls
def _module_paths(basename):
    k32 = ctypes.windll.kernel32

    class ME(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("th32ModuleID", wt.DWORD),
                    ("th32ProcessID", wt.DWORD), ("GlblcntUsage", wt.DWORD),
                    ("ProccntUsage", wt.DWORD), ("modBaseAddr", ctypes.c_void_p),
                    ("modBaseSize", wt.DWORD), ("hModule", wt.HMODULE),
                    ("szModule", ctypes.c_wchar * 256),
                    ("szExePath", ctypes.c_wchar * 260)]

    snap = k32.CreateToolhelp32Snapshot(0x18, os.getpid())
    me = ME()
    me.dwSize = ctypes.sizeof(ME)
    out = []
    if k32.Module32FirstW(snap, ctypes.byref(me)):
        while True:
            if me.szModule.lower() == basename.lower():
                out.append(me.szExePath)
            if not k32.Module32NextW(snap, ctypes.byref(me)):
                break
    k32.CloseHandle(snap)
    return out


def file_version(path):
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        ctypes.windll.version.GetFileVersionInfoW(path, 0, size, buf)
        ptr = ctypes.c_void_p()
        ln = ctypes.c_uint()
        if not ctypes.windll.version.VerQueryValueW(buf, "\\",
                                                   ctypes.byref(ptr), ctypes.byref(ln)):
            return None
        data = ctypes.string_at(ptr, ln.value)
        ms, ls = struct.unpack("<II", data[8:16])
        return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        return None


def check_dlls():
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    sys_path = os.path.join(sysroot, "System32", "msvcp140.dll")
    v = file_version(sys_path)
    ok = bool(v and v >= (14, 30))
    check("system msvcp140", OK if ok else FAIL,
          "%s %s" % (".".join(map(str, v)) if v else "?", sys_path))

    # robot.py 在 import cv2 之前预加载系统运行时；这里验证结果。
    loaded = _module_paths("msvcp140.dll")
    good = any(os.path.normcase(p).endswith(os.path.normcase(r"system32\msvcp140.dll"))
               for p in loaded)
    check("loaded msvcp140", OK if good else FAIL,
          " | ".join(loaded) or "not loaded")

    # winrt 自带的旧运行库是崩溃源头；存在只提示，预加载能屏蔽它。
    winrt_copy = None
    try:
        import importlib.util
        spec = importlib.util.find_spec("winrt")
        if spec and spec.origin:
            cand = os.path.join(os.path.dirname(spec.origin), "msvcp140.dll")
            if os.path.exists(cand):
                winrt_copy = (cand, file_version(cand))
    except Exception:
        pass
    if winrt_copy:
        check("winrt bundled msvcp140", WARN,
              "%s %s (14.29 是 0xC0000005 的来源，已由预加载屏蔽)"
              % (".".join(map(str, winrt_copy[1] or ())), winrt_copy[0]))


# -------------------------------------------------------------------- window
def check_window():
    import robot
    s = robot.Screen()
    if not s.rect:
        return check("game window", FAIL, "未找到洛克王国窗口"), None
    x, y, w, h = s.rect
    cr = s.client_rect_on_screen()
    sx, sy = s.scale()
    detail = "rect=%s client=%s scale=%.3fx%.3f" % ((x, y, w, h), cr, sx, sy)
    ok = cr is not None and sx >= 0.6 and sy >= 0.6
    check("game window", OK if ok else FAIL, detail)
    if not ok:
        return False, None
    # 窗口是否被最小化 / 完全在屏外
    u = ctypes.windll.user32
    iconic = bool(u.IsIconic(s.hwnd))
    if iconic:
        check("window visible", WARN, "窗口最小化（bot 会自动 SW_RESTORE）")
    else:
        check("window visible", OK, "hwnd=%s" % s.hwnd)
    return True, s


# ----------------------------------------------------------------------- ocr
def check_ocr():
    import numpy as np
    import hud
    t0 = time.time()
    eng = hud._engine()
    dt = time.time() - t0
    if eng is None:
        return check("ocr engine", WARN, "加载失败 -> OCR 自动禁用（不影响出招）")
    t1 = time.time()
    try:
        out = hud._ocr(np.zeros((60, 240, 3), dtype=np.uint8))
    except Exception as e:
        return check("ocr engine", FAIL, "推理异常 %s: %s" % (type(e).__name__, e))
    check("ocr engine", OK, "load %.1fs, blank infer %.2fs, out=%d"
          % (dt, time.time() - t1, len(out)))


# ----------------------------------------------------------------------- ble
def check_ble():
    import asyncio
    import esp_shim
    s = esp_shim.Session()
    loop = asyncio.new_event_loop()
    t0 = time.time()
    ok = False
    try:
        ok = loop.run_until_complete(s.connect())
    except Exception as e:
        check("esp32 ble", FAIL, "%s: %s" % (type(e).__name__, e))
        return
    finally:
        try:
            loop.run_until_complete(s.close())
        except Exception:
            pass
    check("esp32 ble", OK if ok else FAIL,
          "connect %.1fs" % (time.time() - t0))


# ---------------------------------------------------------------------- misc
def check_misc():
    lock = os.path.join(str(paths.LOGS), "robot.lock")
    try:
        import msvcrt  # noqa
        f = open(lock, "a+")
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            held = False
        except OSError:
            held = True
        f.close()
    except Exception as e:
        check("robot.lock", WARN, "无法检测: %s" % e)
        return
    check("robot.lock", WARN if held else OK,
          "另一个实例正在运行" if held else "空闲")

    for name in ("turns.jsonl", "rounds.jsonl", "bot_stdout.log"):
        p = os.path.join(str(paths.LOGS), name)
        try:
            with open(p, "a", encoding="utf-8"):
                pass
        except Exception as e:
            check("log writable", FAIL, "%s: %s" % (name, e))
            return
    check("log writable", OK, str(paths.LOGS))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ble", action="store_true", help="额外连接一次 ESP32")
    a = ap.parse_args()

    print("== RocoAgent selfcheck ==")
    check("python", OK, "%s (%s)" % (platform.python_version(), sys.executable))
    # 先走一遍 robot 的导入路径：msvcp140 预加载、cv2/numpy、faulthandler
    # 都在这里发生，后面的 DLL 检查才有意义。
    try:
        import robot  # noqa: F401
    except Exception as e:
        check("import robot", FAIL, "%s: %s" % (type(e).__name__, e))
        return 1
    check("import robot", OK, "preload + cv2/numpy")
    check_dlls()
    ok_win, _s = check_window()
    if not ok_win:
        check("game window hint", WARN, "先打开洛克王国（窗口 2586x1671）再跑机器人")
    check_ocr()
    if a.ble:
        check_ble()
    else:
        check("esp32 ble", WARN, "跳过（加 --ble 检查）")
    check_misc()

    fails = [r for r in _results if r[0] == FAIL]
    warns = [r for r in _results if r[0] == WARN]
    print("== %d OK, %d WARN, %d FAIL ==" % (
        len(_results) - len(fails) - len(warns), len(warns), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
