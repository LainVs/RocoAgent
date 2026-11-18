"""capture.py - 1:1 physical screenshots anchored to the game window.

All regions are stored relative to the game window's top-left corner, so they
survive window moves.

usage:
  python capture.py --rect                 print the game window rect
  python capture.py --grab name x y w h    save a crop (window-relative)
  python capture.py --full out.png         save the whole window
"""

import ctypes
import ctypes.wintypes as wt
import io
import os
import sys


try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

TITLE_KEY = "洛克王国"


def game_rect():
    """Return physical (x, y, w, h) of the game window."""
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, lparam):
        n = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, n, 256)
        if TITLE_KEY in n.value and u.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, cls, 256)
            if "Unreal" in cls.value:
                r = wt.RECT()
                u.GetWindowRect(hwnd, ctypes.byref(r))
                found.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    u.EnumWindows(cb, 0)
    if not found:
        return None
    return found[0]


def grab_rel(dx, dy, w, h):
    """Grab a region given in window-relative physical pixels."""
    import mss
    import numpy as np
    rect = game_rect()
    if not rect:
        raise RuntimeError("game window not found")
    x, y = rect[0] + dx, rect[1] + dy
    with mss.mss() as sct:
        shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
        return np.array(shot)[:, :, :3]


def save_png(arr, path):
    from PIL import Image
    Image.fromarray(arr[:, :, ::-1]).save(path)  # BGR -> RGB


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--rect":
        r = game_rect()
        print("game rect:", r)
        return 0

    if args[0] == "--full":
        out = args[1] if len(args) > 1 else r"D:\Projects\RocoAgent\logs\full.png"
        rect = game_rect()
        arr = grab_rel(0, 0, rect[2], rect[3])
        os.makedirs(os.path.dirname(out), exist_ok=True)
        save_png(arr, out)
        print("saved", out, arr.shape)
        return 0

    if args[0] == "--grab":
        name = args[1]
        dx, dy, w, h = (int(v) for v in args[2:6])
        out = r"D:\Projects\RocoAgent\logs\%s.png" % name
        arr = grab_rel(dx, dy, w, h)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        save_png(arr, out)
        print("saved", out, arr.shape)
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())

