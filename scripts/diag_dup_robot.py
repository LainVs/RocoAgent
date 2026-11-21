# -*- coding: utf-8 -*-
"""诊断：确认是否有多个 robot.py 实例同时运行。

问题（2026-11-21 08:08）：turns.jsonl 同一分钟内出现两组互相独立的记录
（foe=34.3 和 foe=92.2），说明有多个进程在写同一个日志。
"""
import base64
import json
import subprocess
import sys

PS = r"""
$out = @()
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' } | ForEach-Object {
    $out += [PSCustomObject]@{
        pid = $_.ProcessId
        ppid = $_.ParentProcessId
        created = "$($_.CreationDate)"
        cmd = $_.CommandLine
    }
}
$out | ConvertTo-Json -Depth 3
"""

enc = base64.b64encode(PS.encode("utf-16-le")).decode("ascii")
r = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="ignore")
if r.returncode != 0:
    print("PowerShell 失败:", r.stderr[:300])
    sys.exit(1)

try:
    procs = json.loads(r.stdout or "[]")
except Exception as e:
    print("解析失败:", e)
    print(r.stdout[:500])
    sys.exit(1)

if isinstance(procs, dict):
    procs = [procs]

robots = []
for p in procs:
    cmd = p.get("cmd") or ""
    if "robot.py" in cmd:
        robots.append(p)

print("=== robot.py 进程 ===")
for p in robots:
    print("  pid=%s ppid=%s created=%s" % (
        p.get("pid"), p.get("ppid"), p.get("created")))

print()
pids = {p.get("pid") for p in robots}
roots = [p for p in robots if p.get("ppid") not in pids]
print("独立实例数（父进程不是另一个 robot）: %d" % len(roots))
for p in roots:
    print("  pid=%s" % p.get("pid"))

print()
print("=== 判定 ===")
if len(roots) > 1:
    print("!! 有 %d 个独立 robot 实例在同时跑 -> 会互相抢键、写乱日志" % len(roots))
    print("   应当只保留一个。注意不要按进程名批量杀（会连带 ui_server）。")
else:
    print("正常：只有一个独立实例（其余是它的子进程）")

print()
print("=== 其他 python 进程（参考）===")
for p in procs:
    cmd = p.get("cmd") or ""
    if "robot.py" in cmd:
        continue
    tag = "ui_server" if "ui_server" in cmd else (
        "keepalive" if "keepalive" in cmd else (
            "botsup" if "botsup" in cmd else "other"))
    print("  pid=%s %-10s %s" % (p.get("pid"), tag,
                                 cmd[:70] if cmd else ""))
