# -*- coding: utf-8 -*-
"""profile_turn.py —— 剖析"一个回合为什么慢"。

从日志里还原每次动作的完整时间线，按阶段归类耗时：
    识别（PET/FOE/立绘）
    面板扫描（FOE SCAN/PANEL）
    首领化（LEADER）
    换人（SWITCH/FAINT）
    等待（GATE）
    其他

用法
----
    python scripts\\profile_turn.py
    python scripts\\profile_turn.py --top 8
"""
from __future__ import annotations

import collections
import io
import re
import sys

ROOT = r"D:\Projects\RocoAgent"
LOG = r"D:\Projects\RocoAgent\logs\bot_stdout.log"

# 行首关键词 -> 阶段
STAGE_RULES = [
    ("面板扫描", ("FOE SCAN", "FOE PANEL")),
    ("首领化", ("LEADER ->",)),
    ("换人", ("SWITCH", "FAINT", "FORCED SWITCH", "PANEL MAP")),
    ("识别", ("PET ->", "FOE ->", "SHADOW", "CAST ->")),
    ("等待", ("GATE ",)),
    ("推进", ("advance splash", "lobby ->", "action button", "result screen")),
    ("状态", ("state ",)),
]


def stage_of(text):
    for name, keys in STAGE_RULES:
        if any(k in text for k in keys):
            return name
    return "其他"


def main():
    top = 8
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])

    lines = io.open(LOG, encoding="utf-8", errors="replace").read().split("\n")
    rows = []
    for l in lines:
        m = re.match(r"^(\d{2}):(\d{2}):(\d{2}) (.*)$", l)
        if m:
            t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            rows.append((t, m.group(4)))
    acts = [(i, t, s) for i, (t, s) in enumerate(rows)
            if re.match(r"T\d+\s", s)]
    print("动作 %d 次，日志 %d 行" % (len(acts), len(rows)))

    gaps = []
    for k in range(1, len(acts)):
        _i0, t0, s0 = acts[k - 1]
        i1, t1, s1 = acts[k]
        gap = t1 - t0
        if not (0 < gap < 400):
            continue
        # 这一段的行
        seg = rows[acts[k - 1][0] + 1:i1]
        by = collections.Counter()
        for _t, s in seg:
            by[stage_of(s)] += 1
        gaps.append((gap, s0, s1, dict(by), len(seg)))

    gaps.sort(key=lambda x: -x[0])
    print("\n=== 最慢的 %d 次 ===" % top)
    for gap, s0, s1, by, n in gaps[:top]:
        print("\n[%ds, %d 行]" % (gap, n))
        print("  从: %s" % s0[:66])
        print("  到: %s" % s1[:66])
        print("  阶段分布: %s" % by)

    # 阶段总计
    total = collections.Counter()
    for gap, _a, _b, by, _n in gaps:
        for k, v in by.items():
            total[k] += v
    print("\n=== 慢手里各阶段出现次数（总 %d 段）===" % len(gaps))
    for k, v in total.most_common():
        print("  %-8s %d" % (k, v))

    # 中位数
    if gaps:
        g = sorted(x[0] for x in gaps)
        print("\n间隔中位 %ds，P90 %ds" % (g[len(g) // 2], g[int(len(g) * 0.9)]))


if __name__ == "__main__":
    main()
