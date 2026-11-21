# -*- coding: utf-8 -*-
"""mem_audit.py —— 内存归因：按根进程列出整棵子树占用。

为什么需要它
------------
2026-11-21 用户问"32G 内存用了这么多？是不是乱开东西导致的"。
当时只能手工一条条查 ParentProcessId，很慢。

这个脚本把"按根进程归因"固化成一条命令：
    · 按子树总内存排序，一眼看出谁是大头
    · 检测同名进程的"独立实例数"（识别重复启动）
    · 支持只查某个根的子进程明细

用法：
    python scripts\\mem_audit.py              # 根进程前 15
    python scripts\\mem_audit.py --all        # 全部根进程
    python scripts\\mem_audit.py --tree 15132 # 某个根的子树明细
    python scripts\\mem_audit.py --dupes      # 只看重复实例
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from collections import defaultdict

PS = r"""
$out = @()
Get-CimInstance Win32_Process | ForEach-Object {
    $p = $null
    try { $p = Get-Process -Id $_.ProcessId -ErrorAction Stop } catch {}
    $out += [PSCustomObject]@{
        pid   = $_.ProcessId
        ppid  = $_.ParentProcessId
        name  = $_.Name
        ws    = $(if ($p) { $p.WorkingSet64 } else { 0 })
        start = $(if ($p) { $p.StartTime.ToString('HH:mm:ss') } else { '-' })
        cmd   = $_.CommandLine
    }
}
$out | ConvertTo-Json -Depth 3
"""


def load():
    enc = base64.b64encode(PS.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", timeout=180)
    data = json.loads(r.stdout or "[]")
    by = {}
    for p in data:
        try:
            by[int(p["pid"])] = {
                "pid": int(p["pid"]), "ppid": int(p["ppid"] or 0),
                "name": p["name"], "ws": int(p["ws"] or 0),
                "start": p["start"], "cmd": p["cmd"] or "", "ch": [],
            }
        except Exception:
            pass
    for p in by.values():
        par = by.get(p["ppid"])
        if par and par is not p:
            par["ch"].append(p)
    return by


def sub_ws(p, seen=None):
    if seen is None:
        seen = set()
    if p["pid"] in seen:
        return 0
    seen.add(p["pid"])
    return p["ws"] + sum(sub_ws(c, seen) for c in p["ch"])


def mb(x):
    return x / 1048576.0


def show_tree(p, depth=0, min_mb=20):
    print("%s%-7s %-26s %8.1f MB  %s" % (
        "  " * depth, p["pid"], p["name"], mb(sub_ws(p)),
        " ".join(p["cmd"].split())[:66]))
    for c in sorted(p["ch"], key=lambda x: -sub_ws(x)):
        if mb(sub_ws(c)) < min_mb:
            continue
        show_tree(c, depth + 1, min_mb)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="列出全部根进程")
    ap.add_argument("--tree", type=int, help="打印某根的子树")
    ap.add_argument("--dupes", action="store_true", help="只看重复实例")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    by = load()
    roots = [p for p in by.values() if p["ppid"] not in by]
    roots.sort(key=lambda x: -sub_ws(x))
    total = sum(p["ws"] for p in by.values())

    if args.tree:
        p = by.get(args.tree)
        if not p:
            print("PID %d 不存在" % args.tree)
            return 1
        show_tree(p)
        print()
        print("子树合计: %.1f MB" % mb(sub_ws(p)))
        return 0

    if args.dupes:
        print("=== 同名进程的独立实例数（>1 说明重复启动）===")
        groups = defaultdict(list)
        for p in by.values():
            groups[p["name"]].append(p)
        rows = []
        for name, lst in groups.items():
            if len(lst) < 2:
                continue
            roots_of = set()
            for x in lst:
                cur = x
                guard = 0
                while cur["ppid"] in by and guard < 25:
                    cur = by[cur["ppid"]]
                    guard += 1
                roots_of.add(cur["pid"])
            rows.append((name, len(lst), len(roots_of),
                         sum(x["ws"] for x in lst)))
        rows.sort(key=lambda r: -r[3])
        print("%-28s %5s %6s %10s" % ("进程名", "总数", "独立实例", "合计MB"))
        for name, n, ni, ws in rows:
            flag = "  <== 重复" if ni > 1 else ""
            print("%-28s %5d %6d %10.1f%s" % (name, n, ni, mb(ws), flag))
        return 0

    limit = len(roots) if args.all else args.top
    print("=== 根进程子树内存（前 %d）===" % limit)
    print("%-7s %-9s %9s %-26s %s" % ("PID", "启动", "子树MB", "根进程", "命令行"))
    acc = 0
    for r in roots[:limit]:
        w = mb(sub_ws(r))
        acc += w
        print("%-7s %-9s %9.1f %-26s %s" % (
            r["pid"], r["start"], w, r["name"],
            " ".join(r["cmd"].split())[:56]))
    print()
    print("前 %d 根合计: %.2f GB" % (limit, acc / 1024.0))
    print("全部进程合计: %.2f GB (%d 个进程, %d 个根)" % (
        mb(total) / 1024.0, len(by), len(roots)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
