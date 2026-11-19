# -*- coding: utf-8 -*-
"""分析人机对比：人打的每一手 vs 机器人想打的那一手。

只做**能从数据里算出来**的结论，不猜：
    · 每回合对面平均掉多少血（人的进攻效率 vs 机器人的）
    · 每回合自己平均挨多少（人的防守 vs 机器人的）
    · 有多少回合打出了击杀（对面掉血 ≥ 其剩余血）
    · 有多少回合完全没掉血（对面在铺场 / 双方在换人）

用法：
    python analyze_operator.py            # 最近全部
    python analyze_operator.py --last 50  # 只看最近 50 手
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "logs", "operator_log.jsonl")


def load(path=PATH):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def summary(rows):
    n = len(rows)
    if not n:
        return None
    dealt = [r["delta"]["foe"] for r in rows
             if r.get("delta") and r["delta"].get("foe") is not None]
    taken = [r["delta"]["my"] for r in rows
             if r.get("delta") and r["delta"].get("my") is not None]
    kills = sum(1 for r in rows
                if r.get("delta") and r["delta"].get("foe") is not None
                and r["delta"]["foe"] <= -max(5.0, abs(r.get("foe") or 100)))
    quiet = sum(1 for r in rows
                if r.get("delta") and abs(r["delta"].get("foe") or 0) < 1.0)
    return {
        "turns": n,
        "avg_dealt": round(sum(dealt) / len(dealt), 1) if dealt else 0.0,
        "avg_taken": round(sum(taken) / len(taken), 1) if taken else 0.0,
        "kill_turns": kills,
        "no_damage_turns": quiet,
        "by": {k: sum(1 for r in rows if r.get("acted_by") == k)
               for k in ("operator", "bot")},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--last", type=int, default=0)
    args = ap.parse_args()

    rows = load()
    if args.last:
        rows = rows[-args.last:]
    if not rows:
        print("没有记录。先让机器人跑一局（辅助模式），或手动打几手。")
        return 1

    s = summary(rows)
    print("== 总览（%d 手）==" % s["turns"])
    print("  出招方： 人 %d / 机器人 %d" % (s["by"].get("operator", 0),
                                            s["by"].get("bot", 0)))
    print("  每手对面平均掉血： %.1f%%" % abs(s["avg_dealt"]))
    print("  每手自己平均掉血： %.1f%%" % abs(s["avg_taken"]))
    print("  击杀回合：         %d" % s["kill_turns"])
    print("  对面完全没掉血：   %d（它在铺场/换人）" % s["no_damage_turns"])

    # 人 vs 机 分组的效率对比（这才是重点）
    for who in ("operator", "bot"):
        part = [r for r in rows if r.get("acted_by") == who]
        ps = summary(part)
        if not ps or not ps["turns"]:
            continue
        print("\n== %s ==" % ("人" if who == "operator" else "机器人"))
        print("  %d 手  对面均掉 %.1f%%  自己均掉 %.1f%%  击杀 %d"
              % (ps["turns"], abs(ps["avg_dealt"]), abs(ps["avg_taken"]),
                 ps["kill_turns"]))

    # 分歧抽样：记录里带 shadow 的
    diff = [r for r in rows if r.get("shadow")]
    if diff:
        print("\n== 带影子决策的回合（抽样 8 条）==")
        for r in diff[-8:]:
            print("  %s 卡面%s 影子=%s 结果 我%+.0f 敌%+.0f"
                  % (r.get("ts"), r.get("cards"), r.get("shadow"),
                     (r.get("delta") or {}).get("my") or 0,
                     (r.get("delta") or {}).get("foe") or 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
