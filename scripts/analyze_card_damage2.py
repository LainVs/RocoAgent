# -*- coding: utf-8 -*-
"""analyze_card_damage2.py —— 按对手分组验证卡面伤害语义。

用户的关键解释（2026-11-21）
--------------------------
火云车（火系，power=140）：
    140 x 1.25(本系) = 175
    175 x 2(克制)    = 350
    350 x 2(专注力)  = 700

**卡面 = 威力 x STAB x 克制 x buff，不含 atk/defn。**

所以验证方法：按 (我方, 槽位, 对手) 分组，
每组的 读数 / (power x STAB) 应该等于 type_eff x buff，
即整齐的 0.25 / 0.5 / 1 / 2 / 4 这种倍数。

跑法：
    python scripts\\analyze_card_damage2.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

TURNS = os.path.join(ROOT, "logs", "turns.jsonl")
CLEAN = (0.25, 0.5, 1.0, 2.0, 3.0, 4.0)


def base_name(name):
    if not name:
        return name
    for sep in ("（", "("):
        if sep in name:
            return name.split(sep)[0]
    return name


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    import sim.adapter as A

    # 收集 (我方, 槽位, 对手) -> 读数计数
    by_key = defaultdict(Counter)
    for line in open(TURNS, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        me = base_name(r.get("our_pet"))
        foe = base_name(r.get("foe_pet"))
        if not me or not foe:
            continue
        for s in (r.get("skills") or []):
            if not s or s.get("v") is None:
                continue
            by_key[(me, s.get("i"), foe)][s["v"]] += 1

    # 每只精灵的装备技能
    cache = {}

    def equip(me):
        if me in cache:
            return cache[me]
        try:
            p = A.build_pokemon(me)
            cache[me] = (p.pokemon_type.value,
                         [(s.name, s.power,
                           s.skill_type.value if hasattr(s.skill_type, "value")
                           else str(s.skill_type)) for s in p.skills])
        except Exception:
            cache[me] = None
        return cache[me]

    print("=== 按 (我方, 槽位, 对手) 分组验证 ===")
    print("假设：读数 / (power x STAB) = type_eff x buff，应为整齐倍数")
    print()

    ok = bad = 0
    shown = 0
    for (me, slot, foe), cnt in sorted(by_key.items(),
                                       key=lambda kv: -sum(kv[1].values())):
        e = equip(me)
        if not e or slot is None or slot > len(e[1]):
            continue
        my_type, moves = e
        sname, power, stype = moves[slot - 1]
        if power <= 0:
            continue
        stab = 1.25 if stype == my_type else 1.0
        base = power * stab

        ratios = Counter()
        for v, n in cnt.items():
            ratios[round(v / base, 2)] += n

        clean_n = sum(n for r, n in ratios.items()
                      if any(abs(r - t) < 0.1 for t in CLEAN))
        total_n = sum(ratios.values())
        ok += clean_n
        bad += (total_n - clean_n)

        if shown < 12 and total_n >= 3:
            shown += 1
            print("[%s] slot%d %s(p=%d,系=%s,STAB=%.2f) vs %s"
                  % (me, slot, sname, power, stype, stab, foe))
            print("    基数 %.1f，读数分布: %s" % (
                base, ", ".join("%sx(%d)" % (r, n)
                                for r, n in ratios.most_common(5))))
            print("    整齐占比 %.0f%% (%d/%d)" % (
                100.0 * clean_n / max(1, total_n), clean_n, total_n))
            print()

    print("=" * 60)
    total = ok + bad
    print("总体：整齐倍数 %d / %d = %.1f%%" % (
        ok, total, 100.0 * ok / max(1, total)))
    if ok / max(1, total) > 0.7:
        print()
        print("结论：假设成立")
        print("      卡面 = power x STAB x type_eff x buff")
        print("      不含 atk/defn —— 它是「威力等效值」，不是最终伤害")
        print()
        print("对我们的意义：")
        print("  · 卡面不能直接当最终伤害用来校准")
        print("  · 但可以反推 type_eff 和 buff 状态")
        print("  · 最终伤害 = 卡面 x (atk/defn) x 0.9")
    else:
        print()
        print("结论：假设不成立，需要继续研究")
    return 0


if __name__ == "__main__":
    sys.exit(main())
