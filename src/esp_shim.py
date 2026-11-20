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

连接策略（2026-11-20 修订）
---------------------------
**板子的 BLE 地址和名字都会变**（换固件/重启后 MAC 可能不同，广播名也可能没有）。
经验：写死 MAC 或按名字匹配，迟早找不到板子。
所以身份用"服务"来认定：
    1. 先试上次成功过的地址（日志里缓存，只当提示）
    2. 失败 -> 扫描，优先看广播里带 NUS 服务的设备
    3. 再失败 -> 按信号强度取前 N 个，逐个 GATT 连接，认 6E400002 特征值
    4. 找到后记住地址，写 logs/ble_addr.txt
连上后校验 GATT 特征值存在。

注意：**插 USB 时板子会枚举成 HID 复合设备（VID_303A&PID_1001），
此时 BLE 不广播**（11-20 实测：USB 在位时扫 29 个设备都找不到 NUS）。
用无线模式时要么拔掉 USB，要么确固件是双模。
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import time

from bleak import BleakClient, BleakScanner

try:
    import usb_link
except Exception:                      # 没装 pyserial / 没刷 CDC 固件都不影响 BLE
    usb_link = None

# 上次成功的地址只作提示；地址会变，不要依赖它。
ADDR = "14:C1:9F:3A:BB:35"
SERVICE = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
CHAR = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
ADDR_FILE = None
NAME_HINTS = ("esp32", "aimassist", "esp-hid")


def _addr_file():
    global ADDR_FILE
    if ADDR_FILE is None:
        try:
            import paths
            ADDR_FILE = os.path.join(str(paths.LOGS), "ble_addr.txt")
        except Exception:
            ADDR_FILE = ""
    return ADDR_FILE


def _load_addr():
    global ADDR
    p = _addr_file()
    try:
        with open(p, encoding="utf-8") as f:
            a = f.read().strip()
        if len(a) == 17 and a.count(":") == 5:
            ADDR = a
    except Exception:
        pass


def _save_addr(addr):
    try:
        with open(_addr_file(), "w", encoding="utf-8") as f:
            f.write(addr)
    except Exception:
        pass


_load_addr()


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


async def _has_char(addr, timeout=6):
    """连一下看有没有 NUS 写特征值。返回 (ok, services)。"""
    c = None
    try:
        c = BleakClient(addr, timeout=timeout)
        await c.connect()
        if not c.is_connected:
            return False, []
        try:
            ok = c.services.get_characteristic(CHAR) is not None
        except Exception:
            ok = False
        names = []
        try:
            names = [str(s.uuid) for s in c.services]
        except Exception:
            pass
        return ok, names
    except Exception:
        return False, []
    finally:
        if c is not None:
            try:
                await c.disconnect()
            except Exception:
                pass


async def discover(timeout=8, try_n=8):
    """扫描并返回"真的带 NUS 特征值"的地址。找不到返回 None。

    地址/名字都不可信，唯一可靠的身份是 GATT 服务本身。
    """
    try:
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    except Exception as e:
        _log("discover scan failed: %s" % str(e)[:80])
        return None
    rows = []
    for addr, (dev, adv) in found.items():
        rssi = adv.rssi if adv.rssi is not None else -999
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        name = (dev.name or "")
        rows.append((rssi, addr, name, uuids))
    rows.sort(reverse=True)
    _log("discover: %d devices" % len(rows))

    # 1) 广播里就带服务 UUID 的优先；2) 名字像 ESP 的其次；3) 再看最强的
    ordered = ([r for r in rows if SERVICE in r[3]]
               + [r for r in rows if any(h in r[2].lower() for h in NAME_HINTS)
                  and SERVICE not in r[3]]
               + [r for r in rows if SERVICE not in r[3]
                  and not any(h in r[2].lower() for h in NAME_HINTS)][:try_n])
    seen = set()
    for rssi, addr, name, _u in ordered:
        if addr in seen:
            continue
        seen.add(addr)
        ok, services = await _has_char(addr)
        if ok:
            _log("discover: board at %s (%r rssi=%s)" % (addr, name, rssi))
            return addr
    _log("discover: no NUS device among %d candidates" % len(seen))
    return None


class Session:
    def __init__(self, verbose=False):
        self.v = verbose
        self.client = None
        self.usb = None

    @property
    def transport(self):
        if self.usb is not None and self.usb.ok():
            return "usb:%s" % self.usb.port
        return "ble:%s" % ADDR

    async def _try_addr(self, addr, attempts=1, timeout=12):
        for attempt in range(1, attempts + 1):
            try:
                c = BleakClient(addr, timeout=timeout)
                await c.connect()
                if not c.is_connected:
                    continue
                try:
                    ch = c.services.get_characteristic(CHAR)
                except Exception:
                    ch = None
                if ch is None:
                    _log("特征值缺失 %s (attempt %d)" % (addr, attempt))
                    try:
                        await c.disconnect()
                    except Exception:
                        pass
                    await asyncio.sleep(1.5)
                    continue
                self.client = c
                _log("connected %s (attempt %d)" % (addr, attempt))
                return True
            except Exception as e:
                _log("connect %s attempt %d: %s" % (addr, attempt, str(e)[:60]))
                if attempt < attempts:
                    await asyncio.sleep(1.5)
        return False

    async def connect(self, allow_discover=True):
        """连板子。allow_discover=False 用于战斗中的快速重连（扫描太慢）。

        优先级：USB CDC（有线，100% 稳定）> BLE（无线，兜底）。
        """
        if self.usb is not None and self.usb.ok():
            return True
        if self.usb is None and usb_link is not None:
            try:
                lk = usb_link.Link()
                if lk.connect():
                    self.usb = lk
                    _log("transport: usb cdc on %s" % lk.port)
                    return True
                lk.close()
            except Exception as e:
                _log("usb probe failed: %s" % str(e)[:70])
        if self.client is not None and self.client.is_connected:
            return True
        global ADDR
        cached = ADDR
        if cached and await self._try_addr(cached, attempts=1):
            return True
        if not allow_discover:
            return False
        found = await discover()
        if not found:
            return False
        if found != cached:
            _log("board address changed: %s -> %s" % (cached, found))
        ADDR = found
        _save_addr(found)
        return await self._try_addr(found, attempts=2)

    # ---------------- 命令 ----------------
    async def _asend(self, text):
        if self.usb is not None and self.usb.ok():
            try:
                self.usb.send(text)
                return
            except Exception as e:
                _log("usb send failed: %s" % str(e)[:60])
                self.usb = None
        if self.client is None or not self.client.is_connected:
            # 战斗中的重连只走"上次地址"，不做扫描（扫描 8 秒 + 逐个试探
            # 会把一个回合直接耗光）。地址变了的情况交给启动时发现。
            if not await self.connect(allow_discover=False):
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
        if self.usb is not None and self.usb.ok():
            try:
                self.usb.click_at(x, y, hold_alt=hold_alt)
                return "ok"
            except Exception as e:
                _log("usb click_at failed: %s" % str(e)[:60])
                self.usb = None
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
            if self.usb is not None:
                self.usb.close()
        except Exception:
            pass
        self.usb = None
        try:
            if self.client is not None:
                await self.client.disconnect()
        except Exception:
            pass
        self.client = None
        return True


if __name__ == "__main__":
    import sys

    if "--discover" in sys.argv:
        _t = time.time()
        a = asyncio.run(discover())
        print("found: %s (%.1fs)" % (a, time.time() - _t))
        sys.exit(0 if a else 1)
    print("usage: python esp_shim.py --discover")
