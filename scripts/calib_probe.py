# -*- coding: utf-8 -*-
"""离线标定：用用户截图里的 4 张技能卡伤害数字，反推伤害公式的常数。

截图（恶魔狼 417/417 vs 圣剑-X 84%）：
    1 吞噬      142
    2 火焰切割  380
    3 骗术      160
    4 有效预防  +1（卡片右上角 +1，伤害栏可能不是 100）

思路：
    卡片数字 = 威力 × 本系加成 × 属性倍率 × 等级系数 × 全局系数
    （用户确认过：不含防御）
    把已知量代进去，看剩下的常数是不是同一个值。
"""
import json
import os
import sys

BASE = r"D:\GameData\roco_data\game"
OUT = os.path.join(os.environ["TEMP"], "oc_calib.txt")

def load(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return json.load(f)

def main():
    out = []
    def p(*a):
        out.append(" ".join(str(x) for x in a))

    species = load("pet_species.json")
    skills = load("skill_map.json")
    pet_skills = load("pet_skill_map.json")
    chart = load("type_chart.json")

    p("== type_chart ==")
    types = chart.get("types", [])
    p("types:", len(types))
    p("sample:", json.dumps(types[0], ensure_ascii=False)[:300])
    # 找克制关系表在哪个字段
    for t in types[:2]:
        p("keys:", list(t.keys()))

    # 恶魔狼
    wolf = [(k, v) for k, v in species.items() if "恶魔狼" in v.get("name", "")]
    p("\n== 恶魔狼 candidates ==", len(wolf))
    for k, v in wolf:
        p(k, v.get("name"), v.get("types"), v.get("type_names"),
          "hp", v.get("stats", {}).get("hp"), "atk", v.get("stats", {}).get("atk"),
          "spa", v.get("stats", {}).get("spa"),
          "leader", v.get("is_leader"), "energy", v.get("max_energy"))

    if not wolf:
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        return

    wid = wolf[0][0]
    p("\n== pet_skill_map for", wid, "==")
    entry = pet_skills.get(str(wid)) or pet_skills.get(wid)
    p(json.dumps(entry, ensure_ascii=False)[:800] if entry else "MISSING")

    # 截图里的 4 张卡
    cards = [("吞噬", 142), ("火焰切割", 380), ("骗术", 160), ("有效预防", 100)]
    p("\n== skill lookup by name ==")
    by_name = {}
    for sid, s in skills.items():
        by_name.setdefault(s.get("name"), []).append((sid, s))
    for nm, dmg in cards:
        hits = by_name.get(nm, [])
        p("%s -> %d skill(s)" % (nm, len(hits)))
        for sid, s in hits[:3]:
            p("   id", sid, "type", s.get("type"), "dam_para", s.get("dam_para"),
              "energy", s.get("energy_cost"), "dam_type", s.get("skill_dam_type"),
              "prio", s.get("skill_priority"), "hit", s.get("hit_para"))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out))

if __name__ == "__main__":
    main()
    print("written")
