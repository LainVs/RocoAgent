# -*- coding: utf-8 -*-
"""统计 roco-cal 技能的全部效果类型，用来设计技能分类。

目标：把 policy_v2 里"kind==dash 一律当 buff"这个错误分类拆开。
真实数据里有这些类别：
    · 纯伤害        skill_power > 0
    · 自己增益      buff_effects target=self（phys_atk_buff 等）
    · 给敌减益      buff_effects target=opponent
    · 减伤/护盾     damage_reduction
    · 复制技能      借用 / 取念（power=0，无 buff 字段）
    · 治疗          description 含 回复/恢复
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, "src")

SKILL_DIR = os.path.join("data", "external", "roco-cal", "skills")
all_skills = []
for fn in sorted(os.listdir(SKILL_DIR)):
    if not fn.endswith(".json"):
        continue
    for it in json.load(open(os.path.join(SKILL_DIR, fn), encoding="utf-8")):
        if isinstance(it, dict):
            it["_element"] = fn.replace(".json", "")
            all_skills.append(it)

def effects_of(s):
    effs = []
    for opt in (s.get("buff_options") or []):
        effs.extend(opt.get("effects") or [])
    effs.extend(s.get("buff_effects") or [])
    return effs

# 按"有伤害 / 无伤害"分组
dmg = [s for s in all_skills if (s.get("skill_power") or 0) > 0]
nondmg = [s for s in all_skills if (s.get("skill_power") or 0) <= 0]

print("技能总数 %d：有伤害 %d，无伤害 %d" % (
    len(all_skills), len(dmg), len(nondmg)))

print()
print("=== 无伤害技能的构成 ===")
cat = Counter()
detail = {}
for s in nondmg:
    effs = effects_of(s)
    self_stat = [e for e in effs
                 if e.get("target") == "self"
                 and e.get("field") in ("phys_atk_buff", "mag_atk_buff",
                                        "phys_def_buff", "mag_def_buff",
                                        "speed_buff")]
    opp = [e for e in effs if e.get("target") == "opponent"]
    dr = s.get("damage_reduction")
    desc = s.get("description") or ""
    if self_stat:
        k = "self_buff"
    elif opp:
        k = "enemy_debuff"
    elif dr:
        k = "damage_reduction"
    elif "回复" in desc or "恢复" in desc or "治疗" in desc:
        k = "heal"
    elif "变成" in desc or "复制" in desc:
        k = "copy"
    else:
        k = "other"
    cat[k] += 1
    detail.setdefault(k, []).append(s)

for k, n in cat.most_common():
    print("  %-18s %d" % (k, n))

print()
print("=== 各类举例 ===")
for k in ("self_buff", "enemy_debuff", "damage_reduction", "copy", "heal",
          "other"):
    items = detail.get(k) or []
    if not items:
        continue
    print()
    print("  [%s] 共 %d，前 6：" % (k, len(items)))
    for s in items[:6]:
        print("    %-12s cost=%-2s dr=%-5s | %s" % (
            s.get("name"), s.get("cost"), s.get("damage_reduction"),
            (s.get("description") or "")[:56]))

print()
print("=== 无伤害技能里 damage_reduction 分布 ===")
drs = sorted(set(s.get("damage_reduction") for s in nondmg
                 if s.get("damage_reduction")))
print(" ", drs)
