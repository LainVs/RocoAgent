# -*- coding: utf-8 -*-
"""找出第二个 robot 实例的来源进程 436480。

问题（2026-11-21 08:25）：同时存在两个独立 robot 实例：
    pid=477052 <- ppid=470252（botsup）
    pid=479828 <- ppid=436480（未知）

两个实例抢键盘、写乱日志。需要查 436480 是什么。
"""
import base64
import json
import subprocess
import sys


def ps_json(script):
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", timeout=60)
    try:
        d = json.loads(r.stdout or "[]")
    except Exception:
        return None, r.stdout
    return (d if isinstance(d, list) else [d]), None


PS = r"""
$targets = @(436480, 470252)
$out = @()
foreach ($t in $targets) {
    $cur = $t
    for ($i = 0; $i -lt 5; $i++) {
        if (-not $cur -or $cur -eq 0) { break }
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$cur" -ErrorAction SilentlyContinue
        if (-not $p) { break }
        $out += [PSCustomObject]@{
            start = $t
            pid = $p.ProcessId
            ppid = $p.ParentProcessId
            name = $p.Name
            cmd = $p.CommandLine
        }
        $cur = $p.ParentProcessId
    }
}
$out | ConvertTo-Json -Depth 3
"""

chain, raw = ps_json(PS)
if chain is None:
    print("查询失败:", (raw or "")[:300])
    sys.exit(1)

print("=== 父进程链 ===")
cur_start = None
for p in chain:
    if p.get("start") != cur_start:
        cur_start = p.get("start")
        print()
        print("  起点 pid=%s:" % cur_start)
    print("    pid=%-7s %-16s %s" % (
        p.get("pid"), p.get("name"),
        (p.get("cmd") or "")[:100]))

print()
print("=== 判定 ===")
roots = {}
for p in chain:
    roots.setdefault(p.get("start"), []).append(p)

for start, items in roots.items():
    print()
    print("  起点 %s 的链路长度 %d" % (start, len(items)))
    if len(items) >= 2:
        top = items[-1]
        nm = (top.get("name") or "").lower()
        cmd = top.get("cmd") or ""
        print("    最上层: %s  %s" % (nm, cmd[:120]))
        if "explorer" in nm:
            print("    -> explorer 拉起，说明是手工双击 / 桌面快捷方式 / 计划任务")
        elif "python" in nm and "ui_server" in cmd:
            print("    -> UI 服务器拉起的（B 会话的按钮）")
        elif "powershell" in nm or "cmd" in nm:
            print("    -> shell 拉起（脚本 / 计划任务）")
    else:
        print("    （进程已退出，查不到链路）")
