"""espfast.py - persistent BLE link with a TCP command port.

Why this exists
---------------
rocs.py spawns a fresh process per command and re-connects the board every
time. Measured on this machine: 5.0-5.6 s per connect, 5/5 success. So every
`key w` costs five seconds, which makes walking in the world impossible and
makes the bot miss whole turns.

An established link is rock solid: connected in 2.3 s, still up after 20 s of
silence, and fine for 30 s with a keepalive write every 5 s. So the fix is to
connect once and stay connected.

Protocol: one command per line on 127.0.0.1:9998, one reply line.
    ping                 -> pong
    key <k>              -> ok        (k = single char, or "space"/"esc"/...)
    key <k> <ms> <k> ...  -> ok       (sequence with sleeps, same syntax as rocs)
    click <x> <y>        -> ok        (moves the hardware cursor, then clicks)
    click <x> <y> <alt>  -> ok        (hold Alt while clicking)
    move <dx> <dy>       -> ok        (relative mouse move, for turning)
    raw <text>           -> ok        (anything the firmware understands)
    state                -> connected=<0|1> errors=<n> reconnects=<n>

usage:
    python espfast.py            # run the daemon (foreground)
"""
from __future__ import annotations
import os
import paths

import asyncio
import ctypes
import socketserver
import sys
import threading
import time

sys.path.insert(0, str(paths.TOOLS))
from bleak import BleakClient  # noqa: E402

ADDR = "14:C1:9F:3A:BB:35"
NAME = "ESP32_AimAssist"
CHAR = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
PORT = 9998
LOG = str(paths.ROOT / "logs/espfast.log")

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor_pos():
    p = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


class Board:
    """One long-lived BLE link with automatic recovery."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.client = None
        self.lock = threading.Lock()
        self.errors = 0
        self.reconnects = 0
        self._start()

    # ---------------- connection ----------------
    def _start(self):
        def runner():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._connect())
            self.loop.create_task(self._keepalive())
            self.loop.run_forever()
        t = threading.Thread(target=runner, daemon=True)
        t.start()
        # wait for the first connection
        for _ in range(100):
            if self.client is not None and self.client.is_connected:
                return
            time.sleep(0.1)

    async def _connect(self):
        for attempt in range(2):
            try:
                c = BleakClient(ADDR, timeout=8)
                await c.connect()
                if c.is_connected:
                    self.client = c
                    log("connected")
                    return True
            except Exception as e:
                log("connect attempt %d failed: %s" % (attempt + 1, e))
            await asyncio.sleep(0.5)
        return False

    async def _ensure(self):
        if self.client is not None and self.client.is_connected:
            return True
        self.reconnects += 1
        log("link down, reconnecting (#%d)" % self.reconnects)
        return await self._connect()

    async def _keepalive(self):
        """A quiet write every 5 s keeps the link from being dropped."""
        while True:
            await asyncio.sleep(5)
            try:
                if self.client is not None and self.client.is_connected:
                    # LED command with zero brightness: no visible effect.
                    await self.client.write_gatt_char(CHAR, b"LED:0,0,8",
                                                      response=False)
                else:
                    await self._ensure()
            except Exception as e:
                self.errors += 1
                log("keepalive error: %s" % e)

    # ---------------- commands ----------------
    async def _send(self, text):
        if not await self._ensure():
            raise RuntimeError("board offline")
        await self.client.write_gatt_char(CHAR, text.encode(), response=False)

    def _run(self, coro):
        """Submit a coroutine to the BLE loop from this (server) thread.

        run_until_complete cannot be used here: the loop is already running in
        its own thread to service keepalive, and calling it from outside raises
        "This event loop is already running". run_coroutine_threadsafe hands the
        work to that loop and blocks this thread until it finishes.
        """
        with self.lock:
            fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
            return fut.result(timeout=20)

    def key(self, k):
        self._run(self._send("KEY:" + str(k)))

    def move(self, dx, dy, ms=200):
        self._run(self._send("MOVESLOW:%d,%d,%d" % (dx, dy, ms)))

    def click(self, x, y, hold_alt=False):
        # Walk the hardware cursor to the target, then click. The board moves
        # the cursor 3.5 screen px per reported unit, so this is a closed loop.
        #
        # Speed matters: the loop used to allow 6 iterations with a
        # 0.1 + dur/1000 sleep each, which measured 2.2 s per click. The caller
        # only waits 0.6 s before the next action, so the cursor was still
        # travelling when the confirm click arrived and the panel never closed.
        # Fewer, larger steps and a tighter settle time bring it under 700 ms.
        for _ in range(4):
            cx, cy = cursor_pos()
            dx, dy = x - cx, y - cy
            if abs(dx) <= 3 and abs(dy) <= 3:
                break
            ex, ey = round(dx / 3.5), round(dy / 3.5)
            if ex == 0 and ey == 0:
                ex = 1 if dx > 0 else -1
                ey = 1 if dy > 0 else -1
            # Cap the per-step duration: the board needs time to travel, but
            # waiting for the full computed duration is what made this slow.
            dur = 100 + min(120, max(abs(ex), abs(ey)) // 4)
            self.move(ex, ey, dur)
            time.sleep(0.05 + dur / 1000.0)
        if hold_alt:
            self._run(self._send("ALTDOWN"))
            time.sleep(0.2)
        self._run(self._send("CLICK:60"))
        time.sleep(0.08)
        if hold_alt:
            self._run(self._send("ALTUP"))

    def state(self):
        ok = self.client is not None and self.client.is_connected
        return "connected=%d errors=%d reconnects=%d" % (
            int(ok), self.errors, self.reconnects)


BOARD = None


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            line = self.rfile.readline().decode("utf-8", "replace").strip()
        except Exception:
            return
        if not line:
            return
        parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]
        out = "ok"
        try:
            if cmd == "ping":
                out = "pong"
            elif cmd == "state":
                out = BOARD.state()
            elif cmd == "key":
                i = 0
                while i < len(args):
                    a = args[i]
                    if a.isdigit() and i > 0:
                        time.sleep(int(a) / 1000.0)
                        i += 1
                        continue
                    BOARD.key(a)
                    i += 1
            elif cmd == "move":
                BOARD.move(int(args[0]), int(args[1]),
                           int(args[2]) if len(args) > 2 else 200)
            elif cmd == "click":
                hold = len(args) > 2 and args[2] in ("alt", "1", "true")
                BOARD.click(int(args[0]), int(args[1]), hold)
            elif cmd == "raw":
                BOARD._run(BOARD._send(" ".join(args)))
            else:
                out = "unknown " + cmd
        except Exception as e:
            out = "ERR %s: %s" % (type(e).__name__, e)
        try:
            self.wfile.write((out + "\n").encode("utf-8"))
        except Exception:
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    global BOARD
    BOARD = Board()
    if BOARD.client is None or not BOARD.client.is_connected:
        print("board connect failed")
        return 1
    print("espfast up on %d, %s" % (PORT, BOARD.state()), flush=True)
    log("daemon up")
    with Server(("127.0.0.1", PORT), Handler) as srv:
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
