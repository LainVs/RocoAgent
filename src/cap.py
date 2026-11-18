"""cap.py - fast 1:1 screenshot of the game window, no window re-discovery cost.

usage:
  python cap.py out.png              full window
  python cap.py out.png x y w h      crop (window-relative)
"""
import ctypes
import ctypes.wintypes as wt
import sys

import mss
import numpy as np

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

TITLE = "\u6d1b\u514b\u738b\u56fd"  # 洛克王国


def game_rect():
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, lparam):
        n = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, n, 256)
        if TITLE in n.value and u.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, cls, 256)
            if "Unreal" in cls.value:
                r = wt.RECT()
                u.GetWindowRect(hwnd, ctypes.byref(r))
                found.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    u.EnumWindows(cb, 0)
    return found[0] if found else None


def grab(dx=0, dy=0, w=None, h=None):
    r = game_rect()
    if not r:
        raise RuntimeError("game window not found")
    if w is None:
        w, h = r[2], r[3]
    with mss.mss() as s:
        shot = s.grab({"left": r[0] + dx, "top": r[1] + dy, "width": w, "height": h})
        return np.array(shot)[:, :, :3]


def main():
    a = sys.argv[1:]
    out = a[0]
    if len(a) >= 5:
        arr = grab(int(a[1]), int(a[2]), int(a[3]), int(a[4]))
    else:
        arr = grab()
    from PIL import Image
    Image.fromarray(arr[:, :, ::-1]).save(out)
    print("saved", out, arr.shape)


if __name__ == "__main__":
    main()
