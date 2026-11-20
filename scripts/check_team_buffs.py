# -*- coding: utf-8 -*-
"""用真实队伍（my_team 表）查技能，标注哪些是 buff 技能、给多少数值。

simulator 目前用"每层 +20% 攻防、最多 3 层"这个虚构模型。
本脚本确认真实队伍里到底有没有 buff 技能，以及它们的真实数值，
用来判断 simulator 该怎么改。
"""
import json
import os
import sys
import sqlite3

sys.path.insert(0, "src")
import paths

db = sqlite3.connect(str(paths.DB))
db.row_factory = sqlite3.Row

# roco-cal 的 buff 技能表
SKILL_DIR = os.path.join("data", "external", "roco-cal", "skills")
buff_skills = {}
for fn in sorted(os.listdir(SKILL_DIR)):
    if not fn.endswith(".json"):
        continue
    for it in json.load(open(os.path.join(SKILL_DIR, fn), encoding="utf-8")):
        if not isinstance(it, dict):
            continue
        effs = []
        for opt in (it.get("buff_options") or []):
            effs.extend(opt.get("effects") or [])
        effs.extend(it.get("buff_effects") or [])
        if effs:
            buff_skills[it.get("name")] = effs

print("roco-cal 里 buff 技能共 %d 个" % len(buff_skills))

rows = db.execute("SELECT * FROM my_team ORDER BY slot").fetchall()
print()
print("=== 真实队伍技能明细 ===")
n_buff = 0
for r in rows:
    sp = db.execute("SELECT name FROM species WHERE id=?",
                    (r["species_id"],)).fetchone()
    spname = sp["name"] if sp else "id=%s" % r["species_id"]
    mids = json.loads(r["moves"] or "[]")
    print()
    print("  slot%d %s  moves=%s" % (r["slot"], spname, mids))
    for mid in mids:
        s = db.execute(
            "SELECT id,name,power,cost,type_name FROM skills WHERE id=?",
            (mid,)).fetchone()
        if not s:
            print("    id=%-9s (skills 表无此技能)" % mid)
            continue
        eff = buff_skills.get(s["name"])
        tag = ""
        if eff:
            n_buff += 1
            tag = "  <BUFF %s>" % json.dumps(eff, ensure_ascii=False)
        print("    %-9s %-12s power=%-4s cost=%-2s type=%s%s" % (
            s["id"], s["name"], s["power"], s["cost"],
            s["type_name"], tag))

print()
print("=== 结论 ===")
print("真实队伍 24 个技能位中，%d 个是 buff 技能" % n_buff)
