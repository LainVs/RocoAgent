# -*- coding: utf-8 -*-
"""analyze_runs.py —— 从运行日志里找可优化点。

分析维度
--------
1. 出招间隔：两次动作之间隔多久，卡在哪
2. GATE 原因分布：机器人为什么不出手
3. 识别成功率：对面身份 / 血量 / 特性
4. 决策质量：是否选了最高伤害卡
5. 崩溃/重启：进程稳定性

用法
----
    python scripts\\analyze_runs.py
    python scripts\\analyze_runs.py --since 20:00
"""
from __future__ import annotations

import collections
import io
import json
import os
import re
import sys

ROOT = r"D:\Projects\RocoAgent"
LOG = os.path.join(ROOT, "logs", "bot_stdout.log")
TURNS = os.path.join(ROOT, "logs", "turns.jsonl")


def parse_ts(line):
    m = re.match(r"^(\d{2}):(\d{2}):(\d{2}) ", line)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def main():
    since = None
    if "--since" in sys.argv:
        s = sys.argv[sys.argv.index("--since") + 1]
        h, m = s.split(":")
        since = int(h) * 3600 + int(m) * 60

    lines = io.open(LOG, encoding="utf-8", errors="replace").read().split("\n")
    rows = []
    for l in lines:
        t = parse_ts(l)
        if t is None:
            continue
        if since is not None and t < since:
            continue
        rows.append((t, l))

    print("=== 日志分析（%d 行）===" % len(rows))
    if not rows:
        return

    # 1. 动作
    acts = [(t, l) for t, l in rows
            if re.match(r"^\d\d:\d\d:\d\d T\d+\s", l)]
    print("\n[1] 动作 %d 次" % len(acts))
    if len(acts) >= 2:
        gaps = [acts[i][0] - acts[i - 1][0] for i in range(1, len(acts))]
        gaps = [g for g in gaps if 0 < g < 600]
        if gaps:
            gaps.sort()
            print("    间隔: 中位 %ds  P90 %ds  最大 %ds"
                  % (gaps[len(gaps) // 2],
                     gaps[int(len(gaps) * 0.9)], gaps[-1]))
            slow = [g for g in gaps if g > 25]
            print("    >25s 的慢手: %d 次 (%.0f%%)"
                  % (len(slow), 100.0 * len(slow) / len(gaps)))

    # 2. GATE
    gate = collections.Counter()
    for _t, l in rows:
        m = re.search(r"GATE (.+)", l)
        if m:
            gate[m.group(1).strip()] += 1
    print("\n[2] GATE 原因（前 8）")
    for k, v in gate.most_common(8):
        print("    %-42s %d" % (k, v))

    # 3. 识别
    print("\n[3] 识别")
    foe_new = sum(1 for _t, l in rows if "FOE -> new bust" in l)
    foe_id = sum(1 for _t, l in rows if re.search(r"FOE -> \S+ \(0\.\d+\)", l))
    foe_lip = sum(1 for _t, l in rows if "立绘通道" in l)
    foe_unk = sum(1 for _t, l in rows if "unknown bust" in l)
    print("    对面换人 %d 次，认出 %d 次，立绘通道 %d 次，存未知样本 %d 次"
          % (foe_new, foe_id, foe_lip, foe_unk))
    conf = sum(1 for _t, l in rows if "id conflict" in l)
    print("    通道冲突 %d 次" % conf)

    # 4. 面板扫描耗时
    print("\n[4] 面板扫描")
    scans = []
    for _t, l in rows:
        m = re.search(r"FOE PANEL timing: ability ([\d.]+)s, total ([\d.]+)s", l)
        if m:
            scans.append((float(m.group(1)), float(m.group(2))))
    if scans:
        a = sorted(x[0] for x in scans)
        b = sorted(x[1] for x in scans)
        print("    %d 次  特性 OCR 中位 %.1fs  总耗中位 %.1fs  最慢 %.1fs"
              % (len(scans), a[len(a) // 2], b[len(b) // 2], b[-1]))

    # 5. 稳定性
    print("\n[5] 稳定性")
    boots = sum(1 for _t, l in rows if "boot pid=" in l)
    exits = sum(1 for _t, l in rows if "exited rc=" in l)
    stuck = sum(1 for _t, l in rows if "STUCK" in l)
    focus = sum(1 for _t, l in rows if "FOCUS LOST" in l)
    print("    启动 %d 次，异常退出 %d 次，STUCK %d 次，焦点丢失 %d 次"
          % (boots, exits, stuck, focus))

    # 6. 决策质量（从 turns.jsonl）
    print("\n[6] 决策质量")
    if os.path.exists(TURNS):
        L = []
        for line in io.open(TURNS, encoding="utf-8"):
            try:
                L.append(json.loads(line))
            except Exception:
                pass
        recent = L[-300:]
        bad = 0
        total = 0
        for r in recent:
            sk = [s for s in (r.get("skills") or []) if s]
            act = r.get("action") or []
            if not act or act[0] != "skill":
                continue
            lit = [s for s in sk if s.get("lit") and s.get("v") is not None]
            if not lit:
                continue
            chosen = next((s for s in sk if s.get("i") == act[1]), None)
            if not chosen or chosen.get("v") is None:
                continue
            total += 1
            top = max(lit, key=lambda s: s["v"])
            if chosen["v"] < top["v"]:
                bad += 1
        if total:
            print("    近 300 条：选了非最高伤害卡 %d / %d (%.1f%%)"
                  % (bad, total, 100.0 * bad / total))
        # 属性置信度
        conf_c = collections.Counter(
            r.get("foe_types_conf") or "?" for r in recent)
        print("    对面属性置信度: %s" % dict(conf_c))


if __name__ == "__main__":
    main()
