"""esp_shim.py - drop-in replacement for rocs.Session that talks to espfast.

Why: only one process can hold the BLE link. The espfast daemon keeps it open
permanently (54 ms per command instead of a 5 s reconnect), so the bot must go
through the daemon rather than opening its own link. If both try, the loser gets
"BLE FAILED - aborting" - observed at 17:12:24.

The interface matches rocs.Session (connect / key / click_at / send / close) so
robot.py needs no changes beyond importing this instead of rocs.
"""
from __future__ import annotations

import socket
import time

PORT = 9998


class Session:
    def __init__(self, verbose=False):
        self.v = verbose

    # ---------------- transport ----------------
    def _cmd(self, line, timeout=20.0):
        try:
            s = socket.create_connection(("127.0.0.1", PORT), timeout=timeout)
        except OSError:
            raise RuntimeError("espfast daemon not running on %d" % PORT)
        try:
            s.sendall((line + "\n").encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            out = buf.decode("utf-8", "replace").strip()
        finally:
            s.close()
        if out.startswith("ERR"):
            raise RuntimeError(out)
        return out

    async def connect(self):
        try:
            return self._cmd("ping") == "pong"
        except Exception:
            return False

    async def send(self, text, settle=0.0):
        out = self._cmd("raw " + text)
        if settle:
            time.sleep(settle)
        return out

    async def goto(self, tx, ty, tol=3, tries=5):
        # The daemon does the closed-loop cursor walk; this is a no-op kept for
        # interface compatibility.
        return (tx, ty)

    async def click(self, ms=60):
        return self._cmd("raw CLICK:%d" % ms)

    async def click_at(self, x, y, hold_alt=False):
        # Must return non-None: robot.click_abs() reports success as
        # "r is not None". Returning None made every click look failed even
        # though the board had performed it - the switch card got selected but
        # the confirm click never fired, which is the "selected but never
        # deployed" bug.
        return self._cmd("click %d %d%s" % (int(x), int(y),
                                            " alt" if hold_alt else ""))

    async def key(self, k, hold=0):
        out = self._cmd("key " + str(k))
        if hold:
            time.sleep(hold)
        return out

    async def close(self):
        return True
