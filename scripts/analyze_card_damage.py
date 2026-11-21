# -*- coding: utf-8 -*-
"""analyze_card_damage.py —— 破解卡面伤害的真实语义。

用户的关键洞察（2026-11-21）
--------------------------
火云车是火系 150（实际 power=140）伤害
  算上本系加成是 175
  克制是 350
  火狗（音速犬）刚上场伤害加倍 + 克制是 700

反推：
    140 x 1.25 = 175      <- 本系 STAB
    175 x 2    = 350      <- 属性克制
    350 x 2    = 700      <- 专注力（入场首回合物攻 +100%）

**关键结论**：卡面数字 = 威力 x STAB x 克制 x buff，
**不含 atk/defn 的除法**。也就是说卡面显示的是"威力等效值"，
不是最终伤害。

这解释了为什么我们的模拟器（含 atk/defn）算不对 ——
两边算的根本不是同一个量。

本脚本用全部历史读数验证这个假设。
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

TURNS = os.path.join(ROOT, "logs", "turns.jsonl")

# 我方精灵的 STAB 判定（技能系 == 精灵系）
# 音速犬 火系；卡瓦重 草/冰；银月狼王 幽/幻；恶魔狼 恶；冰钻布鲁斯 冰/地
SPECIES_TYPES = {
    "音速犬": ["火"],
    "冰钻布鲁斯": ["冰", "地"],
    "卡瓦重": ["草", "冰"],
    "银月狼王": ["幽", "幻"],
    "恶魔狼": ["恶"],
    "wolf": ["火"],
    "wolf_leader": ["火"],
}


def base_name(name):
    if not name:
        return name
    for sep in ("（", "("):
        if sep in name:
            return name.split(sep)[0]
    return name


def load_skill_power():
    """技能名 -> (power, type_name)"""
    import sqlite3
    import paths
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    out = {}
    for r in db.execute("SELECT name,power,type_name FROM skills"):
        # 同名多条时取 power 最大的（避开脏数据）
        p = int(r["power"] or 0)
        if r["name"] not in out or p > out[r["name"]][0]:
            out[r["name"]] = (p, r["type_name"])
    db.close()
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    skills = load_skill_power()

    # 按 (精灵, 槽位) 收集读数
    by_slot = defaultdict(Counter)
    for line in open(TURNS, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        me = base_name(r.get("our_pet"))
        if not me:
            continue
        for s in (r.get("skills") or []):
            if not s or s.get("v") is None:
                continue
            by_slot[(me, s.get("i"))][s["v"]] += 1

    # 每个精灵的装备技能（按槽位）+ 属性（用 sim 的英文 Type）
    import sim.adapter as A
    equipped = {}
    species_types = {}
    for me in list(SPECIES_TYPES):
        try:
            p = A.build_pokemon(me)
            species_types[me] = [p.pokemon_type.value]
            equipped[me] = [(s.name, s.power,
                             s.skill_type.value if hasattr(s.skill_type, "value")
                             else str(s.skill_type))
                            for s in p.skills]
        except Exception:
            pass

    print("=== 假设：卡面 = power x STAB x 克制 x buff（不含 atk/defn）===")
    print()

    total_ok = total_bad = 0
    for (me, slot), cnt in sorted(by_slot.items()):
        eq = equipped.get(me)
        if not eq or slot is None or slot > len(eq):
            continue
        sname, power, stype = eq[slot - 1]
        if power <= 0:
            continue
        my_types = species_types.get(me, [])
        stab = 1.25 if stype in my_types else 1.0
        base = power * stab

        print("[%s] slot%d %s  power=%d 系=%s STAB=%.2f -> 基数 %.1f"
              % (me, slot, sname, power, stype, stab, base))
        # 每个读数 / 基数 应该是个"整齐"的倍数
        ratios = Counter()
        for v, n in cnt.items():
            ratio = v / base
            ratios[round(ratio, 2)] += n
        top = ratios.most_common(6)
        print("    读数/基数 的分布（前 6）: %s"
              % ", ".join("%.2fx(%d)" % (r, n) for r, n in top))
        # 判断：多数读数应是 0.5 / 1 / 2 / 4 这种整齐倍数
        clean = sum(n for r, n in ratios.items()
                    if any(abs(r - t) < 0.08 for t in (0.25, 0.5, 1.0, 2.0, 4.0)))
        totaln = sum(ratios.values())
        total_ok += clean
        total_bad += (totaln - clean)
        print("    整齐倍数占比: %.0f%% (%d/%d)" % (
            100.0 * clean / max(1, totaln), clean, totaln))
        print()

    print("=" * 60)
    print("总体：整齐倍数 %d / %d = %.1f%%" % (
        total_ok, total_ok + total_bad,
        100.0 * total_ok / max(1, total_ok + total_bad)))
    if total_ok / max(1, total_ok + total_bad) > 0.7:
        print("结论：假设成立 —— 卡面 = 威力 x STAB x 克制 x buff")
        print("      不含 atk/defn，是「威力等效值」而非最终伤害")
    else:
        print("结论：假设不成立，需要继续研究")
    return 0


if __name__ == "__main__":
    sys.exit(main())
