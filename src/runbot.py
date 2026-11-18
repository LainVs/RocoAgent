"""runbot.py - start the bot and keep the game window in the foreground.

The bot loses every key when another window owns the foreground: send_key()
checks GetForegroundWindow() and drops the press. Running the bot from a tool
call makes that worse, because each command brings the editor back to the front.

This launcher:
  * puts the game on top (HWND_TOPMOST) so a normal window cannot cover it
  * re-asserts focus on a timer while the bot runs
  * keeps the bot in this process, so nothing depends on the caller's lifetime

usage:
    python runbot.py [--seconds 3600] [--no-topmost]
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, r"D:\Projects\RocoAgent\src")
sys.path.insert(0, r"D:\Tools")

BOT = r"D:\Projects\RocoAgent\src\robot.py"
PY = sys.executable


def find_game_hwnd():
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, lp):
        n = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, n, 256)
        if "洛克王国" in n.value and u.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, cls, 256)
            if "Unreal" in cls.value:
                found.append(hwnd)
        return True

    u.EnumWindows(cb, 0)
    return found[0] if found else None


def keep_foreground(hwnd, stop, topmost=True):
    u = ctypes.windll.user32
    HWND_TOPMOST = -1
    SWP = 0x0002 | 0x0001 | 0x0040          # NOMOVE | NOSIZE | SHOWWINDOW
    while not stop.is_set():
        try:
            if u.GetForegroundWindow() != hwnd:
                if topmost:
                    u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP)
                u.BringWindowToTop(hwnd)
                u.SetForegroundWindow(hwnd)
        except Exception:
            pass
        stop.wait(0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=3600)
    ap.add_argument("--no-topmost", action="store_true")
    args = ap.parse_args()

    hwnd = find_game_hwnd()
    if not hwnd:
        print("game window not found")
        return 2
    print("game hwnd =", hwnd)

    stop = threading.Event()
    t = threading.Thread(target=keep_foreground,
                         args=(hwnd, stop, not args.no_topmost), daemon=True)
    t.start()
    time.sleep(1.0)

    cmd = [PY, "-W", "ignore", "-u", BOT]
    if args.seconds:
        cmd += ["--seconds", str(args.seconds)]
    print("starting bot:", " ".join(cmd))
    try:
        rc = subprocess.call(cmd, cwd=os.path.dirname(BOT))
    finally:
        stop.set()
    return rc


if __name__ == "__main__":
    sys.exit(main())
