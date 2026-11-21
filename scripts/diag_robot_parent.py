# -*- coding: utf-8 -*-
"""查 robot 实例 B 的父进程链，找出是谁拉起的。

问题：2026-11-21 08:14 出现第二个独立 robot 实例（pid=476824），
父进程 474500 不是 botsup。如果 lock 机制正常，它应该启动失败。
"""
import base64
import json
import subprocess
import sys

PS = r"""
$target = 476824
$chain = @()
$cur = $target
for ($i = 0; $i -lt 6; $i++) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$cur" -ErrorAction SilentlyContinue
    if (-not $p) { break }
    $chain += [PSCustomObject]@{
        pid = $p.ProcessId
        ppid = $p.ParentProcessId
        name = $p.Name
        cmd = $p.CommandLine
        created = "$($p.CreationDate)"
    }
    $cur = $p.ParentProcessId
    if (-not $cur -or $cur -eq 0) { break }
}
$chain | ConvertTo-Json -Depth 3
"""

enc = base64.b64encode(PS.encode("utf-16-le")).decode("ascii")
r = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="ignore")
try:
    chain = json.loads(r.stdout or "[]")
except Exception:
    print("解析失败:", r.stdout[:400])
    sys.exit(1)
if isinstance(chain, dict):
    chain = [chain]

print("=== robot 476824 的父进程链 ===")
for i, p in enumerate(chain):
    cmd = (p.get("cmd") or "")[:110]
    print("  [%d] pid=%-7s %-16s %s" % (
        i, p.get("pid"), p.get("name"), cmd))
    print("      created=%s ppid=%s" % (p.get("created"), p.get("ppid")))

print()
print("=== 判定 ===")
if len(chain) >= 2:
    parent = chain[1]
    pname = (parent.get("name") or "").lower()
    pcmd = parent.get("cmd") or ""
    if "botsup" in pcmd:
        print("父进程是 botsup -> botsup 重复拉起了")
    elif "explorer" in pname or "cmd" in pname or "powershell" in pname:
        print("父进程是 shell/explorer -> 有人手工或计划任务拉起")
        print("  cmd:", pcmd[:200])
    else:
        print("父进程:", pname, pcmd[:150])
