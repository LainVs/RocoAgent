"""run_bot.py - 以完全独立的进程启动机器人。

为什么需要这个
--------------
用 Start-Process / cmd start / WMI 启动的 python，在 OpenCode 的 shell
会话结束时会被一起回收（日志停在某个时刻，没有 traceback，stderr 是空的）。
用 DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP 创建的进程不挂在任何
控制台上，父进程退出也不影响它。

用法：
    python run_bot.py              # 启动
    python run_bot.py --stop       # 停止
    python run_bot.py --status     # 查看
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paths

SRC = str(paths.SRC)
LOGS = str(paths.LOGS)
PY = sys.executable
LOG = os.path.join(LOGS, "bot_stdout.log")
PIDF = os.path.join(LOGS, "bot.pid")

DETACHED = 0x00000008
NEW_GROUP = 0x00000200
NO_WINDOW = 0x08000000


def running():
    """返回正在跑的 robot.py 的 PID 列表。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | "
             "Where-Object { $_.CommandLine -match 'robot\\.py' } | "
             "ForEach-Object { $_.ProcessId }"],
            capture_output=True, timeout=30,
            creationflags=NO_WINDOW)
        text = (out.stdout or b"").decode("utf-8", "replace")
        return [int(x) for x in text.split() if x.strip().isdigit()]
    except Exception:
        return []


def start():
    pids = running()
    if pids:
        print("已经在跑: %s" % pids)
        return 0
    for f in (PIDF,):
        try:
            os.remove(f)
        except OSError:
            pass

    fh = open(LOG, "a", encoding="utf-8")
    cmd = [PY, "-W", "ignore", "-u", "robot.py", "--seconds", "28800"]
    p = subprocess.Popen(
        cmd, cwd=SRC, stdin=subprocess.DEVNULL,
        stdout=fh, stderr=subprocess.STDOUT,
        creationflags=DETACHED | NEW_GROUP | NO_WINDOW)
    fh.close()
    print("已启动 PID %d（独立进程，不会随 shell 退出）" % p.pid)
    time.sleep(3)
    print("确认: %s" % running())
    return 0


def stop():
    pids = running()
    if not pids:
        print("没有在跑")
        return 0
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, creationflags=NO_WINDOW)
    print("已停止: %s" % pids)
    try:
        os.remove(PIDF)
    except OSError:
        pass
    return 0


def status():
    pids = running()
    print("运行中: %s" % pids if pids else "未运行")
    try:
        age = time.time() - os.path.getmtime(LOG)
        print("日志最后更新: %.0f 秒前" % age)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.stop:
        sys.exit(stop())
    if a.status:
        sys.exit(status())
    sys.exit(start())
