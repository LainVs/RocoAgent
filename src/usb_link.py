"""usb_link.py - 有线命令通道（USB CDC），比 BLE 稳定得多。

背景
----
BLE 的问题不是"连不上"，而是 100% 不可保证：地址会变、广播会停、
还可能被手机之类的中心占着。板子本来就用 USB 供电，同一根线完全可以
再开一个 CDC 串口专门走指令：

    一条 USB 线 = HID 键盘/鼠标（游戏收到的输入）
                + CDC 串口（robot 发的 KEY/CLICK/MOVESLOW）

固件见 firmware/blue_cdc。协议是行分隔 ASCII，每条命令带回执：

    -> PING\\n              <- OK PING
    -> KEY:f\\n             <- OK KEY:f
    -> CLICK:60\\n          <- OK CLICK:60
    -> MOVESLOW:dx,dy,ms\\n <- OK MOVESLOW:...

没有回执就重发（最多 3 次）；串口找不到就返回 None，调用方自动退回 BLE。
"""
from __future__ import annotations

import ctypes
import time

try:
    import serial
    from serial.tools import list_ports
except Exception:                       # pyserial 不在也不影响 BLE 路径
    serial = None
    list_ports = None

VID = 0x303A                            # Espressif
PID = 0x1001                            # USB-Serial/JTAG 与常见 CDC 复合体
CURSOR_SCALE = 3.5                      # 和 esp_shim 一致：板子约 3.5 屏幕像素/单位


def _log(msg):
    try:
        import os
        import paths
        p = os.path.join(str(paths.LOGS), "usb_link.log")
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


def find_port():
    """返回板子的 CDC 串口名（COMx），没有就 None。

    只认 VID/PID，不认 COM 号：换 USB 口/重刷固件后 COM 号会变。
    """
    if list_ports is None:
        return None
    try:
        cands = list(list_ports.comports())
    except Exception:
        return None
    for p in cands:
        try:
            if p.vid == VID and p.pid == PID:
                return p.device
        except Exception:
            continue
    # 个别驱动不报 vid/pid，用描述兜底
    for p in cands:
        d = (p.description or "").lower()
        if "usb" in d and ("serial" in d or "串行" in d or "cdc" in d):
            return p.device
    return None


class Link:
    def __init__(self, port=None):
        self.port = port or None
        self.ser = None
        self.last_ok = 0.0

    # ---------------------------------------------------------------- open
    def connect(self, timeout=2.0):
        if self.ser is not None and self.ser.is_open:
            return True
        port = self.port or find_port()
        if not port:
            return False
        try:
            self.ser = serial.Serial(port, 115200, timeout=0.5,
                                     write_timeout=1.0)
            self.ser.reset_input_buffer()
        except Exception as e:
            _log("open %s failed: %s" % (port, str(e)[:70]))
            self.ser = None
            return False
        self.port = port
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._cmd("PING", timeout=0.4, retries=1):
                _log("usb cdc up on %s" % port)
                return True
            time.sleep(0.2)
        _log("usb cdc open but no PING reply on %s" % port)
        self.close()
        return False

    def ok(self):
        return self.ser is not None and self.ser.is_open

    # --------------------------------------------------------------- proto
    def _cmd(self, text, timeout=1.0, retries=2):
        if not self.ok():
            return False
        want = "OK " + text
        for _ in range(max(1, retries)):
            try:
                self.ser.reset_input_buffer()
                self.ser.write((text + "\n").encode("ascii", "ignore"))
                self.ser.flush()
                deadline = time.time() + timeout
                while time.time() < deadline:
                    line = self.ser.readline().decode("ascii", "ignore").strip()
                    if not line:
                        continue
                    if line.startswith(want):
                        self.last_ok = time.time()
                        return True
                    # 固件可能先把上一条的 OK 发过来，忽略即可
            except Exception as e:
                _log("cmd %s failed: %s" % (text, str(e)[:60]))
                try:
                    self.close()
                except Exception:
                    pass
                if not self.connect():
                    return False
        _log("cmd %s: no ack" % text)
        return False

    # -------------------------------------------------------------- input
    def send(self, text):
        if not self._cmd(str(text)):
            raise RuntimeError("usb link offline")
        return "ok"

    def key(self, k):
        return self.send("KEY:%s" % k)

    def click(self, ms=60):
        return self.send("CLICK:%d" % int(ms))

    def click_at(self, x, y, hold_alt=False):
        """和 esp_shim.click_at 同一套相对移动；只是搬到了串口上。"""
        if not self.ok():
            raise RuntimeError("usb link offline")
        for _ in range(4):
            cx, cy = _cursor()
            dx, dy = int(x) - cx, int(y) - cy
            if abs(dx) <= 3 and abs(dy) <= 3:
                break
            ex, ey = round(dx / CURSOR_SCALE), round(dy / CURSOR_SCALE)
            if ex == 0 and ey == 0:
                ex = 1 if dx > 0 else -1
                ey = 1 if dy > 0 else -1
            dur = 100 + min(120, max(abs(ex), abs(ey)) // 4)
            self._cmd("MOVESLOW:%d,%d,%d" % (ex, ey, dur), timeout=0.6)
            time.sleep(0.05 + dur / 1000.0)
        if hold_alt:
            self._cmd("KEY:altdown", timeout=0.3)
            time.sleep(0.2)
        self._cmd("CLICK:60", timeout=0.4)
        time.sleep(0.08)
        if hold_alt:
            self._cmd("KEY:altup", timeout=0.3)
        return "ok"

    def close(self):
        try:
            if self.ser is not None:
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        return True


if __name__ == "__main__":
    p = find_port()
    print("port:", p)
    lk = Link(p)
    print("connect:", lk.connect())
    if lk.ok():
        print("ping:", lk._cmd("PING"))
        lk.close()
