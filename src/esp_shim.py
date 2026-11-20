"""esp_shim.py - 直连 ESP32（无守护进程，纯 async）。

为什么直连
----------
之前有 espfast.py(9998) 和 espd.py(8765) 两个守护进程抢 BLE，表现是
"板子开着却永远连不上"。现在只有本进程持有 BleakClient。

为什么纯 async
--------------
robot.py 用自己的事件循环跑这里的方法：
    self.loop.run_until_complete(self.ble.connect())
所以这里每个方法都必须是协程，**内部绝不能再 run_until_complete**，
否则报 "Cannot run the event loop while another loop is running"。

连接策略（实测有效）
--------------------
1. 直接按地址连（不扫描 —— 扫描会独占适配器）
2. 失败重试 3 次，间隔 3 秒
3. 连上后校验 GATT 特征值存在
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import time

from bleak import BleakClient

ADDR = "14:C1:9F:3A:BB:35"
CHAR = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"


def _log(msg):
    try:
        import paths
        p = os.path.join(str(paths.LOGS), "ble.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def _cursor():
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    p = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


class Session:
    def __init__(self, verbose=False):
        self.v = verbose
        self.client = None

    # ---------------- 连接 ----------------
    async def connect(self):
        if self.client is not None and self.client.is_connected:
            return True
        for attempt in (1, 2, 3):
            try:
                c = BleakClient(ADDR, timeout=20)
                await c.connect()
                if not c.is_connected:
                    continue
                try:
                    ch = c.services.get_characteristic(CHAR)
                except Exception:
                    ch = None
                if ch is None:
                    _log("特征值缺失 attempt %d" % attempt)
                    try:
                        await c.disconnect()
                    except Exception:
                        pass
                    await asyncio.sleep(3)
                    continue
                self.client = c
                _log("connected %s (attempt %d)" % (ADDR, attempt))
                return True
            except Exception as e:
                _log("attempt %d: %s" % (attempt, str(e)[:60]))
                if attempt < 3:
                    await asyncio.sleep(3)
        return False

    # ---------------- 命令 ----------------
    async def _asend(self, text):
        if self.client is None or not self.client.is_connected:
            if not await self.connect():
                raise RuntimeError("board offline")
        await self.client.write_gatt_char(CHAR, str(text).encode(),
                                          response=False)

    async def send(self, text, settle=0.0):
        await self._asend(text)
        if settle:
            await asyncio.sleep(settle)
        return "ok"

    async def key(self, k, hold=0):
        await self._asend("KEY:" + str(k))
        if hold:
            await asyncio.sleep(hold)
        return "ok"

    async def click(self, ms=60):
        await self._asend("CLICK:%d" % ms)
        return "ok"

    async def click_at(self, x, y, hold_alt=False):
        """硬件光标走到 (x,y) 再点击。板子约 3.5 屏幕像素/单位。"""
        for _ in range(4):
            cx, cy = _cursor()
            dx, dy = int(x) - cx, int(y) - cy
            if abs(dx) <= 3 and abs(dy) <= 3:
                break
            ex, ey = round(dx / 3.5), round(dy / 3.5)
            if ex == 0 and ey == 0:
                ex = 1 if dx > 0 else -1
                ey = 1 if dy > 0 else -1
            dur = 100 + min(120, max(abs(ex), abs(ey)) // 4)
            await self._asend("MOVESLOW:%d,%d,%d" % (ex, ey, dur))
            await asyncio.sleep(0.05 + dur / 1000.0)
        if hold_alt:
            await self._asend("ALTDOWN")
            await asyncio.sleep(0.2)
        await self._asend("CLICK:60")
        await asyncio.sleep(0.08)
        if hold_alt:
            await self._asend("ALTUP")
        return "ok"

    async def goto(self, tx, ty, tol=3, tries=5):
        return (tx, ty)

    async def close(self):
        try:
            if self.client is not None:
                await self.client.disconnect()
        except Exception:
            pass
        self.client = None
        return True
