"""espfast.py - persistent BLE link with a TCP command port.

Why this exists
---------------
A fresh connect costs 5 s, so every `key w` would cost five seconds. One
long-lived link costs 54 ms per command instead.

Discovery (this is the part that keeps breaking)
------------------------------------------------
The board's BLE address is not guaranteed to stay the same across reflashes
and it never matches a hard-coded constant forever. So the daemon resolves
the board in this order, and remembers whatever worked:

    1. logs/board.json  上次连上的地址（命中就秒连）
    2. PINNED           历史地址，兼容老机器
    3. 按名字扫描       ESP32 / AimAssist（大小写无关）
    4. 按 Service UUID  6E400001-…（名字改了也能找回来）

连上之后把地址写回 board.json，下次直接命中，不用再扫。
板子不在时守护进程**不退出**：端口照常监听，后台每 5 秒重试一次，
这样"先启动机器人、后给板子上电"也能自己接上。

Protocol: one command per line on 127.0.0.1:9998, one reply line.
    ping                 -> pong（板子在线） / offline（守护进程在，板子没连上）
    pingd                -> pong（只测守护进程是否活着）
    key <k>              -> ok        (k = single char, or "space"/"esc"/...)
    key <k> <ms> <k> ...  -> ok       (sequence with sleeps, same syntax as rocs)
    click <x> <y>        -> ok        (moves the hardware cursor, then clicks)
    click <x> <y> <alt>  -> ok        (hold Alt while clicking)
    move <dx> <dy>       -> ok        (relative mouse move, for turning)
    raw <text>           -> ok        (anything the firmware understands)
    state                -> connected=<0|1> errors=<n> reconnects=<n> addr=<..>

usage:
    python espfast.py            # run the daemon (foreground)
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import socketserver
import sys
import threading
import time

sys.path.insert(0, r"D:\Tools")
from bleak import BleakClient, BleakScanner  # noqa: E402

PINNED = "14:C1:9F:3A:BB:35"          # 历史地址，只作为候选
NAME = "ESP32_AimAssist"
SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
CHAR = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
PORT = 9998
LOG = r"D:\Projects\RocoAgent\logs\espfast.log"
HERE = os.path.dirname(os.path.abspath(__file__))
BOARD_FILE = os.path.join(HERE, "board.json")

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


def load_hint():
    try:
        with open(BOARD_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_hint(addr, name, how):
    try:
        with open(BOARD_FILE, "w", encoding="utf-8") as f:
            json.dump({"address": addr, "name": name, "how": how,
                       "updated": time.strftime("%Y-%m-%d %H:%M:%S")},
                      f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class Board:
    """One long-lived BLE link with automatic recovery."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.client = None
        self.address = None
        self.lock = threading.Lock()
        self.errors = 0
        self.reconnects = 0
        self.waiting = True
        self._start()

    # ---------------- connection ----------------
    def _start(self):
        def runner():
            asyncio.set_event_loop(self.loop)

            async def boot():
                while True:
                    if await self._connect():
                        self.waiting = False
                        break
                    log("board not found, retry in 5 s")
                    await asyncio.sleep(5)
                self.loop.create_task(self._keepalive())

            self.loop.run_until_complete(boot())
            self.loop.run_forever()

        t = threading.Thread(target=runner, daemon=True)
        t.start()

    async def _open(self, target, how):
        """target 可以是地址字符串，也可以是扫到的 BLEDevice 对象。

        按地址连需要系统再解析一次，设备刚好停止广播就报 not found ——
        实测板子重启后只广播十几秒，扫描刚结束地址就解析不到了。
        用扫描返回的对象直连可以跳过这一步。
        """
        try:
            c = BleakClient(target, timeout=10)
            await c.connect()
            if c.is_connected:
                # 连接成功不等于服务发现成功：重连后偶发
                # BleakCharacteristicNotFoundError，写命令直接废掉。
                # 这里显式确认那条特征值真的在，不在就当这次没连上。
                ch = None
                try:
                    ch = c.services.get_characteristic(CHAR)
                except Exception:
                    ch = None
                if ch is None:
                    log("connected but characteristic missing, retry")
                    try:
                        await c.disconnect()
                    except Exception:
                        pass
                    return False
                self.client = c
                self.address = getattr(target, "address", target)
                save_hint(self.address, NAME, how)
                log("connected %s (%s)" % (self.address, how))
                return True
        except Exception as e:
            log("%s->%s failed: %s" % (how, getattr(target, "address", target),
                                       str(e)[:70]))
        return False

    async def _scan(self, mode, timeout=6.0):
        """按名字或 Service UUID 扫，返回候选 BLEDevice。"""
        try:
            devs = await BleakScanner.discover(timeout=timeout, return_adv=True)
        except Exception as e:
            log("scan failed: %s" % str(e)[:70])
            return []
        out = []
        for addr, (d, adv) in devs.items():
            n = d.name or ""
            if mode == "name":
                if NAME.lower() in n.lower() or "esp32" in n.lower():
                    out.append(d)
            else:
                uu = [u.lower() for u in (adv.service_uuids or [])]
                if SERVICE_UUID.lower() in uu:
                    out.append(d)
        log("scan/%s: %d devices, %d match" % (mode, len(devs), len(out)))
        return out

    async def _connect(self):
        # 1) 上次成功的地址
        hint = load_hint().get("address")
        if hint and hint != PINNED:
            if await self._open(hint, "cached"):
                return True

        # 2) 历史固定地址
        if await self._open(PINNED, "pinned"):
            return True

        # 3) 名字 / 4) service uuid，各扫两轮
        for mode in ("name", "uuid"):
            for attempt in (1, 2):
                for addr in await self._scan(mode):
                    if await self._open(addr, "%s#%d" % (mode, attempt)):
                        return True
                await asyncio.sleep(1.0)
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
        return "connected=%d errors=%d reconnects=%d addr=%s" % (
            int(ok), self.errors, self.reconnects, self.address or "-")


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
                # 板子真在线才回 pong：机器人据此判断能不能打
                ok = BOARD.client is not None and BOARD.client.is_connected
                out = "pong" if ok else "offline"
            elif cmd == "pingd":
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
    # 板子没连上也要把端口开起来：机器人可以先起来等它
    print("espfast up on %d, %s" % (PORT, BOARD.state()), flush=True)
    log("daemon up (waiting=%d)" % int(BOARD.waiting))
    with Server(("127.0.0.1", PORT), Handler) as srv:
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
