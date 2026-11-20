# -*- coding: utf-8 -*-
"""profile_stage.py —— 精确统计每个阶段的耗时（用日志时间戳）。

比 profile_turn.py 更细：把相邻两行的时间差按"后一行属于什么阶段"
累加，得出每个阶段真正吃掉多少秒。

用法
----
    python scripts\\profile_stage.py
    python scripts\\profile_stage.py --since 05:28
"""
from __future__ import annotations

import collections
import io
import re
import sys

ROOT = r"D:\Projects\RocoAgent"
LOG = r"D:\Projects\RocoAgent\logs\bot_stdout.log"

STAGES = [
    ("面板扫描", ("FOE SCAN", "FOE PANEL")),
    ("换人", ("SWITCH", "FAINT", "FORCED SWITCH", "PANEL MAP", "try_switch")),
    ("识别", ("PET ->", "FOE ->", "SHADOW", "CAST ->")),
    ("推进", ("advance", "lobby", "action button", "result screen")),
    ("等待", ("GATE",)),
    ("状态", ("state ",)),
]


def stage_of(text):
    for name, keys in STAGES:
        if any(k in text for k in keys):
            return name
    return "其他"


def main():
    since = None
    if "--since" in sys.argv:
        s = sys.argv[sys.argv.index("--since") + 1]
        h, m = s.split(":")
        since = int(h) * 3600 + int(m) * 60

    lines = io.open(LOG, encoding="utf-8", errors="replace").read().split("\n")
    rows = []
    for l in lines:
        m = re.match(r"^(\d{2}):(\d{2}):(\d{2}) (.*)$", l)
        if m:
            t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if since is not None and t < since:
                continue
            rows.append((t, m.group(4)))
    if len(rows) < 2:
        print("日志不足")
        return 0

    total = collections.Counter()
    counts = collections.Counter()
    for i in range(1, len(rows)):
        t0, _s0 = rows[i - 1]
        t1, s1 = rows[i]
        dt = t1 - t0
        if not (0 <= dt <= 120):      # 跳过局间等待/异常间隔
            continue
        st = stage_of(s1)
        total[st] += dt
        counts[st] += 1

    span = rows[-1][0] - rows[0][0]
    print("时间跨度 %d 秒，%d 行日志" % (span, len(rows)))
    print("\n%-10s %10s %8s %8s" % ("阶段", "总耗时", "占比", "次数"))
    print("-" * 42)
    for st, sec in total.most_common():
        print("%-10s %9ds %7.1f%% %8d"
              % (st, sec, 100.0 * sec / max(1, span), counts[st]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
