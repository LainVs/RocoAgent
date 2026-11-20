# -*- coding: utf-8 -*-
"""统计真实 self-buff 技能的数值分布，用来校准 simulator。

结论（已确认）：
    simulator.calc_damage 假设 buff 每层 +20% 攻防、最多 3 层（+60%）。
    roco-cal 的真实数据里**没有这个机制**：每个 buff 技能给一次固定数值，
    且数值分布很宽（20%~140%），也有给敌方挂 debuff 的。

本脚本统计真实分布，供 simulator 改成"用真实技能数值"。
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
            all_skills.append(it)

self_buff = []      # (skill, field, value)
opp_debuff = []     # (skill, field, value)
for s in all_skills:
    for opt in (s.get("buff_options") or []):
        for e in (opt.get("effects") or []):
            tgt = e.get("target")
            rec = (s.get("name"), e.get("field"), e.get("value"))
            if tgt == "self":
                self_buff.append(rec)
            elif tgt == "opponent":
                opp_debuff.append(rec)
    for e in (s.get("buff_effects") or []):
        tgt = e.get("target")
        rec = (s.get("name"), e.get("field"), e.get("value"))
        if tgt == "self":
            self_buff.append(rec)
        elif tgt == "opponent":
            opp_debuff.append(rec)

print("=== self buff 共 %d 条 ===" % len(self_buff))
by_field = {}
for nm, f, v in self_buff:
    by_field.setdefault(f, []).append(v)
for f, vals in sorted(by_field.items(), key=lambda x: -len(x[1])):
    u = sorted(set(vals))
    print("  %-20s n=%-3d 取值: %s" % (f, len(vals), u))

print()
print("=== opponent debuff 共 %d 条 ===" % len(opp_debuff))
by_field2 = {}
for nm, f, v in opp_debuff:
    by_field2.setdefault(f, []).append(v)
for f, vals in sorted(by_field2.items(), key=lambda x: -len(x[1])):
    u = sorted(set(vals))
    print("  %-20s n=%-3d 取值: %s" % (f, len(vals), u))

# 攻击类 buff 的数值分布（决定 simulator 该用什么系数）
atk_buffs = [v for nm, f, v in self_buff
             if f in ("phys_atk_buff", "mag_atk_buff")]
print()
print("=== 攻击类 self buff（simulator 最关心）===")
print("  条数 %d, 取值 %s" % (len(atk_buffs), sorted(set(atk_buffs))))
if atk_buffs:
    print("  均值 %.1f%%  中位 %.1f%%" % (
        sum(atk_buffs) / len(atk_buffs),
        sorted(atk_buffs)[len(atk_buffs) // 2]))

def_buffs = [v for nm, f, v in self_buff
             if f in ("phys_def_buff", "mag_def_buff")]
print()
print("=== 防御类 self buff ===")
print("  条数 %d, 取值 %s" % (len(def_buffs), sorted(set(def_buffs))))

# 关键问题：有 buff 技能的精灵占比
pets_dir = os.path.join("data", "external", "roco-cal", "data",
                        "pets_w_skill_json")
buff_skill_names = set()
for s in all_skills:
    if s.get("buff_options") or s.get("buff_effects"):
        buff_skill_names.add(s.get("name"))

print()
print("=== 带 buff 的技能共 %d 个 ===" % len(buff_skill_names))

if os.path.isdir(pets_dir):
    n_pets = 0
    n_with = 0
    for fn in os.listdir(pets_dir):
        if not fn.endswith(".json"):
            continue
        try:
            data = json.load(open(os.path.join(pets_dir, fn),
                                  encoding="utf-8"))
        except Exception:
            continue
        n_pets += 1
        names = set()
        for it in (data if isinstance(data, list) else [data]):
            if isinstance(it, dict):
                for sk in (it.get("skills") or []):
                    if isinstance(sk, str):
                        names.add(sk)
                    elif isinstance(sk, dict):
                        names.add(sk.get("name"))
        if names & buff_skill_names:
            n_with += 1
    print("  665 只精灵中 %d/%d (%.0f%%) 至少有一个 buff 技能" % (
        n_with, n_pets, 100.0 * n_with / max(1, n_pets)))
