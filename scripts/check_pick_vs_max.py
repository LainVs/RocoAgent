# -*- coding: utf-8 -*-
"""对比 v1/v2 的选招依据，并检查"实际选了非最高伤害"的记录。

用户的问题（2026-11-21）
--------------------
"我们机器人升级了，不像之前看哪个伤害高用哪个了"

要回答清楚两件事：
  1. v1 和 v2 的选招依据到底差在哪
  2. 实盘里"选了非最高伤害"的记录，差多少、为什么
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "src"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TURNS = os.path.join(ROOT, "logs", "turns.jsonl")


def load():
    out = []
    for line in open(TURNS, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    rows = load()
    print("turns.jsonl %d 条" % len(rows))

    # 只看有 action 且是出招的
    picks = []
    for r in rows:
        act = r.get("action") or []
        if len(act) != 2 or act[0] != "skill":
            continue
        sk = r.get("skills") or []
        chosen = act[1]
        lit = [(s.get("i"), s.get("v")) for s in sk
               if s and s.get("lit") and s.get("v") is not None]
        if len(lit) < 2:
            continue
        best = max(lit, key=lambda x: x[1])
        picked = next((v for i, v in lit if i == chosen), None)
        if picked is None:
            continue
        picks.append({
            "ts": r.get("ts"), "turn": r.get("turn"),
            "pet": r.get("our_pet"), "foe": r.get("foe_pet"),
            "why": r.get("why"), "chosen": chosen, "picked": picked,
            "best_i": best[0], "best_v": best[1],
            "gap": best[1] - picked,
            "lit": lit,
        })

    print("可比较的决策 %d 条" % len(picks))

    same = sum(1 for p in picks if p["gap"] == 0)
    print()
    print("=== 选中的是不是最高伤害 ===")
    print("  选最高      %d 条 (%.1f%%)" % (
        same, 100.0 * same / max(1, len(picks))))
    print("  选非最高    %d 条 (%.1f%%)" % (
        len(picks) - same, 100.0 * (len(picks) - same) / max(1, len(picks))))

    # 差距分布
    print()
    print("=== 差距分布（最高 - 实选）===")
    buckets = Counter()
    for p in picks:
        g = p["gap"]
        if g == 0:
            buckets["0"] += 1
        elif g <= 5:
            buckets["1-5"] += 1
        elif g <= 20:
            buckets["6-20"] += 1
        elif g <= 50:
            buckets["21-50"] += 1
        elif g <= 100:
            buckets["51-100"] += 1
        else:
            buckets["100+"] += 1
    for k in ("0", "1-5", "6-20", "21-50", "51-100", "100+"):
        print("  %-8s %d" % (k, buckets.get(k, 0)))

    # 差距最大的几条
    print()
    print("=== 差距最大的 10 条 ===")
    for p in sorted(picks, key=lambda x: -x["gap"])[:10]:
        if p["gap"] <= 0:
            break
        print("  %s T%-3s %-10s vs %-10s 选s%s=%s 最高s%s=%s (差 %d)" % (
            p["ts"], p["turn"], p["pet"], p["foe"],
            p["chosen"], p["picked"], p["best_i"], p["best_v"], p["gap"]))
        print("        why=%s" % (p["why"] or "")[:80])
        print("        lit=%s" % (p["lit"],))

    # 按精灵统计
    print()
    print("=== 按精灵看（只列有差距的）===")
    by_pet = Counter()
    for p in picks:
        if p["gap"] > 0:
            by_pet[p["pet"]] += 1
    for pet, n in by_pet.most_common(10):
        print("  %-14s %d 次" % (pet, n))

    return 0


if __name__ == "__main__":
    sys.exit(main())
