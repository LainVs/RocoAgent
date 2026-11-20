# -*- coding: utf-8 -*-
"""keepalive.py —— 最外层守护：确保 botsup（看门狗）一直活着。

为什么需要
----------
现有三层是：robot.py <- botsup.py（看门狗）<- 人工启动。
但 botsup 自己被杀（例如别的脚本按进程名批量 Stop-Process python），
就再没人拉起来了 —— 实测 2026-11-21 05:32 之后机器人静默 461 秒。

本脚本是最外一层：每 N 秒检查 botsup 是否在跑，不在就重新拉起。
它只做这一件事，代码极简，出问题的概率最低。

用法
----
    pythonw src\\keepalive.py          # 后台常驻（推荐）
    python src\\keepalive.py --once    # 只检查一次（配合计划任务）
    python src\\keepalive.py --status  # 看状态
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

LOGS = os.path.join(ROOT, "logs")
LOG = os.path.join(LOGS, "keepalive.log")
PIDFILE = os.path.join(LOGS, "keepalive.pid")

CHECK_S = 60          # 检查间隔
PY = sys.executable
DETACHED = 0x00000008
NEW_GROUP = 0x00000200
NO_WINDOW = 0x08000000


def _log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def _pids(pattern):
    """找命令行含 `pattern` 的 python 进程 PID（只读，不杀）。

    四个坑（都是实测踩到的）：
      1. wmic 在 Windows 11 已移除（FileNotFoundError）
      2. `-Command` 传参时双引号会被剥离，`-Filter "Name='python.exe'"`
         变成无效查询（HRESULT 0x80041017）
      3. `$_` 在多层引号里容易被吃掉
      4. `Select-Object -ExpandProperty` 在 -Command 里同样受转义影响

    最终方案：**不传 -Filter，不用 $_**，改用 Get-Process 的 Name 过滤 +
    CIM 全量取 CommandLine，并用 -EncodedCommand 避开一切转义。
    """
    import base64
    ps = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like "
        "'*%s*' } | ForEach-Object { $_.ProcessId }" % pattern
    )
    enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-EncodedCommand", enc],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="ignore").stdout
    except Exception:
        return []
    pids = []
    for line in (out or "").splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def _sup_alive():
    return bool(_pids("botsup.py"))


def _start_sup():
    # botsup.py 在 paths.TOOLS（外部目录 D:\Tools），不在项目里。
    # 实测 2026-11-21 06:00：拼成 ROOT/tools/botsup.py 会一直
    # "找不到 botsup.py，无法拉起"，机器人静默 20 分钟。
    import paths
    sup = os.path.join(str(paths.TOOLS), "botsup.py")
    if not os.path.exists(sup):
        _log("找不到 botsup.py: %s" % sup)
        return False
    try:
        logf = open(os.path.join(LOGS, "supervisor.out.log"), "a",
                    encoding="utf-8")
        subprocess.Popen(
            [PY, "-W", "ignore", "-u", sup, "--seconds", "0"],
            cwd=str(paths.TOOLS),
            stdin=subprocess.DEVNULL, stdout=logf,
            stderr=subprocess.STDOUT,
            creationflags=DETACHED | NEW_GROUP | NO_WINDOW)
        logf.close()
        _log("botsup 不在，已重新拉起")
        return True
    except Exception as e:
        _log("拉起 botsup 失败: %s: %s" % (type(e).__name__, e))
        return False


def main():
    if "--status" in sys.argv:
        sup = _pids("botsup.py")
        rob = _pids("robot.py")
        print("botsup: %s" % (sup or "未运行"))
        print("robot : %s" % (rob or "未运行"))
        return 0

    if "--once" in sys.argv:
        if not _sup_alive():
            _start_sup()
            print("已拉起 botsup")
        else:
            print("botsup 正常")
        return 0

    # 常驻模式：单实例锁（防止重复启动）
    #
    # 实测（2026-11-21 05:56）：Start-Process 一次会留下两个 python 进程
    # （外层 + 子进程），多个 keepalive 会互相干扰。用 pidfile + 存活检查
    # 保证只有一个在跑。
    if os.path.exists(PIDFILE):
        try:
            old = int(open(PIDFILE).read().strip())
            # 检查旧进程是否还活着
            alive = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-Process -Id %d -ErrorAction SilentlyContinue) -ne $null"
                 % old],
                capture_output=True, text=True, timeout=15).stdout.strip()
            if alive.lower().startswith("true"):
                print("已有 keepalive 在跑 (pid=%d)，退出" % old)
                return 0
        except Exception:
            pass
    try:
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    _log("keepalive 启动 pid=%d 间隔=%ds" % (os.getpid(), CHECK_S))
    while True:
        try:
            if not _sup_alive():
                _start_sup()
        except Exception as e:
            _log("检查异常: %s: %s" % (type(e).__name__, e))
        time.sleep(CHECK_S)


if __name__ == "__main__":
    sys.exit(main())
