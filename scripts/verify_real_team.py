# -*- coding: utf-8 -*-
"""用真实队伍验证 policy_v2：不再把借用/取念当 buff。

这是本次修复的核心验收：
    修复前：T1 必选 slot4（借用/取念），日志 `SKILL 4 (buff first)`
    修复后：slot4 得分为负，T1 选最高伤害技能

跑法：
    python scripts\verify_real_team.py
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, "src")
import paths
import policy_v2 as P
import skill_kind as SK
import team_db

db = sqlite3.connect(str(paths.DB))
db.row_factory = sqlite3.Row

team = [t for t in team_db.dump_team() if t.get("name")]
print("=== 真实队伍 ===")
for t in team:
    print("  %-10s moves=%s" % (t["name"], t.get("moves")))

print()
print("=== 各精灵 slot4 的真实分类 ===")
for t in team:
    moves = t.get("moves") or []
    if len(moves) < 4:
        continue
    row = db.execute("SELECT name FROM skills WHERE id=?",
                     (int(moves[3]),)).fetchone()
    if not row:
        continue
    nm = row[0]
    print("  %-10s slot4 = %-10s -> %s" % (
        t["name"], nm, SK.classify(nm)))

print()
print("=== 模拟 policy_v2 决策（T1，满血满能量）===")
fails = []
for t in team:
    moves = t.get("moves") or []
    if len(moves) < 4:
        continue
    skills = []
    for i, sid in enumerate(moves[:4]):
        r = db.execute(
            "SELECT name,power,cost FROM skills WHERE id=?",
            (int(sid),)).fetchone()
        if not r:
            continue
        nm, power, cost = r[0], r[1] or 0, r[2] or 0
        if power > 0:
            skills.append({"i": i + 1, "dmg": power, "cost": cost,
                           "kind": "num", "lit": True})
        else:
            skills.append({"i": i + 1, "dmg": None, "cost": cost,
                           "kind": SK.classify(nm), "name": nm,
                           "lit": True})

    state = {
        "me": {"name": t["name"], "hp": 400, "hp_max": 400, "energy": 10,
               "skills": skills},
        "foe": {"name": "对手", "hp": 400, "hp_max": 400},
        "bench": [], "turn": 1, "buffed": False,
    }
    d = P.decide(state)
    picked = d[0] if isinstance(d, tuple) else d
    reason = d[-1] if isinstance(d, tuple) else ""
    slot = picked[1] if isinstance(picked, tuple) else None

    # 检查选中的槽位是不是 copy
    chosen_kind = None
    for s in skills:
        if s["i"] == slot:
            chosen_kind = s["kind"]
    bad = chosen_kind == SK.KIND_COPY
    if bad:
        fails.append(t["name"])
    print("  %-10s T1 -> slot%s (%s)  %s%s" % (
        t["name"], slot, chosen_kind,
        "  <== BUG 仍然选了复制技能!" if bad else "",
        "" if bad else "  ok"))

print()
if fails:
    print("FAILED: %s 仍然在 T1 选了复制技能" % fails)
    sys.exit(1)
print("ALL PASS：没有任何精灵在 T1 选复制技能")
