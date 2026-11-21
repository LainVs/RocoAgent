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


def running(match="robot\\.py"):
    """返回命令行里匹配 match 的进程 PID 列表。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | "
             "Where-Object { $_.CommandLine -match '%s' } | "
             "ForEach-Object { $_.ProcessId }" % match],
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


def start_supervisor(minutes=0, no_input=False):
    """用看门狗启动：崩溃自动拉起，且整条链都脱离 shell。

    minutes > 0 时看门狗到点会自己停掉机器人并退出（空转测试用）。
    """
    pids = running("robot\\.py") + running("botsup\\.py")
    if pids:
        print("已经在跑: %s" % pids)
        return 0
    sup = os.path.join(str(paths.TOOLS), "botsup.py")
    if not os.path.exists(sup):
        print("找不到看门狗: %s" % sup)
        return 2
    args = [PY, "-W", "ignore", "-u", sup, "--seconds",
            str(int(minutes) * 60) if minutes else "0"]
    if no_input:
        args.append("--no-input")
    logf = open(os.path.join(LOGS, "supervisor.out.log"), "a", encoding="utf-8")
    p = subprocess.Popen(
        args, cwd=str(paths.TOOLS), stdin=subprocess.DEVNULL,
        stdout=logf, stderr=subprocess.STDOUT,
        creationflags=DETACHED | NEW_GROUP | NO_WINDOW)
    logf.close()
    print("看门狗已启动 PID %d（日志 logs\\supervisor.out.log）" % p.pid)
    time.sleep(4)
    print("确认 supervisor: %s" % running("botsup\\.py"))
    print("确认 robot     : %s" % running("robot\\.py"))
    return 0


def stop():
    # 必须按依赖顺序停：keepalive -> botsup -> robot。
    #
    # 踩过的坑（2026-11-21 09:30，用户报"停了又被拉起来"）
    # ------------------------------------------------
    # 原来这里只停 robot 和 botsup，**不知道 keepalive 的存在**：
    #     --stop -> robot 停了、botsup 停了
    #     -> keepalive 还活着，60 秒后发现 botsup 不在 -> 拉起
    #     -> botsup 拉起 robot -> 机器人又活了
    # 用户看到的现象就是"怎么停都停不掉"。
    #
    # 而且必须先停 keepalive：如果先停 robot，keepalive 会立刻
    # 发现并拉起，形成"杀了又活"的循环。
    keep = running("keepalive\\.py")
    sup = running("botsup\\.py")
    rob = running("robot\\.py")
    pids = sorted(set(keep + sup + rob))
    if not pids:
        print("没有在跑")
        return 0

    # 1) keepalive 先死（它会拉起 botsup）
    for pid in keep:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, creationflags=NO_WINDOW)
    if keep:
        time.sleep(0.5)

    # 2) botsup 再死（它会拉起 robot）
    for pid in sup:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, creationflags=NO_WINDOW)
    if sup:
        time.sleep(0.5)

    # 3) robot 最后
    for pid in rob:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, creationflags=NO_WINDOW)

    print("已停止: %s" % pids)
    for fn in (PIDF, os.path.join(LOGS, "robot.lock"),
               os.path.join(LOGS, "keepalive.pid")):
        try:
            os.remove(fn)
        except OSError:
            pass
    return 0


def status():
    bots = running("robot\\.py")
    sups = running("botsup\\.py")
    keep = running("keepalive\\.py")
    print("robot: %s" % (bots if bots else "未运行"))
    print("supervisor: %s" % (sups if sups else "未运行"))
    print("keepalive: %s" % (keep if keep else "未运行"))
    for name in ("bot_stdout.log", "supervisor_restarts.log", "crash.log"):
        try:
            age = time.time() - os.path.getmtime(os.path.join(LOGS, name))
            print("%-22s 最后更新 %.0f 秒前" % (name, age))
        except OSError:
            print("%-22s 不存在" % name)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--supervisor", action="store_true",
                    help="用 botsup 看门狗启动（推荐，崩溃自动拉起）")
    ap.add_argument("--minutes", type=int, default=0,
                    help="看门狗预算（分钟，0=不限）；到点会连同机器人一起停")
    ap.add_argument("--no-input", action="store_true",
                    help="空转模式：只感知不操作（稳定性测试）")
    a = ap.parse_args()
    if a.stop:
        sys.exit(stop())
    if a.status:
        sys.exit(status())
    if a.supervisor:
        sys.exit(start_supervisor(a.minutes, a.no_input))
    sys.exit(start())
