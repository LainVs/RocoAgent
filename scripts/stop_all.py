# -*- coding: utf-8 -*-
"""stop_all.py —— 按命令行精确停止机器人的所有相关进程。

为什么不用 Stop-Process -Name python
------------------------------------
本机还有 ui_server.py 在并行开发（B 会话），按进程名批量杀会连带干掉它。
这里按**命令行内容**精确匹配，只杀机器人的三件套：
    robot.py / botsup.py / keepalive.py

顺序很重要：先杀 keepalive（它会拉起 botsup），
再杀 botsup（它会拉起 robot），最后杀 robot。

跑法：
    python scripts\\stop_all.py
    python scripts\\stop_all.py --dry   # 只列出，不杀
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time

PATTERNS = ("keepalive.py", "botsup.py", "robot.py")

PS = r"""
$out = @()
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' } | ForEach-Object {
    $out += [PSCustomObject]@{
        pid = $_.ProcessId
        ppid = $_.ParentProcessId
        cmd = $_.CommandLine
    }
}
$out | ConvertTo-Json -Depth 3
"""


def list_procs():
    enc = base64.b64encode(PS.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", timeout=60)
    try:
        d = json.loads(r.stdout or "[]")
    except Exception:
        return []
    return d if isinstance(d, list) else [d]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    dry = "--dry" in sys.argv
    procs = list_procs()

    # 按类别分组
    groups = {p: [] for p in PATTERNS}
    for pr in procs:
        cmd = pr.get("cmd") or ""
        for pat in PATTERNS:
            if pat in cmd:
                groups[pat].append(pr)
                break

    print("=== 当前机器人相关进程 ===")
    for pat in PATTERNS:
        items = groups[pat]
        if not items:
            print("  %-14s 无" % pat)
            continue
        print("  %-14s %s" % (pat, [x.get("pid") for x in items]))

    if dry:
        print()
        print("（--dry：只列出，不杀）")
        return 0

    # 按依赖顺序杀：keepalive -> botsup -> robot
    print()
    print("=== 停止（按依赖顺序）===")
    for pat in PATTERNS:
        for pr in groups[pat]:
            pid = pr.get("pid")
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=20)
                print("  已停 %s (pid=%s)" % (pat, pid))
            except Exception as e:
                print("  停 %s (pid=%s) 失败: %s" % (pat, pid, e))
        if groups[pat]:
            time.sleep(1.5)

    # 清锁文件
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for fn in ("robot.lock", "keepalive.pid"):
        p = os.path.join(root, "logs", fn)
        try:
            os.remove(p)
            print("  已删 logs/%s" % fn)
        except OSError:
            pass

    time.sleep(2)
    print()
    print("=== 复查 ===")
    left = list_procs()
    alive = []
    for pr in left:
        cmd = pr.get("cmd") or ""
        if any(p in cmd for p in PATTERNS):
            alive.append((pr.get("pid"), cmd[:60]))
    if alive:
        print("!! 仍有残留：")
        for pid, cmd in alive:
            print("   pid=%s %s" % (pid, cmd))
        return 1
    print("全部已停")
    return 0


if __name__ == "__main__":
    sys.exit(main())
