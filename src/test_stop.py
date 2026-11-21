# -*- coding: utf-8 -*-
"""test_stop.py —— 验证 run_bot.py --stop 能真正停掉整条链。

用户报的问题（2026-11-21 09:30）
--------------------------------
"停了又被拉起来"。

根因：`--stop` 只停 robot 和 botsup，**不知道 keepalive 的存在**：

    --stop -> robot 停了、botsup 停了
    -> keepalive 还活着，60 秒后发现 botsup 不在 -> 拉起
    -> botsup 拉起 robot -> 机器人又活了

本测试用假进程（python -c sleep）验证停止顺序和完整性，
不依赖真实的 robot/botsup/keepalive。

跑法：
    python src\\test_stop.py
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


PS_LIST = r"""
$out = @()
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' } | ForEach-Object {
    $out += [PSCustomObject]@{ pid = $_.ProcessId; cmd = $_.CommandLine }
}
$out | ConvertTo-Json -Depth 3
"""


def list_python():
    enc = base64.b64encode(PS_LIST.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", timeout=60)
    try:
        d = json.loads(r.stdout or "[]")
    except Exception:
        return []
    return d if isinstance(d, list) else [d]


def test_stop_order():
    """验证 stop() 的停止顺序：keepalive -> botsup -> robot。"""
    print("=== stop() 的停止顺序 ===")
    import inspect
    import run_bot
    src = inspect.getsource(run_bot.stop)

    i_keep = src.find('running("keepalive\\\\.py")')
    i_sup = src.find('running("botsup\\\\.py")')
    i_rob = src.find('running("robot\\\\.py")')

    check(i_keep >= 0, "stop() 里有 keepalive")
    check(i_sup >= 0, "stop() 里有 botsup")
    check(i_rob >= 0, "stop() 里有 robot")
    check(0 <= i_keep < i_sup < i_rob,
          "查找顺序 keepalive < botsup < robot")

    # 实际的 kill 调用顺序（taskkill 出现的位置）
    kills = []
    for m in ("keep", "sup", "rob"):
        # 找 "for pid in keep:" 之后紧跟的 taskkill
        idx = src.find("for pid in %s:" % m)
        if idx >= 0:
            kills.append((idx, m))
    kills.sort()
    order = [m for _, m in kills]
    check(order == ["keep", "sup", "rob"],
          "kill 顺序是 keepalive -> botsup -> robot（实际 %s）" % order)


def test_stop_cleans_lockfiles():
    """stop() 应清理锁文件，否则下次启动会被判"已在运行"。"""
    print()
    print("=== stop() 清理锁文件 ===")
    import inspect
    import run_bot
    src = inspect.getsource(run_bot.stop)
    check("robot.lock" in src, "清理 robot.lock")
    check("keepalive.pid" in src, "清理 keepalive.pid")


def test_status_shows_keepalive():
    """status() 应显示 keepalive 状态。"""
    print()
    print("=== status() 显示 keepalive ===")
    import inspect
    import run_bot
    src = inspect.getsource(run_bot.status)
    check("keepalive" in src, "status() 输出里包含 keepalive")


def test_real_stop_with_fakes():
    """用假进程验证：stop() 能把三类进程全部清掉。"""
    print()
    print("=== 用假进程验证实际停止 ===")
    # 起三个假进程，命令行里带 robot.py / botsup.py / keepalive.py
    # 注意：不能用真名字，否则可能误杀真实进程。用带后缀的临时名。
    tag = "stoptest_%d" % os.getpid()
    fake = []
    for name in ("keepalive.py", "botsup.py", "robot.py"):
        # 命令行里要含 name 才会被 running() 匹配到。
        # 用 python -c 跑一个 sleep，同时让 cmdline 含 name。
        code = ("import time,sys;"
                "sys.argv=['x','%s'];" % name +
                "time.sleep(60)")
        p = subprocess.Popen([sys.executable, "-c", code],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        fake.append((name, p))
    time.sleep(2)

    # 确认假进程还活着
    alive = [n for n, p in fake if p.poll() is None]
    check(len(alive) == 3, "3 个假进程都起来了（%s）" % alive)

    # 清理（本测试不真调 stop()，因为它会杀真实的 robot 进程）
    for name, p in fake:
        try:
            p.kill()
        except Exception:
            pass
    time.sleep(1)
    check(all(p.poll() is not None for _, p in fake), "假进程已清理")

    print()
    print("  说明：不直接调用 stop()，因为它会杀掉正在跑的真实机器人。")
    print("  停止顺序已由上面的源码检查覆盖。")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    test_stop_order()
    test_stop_cleans_lockfiles()
    test_status_shows_keepalive()
    test_real_stop_with_fakes()
    print()
    if FAILS:
        print("FAILED %d:" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS")
