# -*- coding: utf-8 -*-
"""用已有数据找优化点：1290 条回合记录能看出什么。"""
import json
import os
from collections import Counter

LOG = r"D:\Projects\RocoAgent\logs"
OUT = os.path.join(os.environ["TEMP"], "oc_analysis.txt")


def read(name):
    p = os.path.join(LOG, name)
    out = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def main():
    lines = []
    p = lambda *a: lines.append(" ".join(str(x) for x in a))

    turns = read("turns.jsonl")
    p("turns:", len(turns))

    kinds = Counter()
    slots = Counter()
    unreadable = 0
    no_card = 0
    for t in turns:
        a = t.get("action") or []
        kinds[a[0] if a else "?"] += 1
        if a and a[0] == "skill":
            slots[a[1] if len(a) > 1 else "?"] += 1
        skills = t.get("skills") or []
        vals = [s.get("v") for s in skills if s]
        if all(v is None for v in vals):
            unreadable += 1
        elif sum(1 for v in vals if v) <= 1:
            no_card += 1

    p("\n== 动作分布 ==")
    for k, v in kinds.most_common():
        p("  %-10s %5d  (%.1f%%)" % (k, v, 100.0 * v / max(1, len(turns))))
    p("\n== 技能槽位分布 ==")
    for k, v in slots.most_common():
        p("  slot %-4s %5d  (%.1f%%)" % (k, v, 100.0 * v / max(1, sum(slots.values()))))
    p("\n== 技能栏可读性 ==")
    p("  全空（完全读不到）: %d (%.1f%%)" % (unreadable, 100.0 * unreadable / max(1, len(turns))))
    p("  只有 1 张卡有数字  : %d (%.1f%%)" % (no_card, 100.0 * no_card / max(1, len(turns))))

    # 连续同动作（可能是重复按）
    rep = 0
    prev = None
    for t in turns:
        key = (t.get("ts"), tuple(t.get("action") or []))
        if prev and key[1] == prev[1] and t.get("foe_pct") == prev[2]:
            rep += 1
        prev = (key, t.get("foe_pct"))
    p("\n== 疑似同一回合重复动作: %d" % rep)

    # 血量/对手血量分布
    my0 = [t["my_pct"] for t in turns if t.get("my_pct") is not None]
    foe0 = [t["foe_pct"] for t in turns if t.get("foe_pct") is not None]
    if my0:
        p("\n== 血量样本 ==")
        p("  我方 n=%d 均值 %.1f 中位 %.1f" % (
            len(my0), sum(my0) / len(my0), sorted(my0)[len(my0) // 2]))
        p("  对面 n=%d 均值 %.1f 中位 %.1f" % (
            len(foe0), sum(foe0) / len(foe0), sorted(foe0)[len(foe0) // 2]))

    # 卡面伤害分布：max 伤害是否常常远超对手剩余血量（可以一击必杀却没打？）
    kill_missed = 0
    for t in turns:
        skills = t.get("skills") or []
        vals = [s.get("v") for s in skills if s and s.get("v")]
        foe = t.get("foe_pct")
        if not vals or foe is None:
            continue
        best = max(vals)
        # 卡面伤害是绝对数值，血量是百分比；只知道相对关系，无法直接比较
        # 这里只统计"最高伤害卡是否是 2.0 克制"的占比
    p("\n== 卡面颜色分布（绿=克制/红=被克）==")
    colors = Counter()
    for t in turns:
        for s in [x for x in (t.get("skills") or []) if x]:
            if s.get("v"):
                colors[s.get("c")] += 1
    for k, v in colors.most_common():
        p("  %-6s %5d" % (k, v))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
    print("ok")
