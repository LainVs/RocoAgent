# -*- coding: utf-8 -*-
"""验证 robot.lock 单实例锁是否真的生效。

背景（2026-11-21 08:27）：
    两个 botsup 各拉起一个 robot：
        botsup 470252 -> robot 477052
        botsup 479888 -> robot 478172
    两个实例同时跑，抢键盘、写乱 turns.jsonl。

    robot.py 里调用了 acquire_lock()，理论上第二个应该退出（rc=3）。
    但它没退出 —— 需要确认锁到底有没有工作。

跑法：
    python scripts\\test_lock.py
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "src"))

import config

print("LOCK_PATH = %s" % config.LOCK_PATH)
print("文件存在: %s" % os.path.exists(config.LOCK_PATH))
print()

# 场景 1：当前进程能否拿到锁
print("=== 场景 1：主进程尝试拿锁 ===")
ok = config.acquire_lock()
print("  acquire_lock() -> %s" % ok)
if not ok:
    print("  -> 已有实例持有锁（符合预期，因为机器人正在跑）")
    print("  这一步说明锁**是生效的**：新的进程拿不到。")
else:
    print("  !! 拿到了锁，说明当前没有实例持有 -> 锁可能有问题")

print()
print("=== 场景 2：子进程尝试拿锁（模拟 botsup 拉起第二个 robot）===")
code = (
    "import sys, os;"
    "sys.path.insert(0, r'%s');"
    "import config;"
    "print('child acquire ->', config.acquire_lock())"
) % os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src")

r = subprocess.run([sys.executable, "-W", "ignore", "-c", code],
                   capture_output=True, text=True, timeout=60,
                   encoding="utf-8", errors="ignore")
print("  stdout: %s" % (r.stdout or "").strip())
print("  stderr: %s" % (r.stderr or "").strip()[:200])

print()
print("=== 判定 ===")
if not ok and "False" in (r.stdout or ""):
    print("锁正常：主进程和子进程都拿不到（已被运行中的机器人持有）")
elif ok and "True" in (r.stdout or ""):
    print("!! 锁失效：两个进程都能拿到")
else:
    print("结果需要人工判断（见上面输出）")
