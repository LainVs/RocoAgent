"""review_turns.py - per-match review built from the bot's own decision log.

Pixel reviews (review.py) are limited by frame density: recording a full window
takes ~2.5 s per frame, so a 60 s match yields ~20 frames - far too coarse to
score individual decisions. The bot already logs every decision with the state
it saw, which is a much better substrate.

usage:
    python review_turns.py --from 033647 --to 033747 [--out file.md]
    python review_turns.py --last 3
"""
from __future__ import annotations
import paths

import argparse
import json
import os
import sys
import time
from collections import Counter

LOG_DIR = str(paths.ROOT / "logs")
TURNS = os.path.join(LOG_DIR, "turns.jsonl")
ROUNDS = os.path.join(LOG_DIR, "rounds.jsonl")


def hhmmss_to_s(t):
    try:
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:
        return None


def load_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def split_matches(turns):
    """Group turns into matches by time gaps.

    A single turn can take 20-30 s (attack animation, forced switch, faint
    handling), so the split threshold has to be well above that - 90 s - or one
    match gets shredded into fragments and the metrics become meaningless.
    """
    if not turns:
        return []
    groups, cur = [], [turns[0]]
    for prev, cur_t in zip(turns, turns[1:]):
        a, b = hhmmss_to_s(prev["ts"]), hhmmss_to_s(cur_t["ts"])
        if a is None or b is None or b - a > 90:
            groups.append(cur)
            cur = [cur_t]
        else:
            cur.append(cur_t)
    groups.append(cur)
    return groups


def describe(skills):
    parts = []
    for s in skills or []:
        if not s:
            parts.append("-")
        elif s.get("v") is not None:
            parts.append("%s%s" % (s["v"], "" if s.get("c") != "red" else "(抗)"))
        else:
            parts.append(s.get("t") or "-")
    return "/".join(parts)


def review(match_turns, idx):
    L = []
    a = match_turns[0]
    b = match_turns[-1]
    L.append("# 对局复盘 #%d  %s → %s\n" % (idx, a["ts"], b["ts"]))
    L.append("- 我方出手记录：**%d 条**" % len(match_turns))

    kinds = Counter()
    for t in match_turns:
        act = t.get("action") or []
        if act and act[0] == "skill":
            kinds["技能 %s" % act[1]] += 1
        elif act and act[0] == "energy":
            kinds["聚能 F"] += 1
        elif act and act[0] == "switch":
            kinds["换人"] += 1
    L.append("- 动作分布：" + "，".join("%s×%d" % (k, v) for k, v in kinds.items()))

    # decision quality flags
    cached = [t for t in match_turns if "cached" in (t.get("why") or "")]
    blind = [t for t in match_turns if "blind" in (t.get("why") or "")]
    unread = [t for t in match_turns if "unreadable" in (t.get("why") or "")]
    L.append("")
    L.append("## 决策质量")
    L.append("")
    L.append("| 指标 | 次数 | 说明 |")
    L.append("| --- | --- | --- |")
    L.append("| 读取失败→复用旧计划 | %d | 伤害数字没读出来，按上一回合的计划 |"
             % len(cached))
    L.append("| 读取失败→盲按 2 号位 | %d | 连旧计划都没有 |" % len(blind))
    L.append("| 无能量且读不到 | %d | 只能聚能 |" % len(unread))
    bad = len(cached) + len(blind) + len(unread)
    rate = 0.0 if not match_turns else 100.0 * bad / len(match_turns)
    L.append("")
    L.append("**感知失败率 %.0f%%**（%d/%d）" % (rate, bad, len(match_turns)))
    if rate > 30:
        L.append("")
        L.append("> 超过 30%：说明技能栏识别在这局里不稳定，优先修感知，别调策略。")
    elif rate > 10:
        L.append("")
        L.append("> 10-30%：能打，但仍有浪费的回合。")

    # HP trajectory
    L.append("")
    L.append("## 血量轨迹")
    L.append("")
    L.append("| # | 时间 | 我方 | 敌方 | 动作 | 技能数字 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for i, t in enumerate(match_turns, 1):
        act = t.get("action") or []
        act_s = ("技能%s" % act[1]) if act and act[0] == "skill" else (
            "聚能" if act and act[0] == "energy" else str(act))
        L.append("| %d | %s | %s%% | %s%% | %s | %s |" % (
            i, t["ts"], t.get("my_pct"), t.get("foe_pct"),
            act_s, describe(t.get("skills"))))

    # wasted turns: same my/foe on consecutive decisions with no damage change
    waste = 0
    for prev, cur in zip(match_turns, match_turns[1:]):
        if (prev.get("my_pct") == cur.get("my_pct")
                and prev.get("foe_pct") == cur.get("foe_pct")):
            waste += 1
    L.append("")
    L.append("## 浪费的回合")
    L.append("")
    L.append("连续两次出手后血条完全没变：**%d 次**（多半是按键被吞 / 能量不足 / 重按）"
             % waste)

    # healing
    heals = [t for t in match_turns if "heal" in (t.get("why") or "")]
    if heals:
        L.append("")
        L.append("## 治疗")
        L.append("")
        for t in heals:
            L.append("- %s 血量 %s%% 时选择治疗" % (t["ts"], t.get("my_pct")))

    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="t0")
    ap.add_argument("--to", dest="t1")
    ap.add_argument("--last", type=int, default=0,
                    help="review the last N matches instead of a time window")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    turns = load_jsonl(TURNS)
    if not turns:
        print("no turns.jsonl yet")
        return 1

    groups = split_matches(turns)
    if args.last:
        groups = groups[-args.last:]
    elif args.t0 and args.t1:
        a = hhmmss_to_s("%s:%s:%s" % (args.t0[0:2], args.t0[2:4], args.t0[4:6]))
        b = hhmmss_to_s("%s:%s:%s" % (args.t1[0:2], args.t1[2:4], args.t1[4:6]))
        # keep a group if ANY of its turns falls in the window - using only the
        # group's first turn silently produced empty reports for most matches
        keep = []
        for g in groups:
            if any(a <= (hhmmss_to_s(t["ts"]) or 0) <= b for t in g):
                keep.append(g)
        groups = keep

    if not groups:
        msg = "no turns in range\n"
        print(msg.strip())
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write("# 对局复盘（无数据）\n\n时间窗 %s-%s 内 turns.jsonl 没有记录。\n"
                        "这通常意味着那一局一次决策都没做出（感知门未开），"
                        "而不是脚本失败。\n" % (args.t0, args.t1))
            print("wrote stub", args.out)
        return 1

    out = "\n\n---\n\n".join(review(g, i + 1) for i, g in enumerate(groups))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
        print("wrote", args.out)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
