# -*- coding: utf-8 -*-
"""nightly_report.py —— 每日策略仪表汇总（一条命令出一份 markdown）。

把三件每天早上要手动跑的事合并成一个报告：

    & $py scripts/nightly_report.py            # -> docs/NIGHTLY_<HHMM>.md

内容四节：
  1. 胜率（results.jsonl，唯一衡量指标）
  2. 承伤估计偏差（复用 scripts/audit_incoming.py 的聚合结论）
  3. 对手行为模型覆盖面（达标对位数 / anticipate 覆盖率）
  4. 动作率健康度（turns.jsonl 去重后每局动作数、盲打次数）

只读日志，只写一个 markdown 文件，不动任何决策数据。
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, r"D:\Tools")

import paths  # noqa: E402
paths.use_tools()

LOGS = os.path.join(ROOT, "logs")


# ---------------------------------------------------------------- 1. 胜率
def section_winrate(lines):
    lines.append("## 一、胜率（results.jsonl）")
    res = os.path.join(LOGS, "results.jsonl")
    rows = []
    try:
        with io.open(res, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
    except OSError:
        pass
    if not rows:
        lines.append("- 暂无战绩记录（机器人还没进过大厅读段位）")
        return
    c = collections.Counter(r.get("result") for r in rows)
    decided = c.get("win", 0) + c.get("loss", 0)
    rate = 100.0 * c.get("win", 0) / decided if decided else 0.0
    lines.append("- 总计 %d 场：胜 %d / 负 %d / 平 %d → **%.1f%%**"
                 % (len(rows), c.get("win", 0), c.get("loss", 0),
                    c.get("flat", 0), rate))
    # 最近 20 场
    recent = rows[-20:]
    rc = collections.Counter(r.get("result") for r in recent)
    rd = rc.get("win", 0) + rc.get("loss", 0)
    if rd:
        lines.append("- 最近 20 场：**%.1f%%**（胜 %d / 负 %d）"
                     % (100.0 * rc.get("win", 0) / rd,
                        rc.get("win", 0), rc.get("loss", 0)))
    lines.append("")


# ---------------------------------------------------------------- 2. 承伤
def section_incoming(lines):
    lines.append("## 二、承伤估计偏差（weighted vs observed）")
    try:
        import audit_incoming as A
        agg = A.observed_by_pair()
        rows = []
        for (me, foe), ds in agg.items():
            if len(ds) < 3:
                continue
            obs = sum(ds) / len(ds)
            worst = A.worst_in(foe, me)
            wt = A.weighted_in(foe, me)
            rows.append((me, foe, len(ds), obs, worst, wt))
        rows.sort(key=lambda r: -(r[3] or 0))
        if not rows:
            lines.append("- 样本不足（n>=3 的对位 0 组）")
        else:
            lines.append("")
            lines.append("| me | foe | n | obs_avg | worst | weighted | wt/obs |")
            lines.append("|---|---|---|---|---|---|---|")
            for me, foe, n, obs, worst, wt in rows[:12]:
                ratio = "%.2f" % (wt / obs) if wt and obs else "-"
                lines.append("| %s | %s | %d | %.0f | %s | %s | %s |"
                             % (me, foe, n, obs, worst, wt, ratio))
            ratios = [wt / obs for _m, _f, _n, obs, _w, wt in rows
                      if wt and obs]
            if ratios:
                med = sorted(ratios)[len(ratios) // 2]
                lines.append("")
                lines.append("- **median wt/obs = %.2f**（%d 组）→ %s"
                             % (med, len(ratios),
                                "建议 blend 降到 0.6" if med < 0.8
                                else ("偏高，检查 foe_moves" if med > 1.3
                                      else "保持 blend=0.75")))
    except Exception as e:
        lines.append("- 审计失败：%s: %s" % (type(e).__name__, e))
    lines.append("")


# ---------------------------------------------------------------- 3. 对手模型
def section_opponent(lines):
    lines.append("## 三、对手行为模型覆盖面")
    try:
        import opponent_model as OM
        rows = OM.load_actions()
        model, n_matches = OM.build_from_actions(rows)
        pairs = {k: v for k, v in model.items()
                 if "|" in k and not k.startswith("__")}
        reliable = {k: v for k, v in pairs.items() if v.get("reliable")}
        lines.append("- 对位组 %d（达标 n>=12：%d 组）"
                     % (len(pairs), len(reliable)))
        if reliable:
            for k, v in sorted(reliable.items(),
                               key=lambda kv: -kv[1]["n"])[:5]:
                lines.append("  - %s：n=%d 攻%.0f%% 换%.0f%% 增%.0f%%"
                             % (k, v["n"], v["p_attack"] * 100,
                                v["p_switch"] * 100, v["p_buff"] * 100))
        import anticipate
        adv_ok = tested = 0
        for k in list(pairs)[:300]:
            me, foe = k.split("|", 1)
            if not foe:
                continue
            adv = anticipate.advice(me=me, foe=foe, my_pct=80, foe_pct=60,
                                    my_hp=400, foe_hp=400, bench=[])
            tested += 1
            if adv:
                adv_ok += 1
        lines.append("- anticipate 建议覆盖率：**%d/%d**（%.0f%%）"
                     % (adv_ok, tested,
                        100.0 * adv_ok / tested if tested else 0))
    except Exception as e:
        lines.append("- 模型读取失败：%s: %s" % (type(e).__name__, e))
    lines.append("")


# ---------------------------------------------------------------- 4. 动作率
def section_actions(lines):
    lines.append("## 四、动作率健康度（turns.jsonl 去重）")
    p = os.path.join(LOGS, "turns.jsonl")
    rows = []
    try:
        with io.open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    except OSError:
        pass
    if not rows:
        lines.append("- 无回合记录")
        lines.append("")
        return
    # 按 (match, turn) 去重（轮询日志同回合重复多行）
    seen = set()
    actions_by_match = collections.Counter()
    matches = set()
    for r in rows:
        act_list = r.get("action")
        act0 = act_list[0] if isinstance(act_list, list) and act_list else None
        key = (r.get("mid"), r.get("turn"),
               tuple(act_list) if isinstance(act_list, list) else act_list)
        if key in seen:
            continue
        seen.add(key)
        mid = r.get("mid")
        if mid:
            matches.add(mid)
        if act0 in ("skill", "energy", "switch"):
            actions_by_match[mid] += 1
    if actions_by_match:
        vals = sorted(actions_by_match.values())
        med = vals[len(vals) // 2]
        low = sum(1 for v in vals if v <= 2)
        lines.append("- 对局 %d 场（有动作记录 %d 场）：每局动作中位数 **%d**"
                     % (len(matches), len(actions_by_match), med))
        lines.append("- 动作数 <=2 的对局 %d 场（卡局/弃权嫌疑）" % low)
    else:
        lines.append("- 有回合记录但无动作")
    lines.append("")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    lines = ["# 每日策略仪表（%s）" % time.strftime("%Y-%m-%d %H:%M"), ""]
    section_winrate(lines)
    section_incoming(lines)
    section_opponent(lines)
    section_actions(lines)

    text = "\n".join(lines)
    out = args.out or os.path.join(
        ROOT, "docs", "NIGHTLY_%s.md" % time.strftime("%Y%m%d_%H%M"))
    with io.open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print("wrote", out)


if __name__ == "__main__":
    main()
