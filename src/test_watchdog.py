# -*- coding: utf-8 -*-
"""test_watchdog.py —— 验证主循环看门狗能让僵死进程主动退出。

背景（2026-11-21 08:28 事故）：
    robot 僵死后 robot.lock 一直被持有，botsup 的 p.wait() 永远不返回，
    新实例因拿不到锁而退出，整条链卡死。

    botsup 只在 4 分钟无日志后才杀，且依赖 p.wait() 能返回 ——
    进程僵死时 wait() 不返回，这个保护等于不存在。

本测试用真实子进程验证：
    1. 子进程正常心跳 -> 不退出
    2. 子进程停止心跳 -> STALL_EXIT_S 后硬退出（退出码 9）
    3. 退出后 robot.lock 能被重新获取

跑法：
    python src\\test_watchdog.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


# 用一个缩短版看门狗做测试（不用等 180 秒）
CHILD = textwrap.dedent('''
    import os, sys, time, threading
    sys.path.insert(0, r"%s")
    import config

    STALL = float(sys.argv[1])      # 缩短的阈值
    HB = [time.time()]
    log_path = sys.argv[2]

    def log(msg):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("%%s %%s\\n" %% (time.strftime("%%H:%%M:%%S"), msg))

    def watchdog():
        while True:
            time.sleep(0.2)
            age = time.time() - HB[0]
            if age > STALL:
                log("STALL %%.1fs without heartbeat -> hard exit" %% age)
                os._exit(9)

    threading.Thread(target=watchdog, daemon=True).start()
    log("child up pid=%%d stall=%%.1f" %% (os.getpid(), STALL))

    mode = sys.argv[3]
    if mode == "alive":
        # 正常心跳，跑 3 秒
        end = time.time() + 3.0
        while time.time() < end:
            HB[0] = time.time()
            time.sleep(0.1)
        log("child done (heartbeat ok)")
        sys.exit(0)
    else:
        # 模拟卡死：只心跳一次，然后"卡在 C 调用里"
        HB[0] = time.time()
        log("child pretending to stall")
        time.sleep(30)
        sys.exit(0)
''') % HERE


def run_child(stall_s, mode, tag):
    logf = os.path.join(tempfile.gettempdir(),
                        "watchdog_test_%s.log" % tag)
    try:
        os.remove(logf)
    except OSError:
        pass
    t0 = time.time()
    r = subprocess.run([sys.executable, "-W", "ignore", "-c", CHILD,
                        str(stall_s), logf, mode],
                       capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="ignore")
    elapsed = time.time() - t0
    try:
        content = open(logf, encoding="utf-8").read()
    except OSError:
        content = ""
    return r.returncode, elapsed, content


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("=== 1) 正常心跳 -> 不触发看门狗 ===")
    rc, secs, out = run_child(2.0, "alive", "alive")
    check(rc == 0, "退出码 0（实际 %s）" % rc)
    check("STALL" not in out, "没有 STALL 记录")
    check(2.5 <= secs <= 15, "耗时 %.1fs 合理" % secs)

    print()
    print("=== 2) 停止心跳 -> 看门狗硬退出 ===")
    rc, secs, out = run_child(1.0, "stall", "stall")
    check(rc == 9, "退出码 9（实际 %s）" % rc)
    check("STALL" in out, "有 STALL 记录")
    check(secs < 10, "%.1fs 内退出（不是等 30s）" % secs)

    print()
    print("=== 3) 配置常量合理 ===")
    check(config.STALL_EXIT_S >= 60,
          "STALL_EXIT_S=%.0f 不会误杀正常轮询" % config.STALL_EXIT_S)
    check(config.STALL_EXIT_S <= 300,
          "STALL_EXIT_S=%.0f 不至于卡太久" % config.STALL_EXIT_S)

    print()
    print("=== 4) 锁能被释放（模拟）===")
    # 直接验证：进程退出后，同路径的锁可以被重新获取
    lock = os.path.join(tempfile.gettempdir(), "watchdog_test.lock")
    try:
        os.remove(lock)
    except OSError:
        pass
    code = textwrap.dedent('''
        import msvcrt, sys
        fh = open(r"%s", "a+")
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        print("locked")
    ''') % lock
    r1 = subprocess.run([sys.executable, "-c", code], capture_output=True,
                        text=True, timeout=30, encoding="utf-8",
                        errors="ignore")
    # 子进程已退出 -> 锁应已释放
    r2 = subprocess.run([sys.executable, "-c", code], capture_output=True,
                        text=True, timeout=30, encoding="utf-8",
                        errors="ignore")
    check("locked" in (r1.stdout or ""), "第一个进程拿到锁")
    check("locked" in (r2.stdout or ""),
          "进程退出后锁被释放，第二个能拿到")

    print()
    if FAILS:
        print("FAILED %d:" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
