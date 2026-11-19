# -*- coding: utf-8 -*-
"""机器人守护进程：挂了就拉起来，看到 restart.flag 就换成新代码。

为什么要它：
    机器人今晚挂过一次（01:10，日志停在 FOCUS LOST 之后），之后就一直没人
    重启。手工重启一次只能撑到下一次崩，跑一整晚必须有人看着。

规则：
    - 只杀 robot.py，绝不碰 espfast（BLE 守护进程，杀了要重新连板子）
    - 重启前检查 9998 端口，espfast 不在就先把它拉起来
    - 连续崩溃要退避：1 分钟内崩 3 次就等 2 分钟，避免无限重启刷屏
    - logs/restart.flag 存在 = 有新版本要加载，主动重启一次再删掉

用法：
    pythonw watchdog.py        （后台常驻）
    python watchdog.py --once  （只检查一次，调试用）
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
LOG_DIR = os.path.join(ROOT, "logs")
BOT_LOG = os.path.join(LOG_DIR, "bot_stdout.log")
PID_FILE = os.path.join(LOG_DIR, "bot.pid")
WD_PID = os.path.join(LOG_DIR, "watchdog.pid")
WD_LOG = os.path.join(LOG_DIR, "watchdog.log")
FLAG = os.path.join(LOG_DIR, "restart.flag")
ESPAST = r"D:\Tools\espfast.py"
PY = sys.executable
CREATE_NO_WINDOW = 0x08000000
DETACHED = 0x00000008


def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(WD_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def port_open(port=9998):
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=1.0)
        s.close()
        return True
    except OSError:
        return False


def pid_alive(pid):
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259
    finally:
        k32.CloseHandle(h)


def bot_pid():
    try:
        with open(PID_FILE) as f:
            return int((f.read() or "0").strip()) or None
    except (OSError, ValueError):
        return None


def kill_bot():
    pid = bot_pid()
    if not pid:
        return
    if pid_alive(pid):
        log("kill bot pid %d" % pid)
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    time.sleep(1.0)


def ensure_espfast():
    if port_open():
        return True
    log("espfast down -> starting")
    try:
        subprocess.Popen([PY, "-W", "ignore", "-u", ESPAST],
                         cwd=os.path.dirname(ESPAST),
                         stdin=subprocess.DEVNULL,
                         stdout=open(os.path.join(LOG_DIR, "espfast.log"), "a",
                                     encoding="utf-8"),
                         stderr=subprocess.STDOUT,
                         creationflags=DETACHED)
    except Exception as e:
        log("espfast start failed: %s" % e)
        return False
    for _ in range(20):
        time.sleep(1.0)
        if port_open():
            log("espfast up")
            return True
    log("espfast still down after 20s")
    return False


def monitor_alive():
    """靠心跳文件判断：wmic 在 Win11 上不一定有，命令行匹配也不可靠。"""
    try:
        p = os.path.join(LOG_DIR, "monitor.beat")
        return time.time() - os.path.getmtime(p) < 120.0
    except OSError:
        return False


def ensure_monitor():
    if monitor_alive():
        return
    try:
        subprocess.Popen([PY, "-W", "ignore", "monitor.py"], cwd=SRC,
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=DETACHED)
        log("monitor restarted")
    except Exception as e:
        log("monitor start failed: %s" % e)


def start_bot():
    fh = open(BOT_LOG, "a", encoding="utf-8")
    cmd = [PY, "-W", "ignore", "-u", "robot.py", "--seconds", "7200"]
    if os.path.exists(os.path.join(LOG_DIR, "assist.flag")):
        cmd.append("--assist")
        log("mode: assist (operator plays, bot is the safety net)")
    p = subprocess.Popen(cmd,
                         cwd=SRC, stdin=subprocess.DEVNULL,
                         stdout=fh, stderr=subprocess.STDOUT,
                         creationflags=DETACHED)
    fh.close()
    log("bot started pid %d" % p.pid)
    return p.pid


def main():
    once = "--once" in sys.argv
    try:
        with open(WD_PID, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    log("watchdog up pid %d" % os.getpid())

    crashes = []
    while True:
        try:
            pid = bot_pid()
            alive = bool(pid and pid_alive(pid))
            flag = os.path.exists(FLAG)
            if flag and alive:
                log("restart flag -> reloading new code")
                kill_bot()
                alive = False
            if flag:
                try:
                    os.remove(FLAG)
                except OSError:
                    pass
            ensure_monitor()
            if not alive:
                now = time.time()
                crashes = [t for t in crashes if now - t < 60.0]
                if len(crashes) >= 3:
                    log("3 crashes in 60s -> backing off 120s")
                    time.sleep(120)
                    crashes = []
                crashes.append(now)
                if ensure_espfast():
                    start_bot()
                    time.sleep(5)
            if once:
                return 0
            time.sleep(10)
        except KeyboardInterrupt:
            return 0
        except Exception as e:
            log("watchdog error: %s: %s" % (type(e).__name__, e))
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
