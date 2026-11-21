# -*- coding: utf-8 -*-
"""诊断：卡瓦重/冰钻布鲁斯的技能配置与实盘读数是否对得上。

用户反馈（2026-11-21）
--------------------
1. 冰钻布鲁斯 vs 独角兽：丢冰块伤害更高，却选了主轴
2. 卡瓦重：有伤害更高的筛管奔流，却选了伤害最低的扬沙
3. 怀疑"主轴颜色是绿色 -> 被识别成增益"

要查的关键点
-----------
A. 队伍配置里的技能，是否就是用户实际装备的技能？
   （如果配置过期，cost / skill_kind / 分类全部会错）
B. 实盘 turns.jsonl 里这两只精灵的卡面读数（value/color/kind）
C. color 是否参与了任何决策逻辑
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, "src")
import paths

OUT = os.path.join(os.environ.get("TEMP", "."), "diag_user_skills.json")

db = sqlite3.connect(str(paths.DB))
db.row_factory = sqlite3.Row

result = {}

# ---------- A. 队伍配置 vs 可学技能 ----------
team = {}
for r in db.execute("SELECT slot, species_id, moves FROM my_team ORDER BY slot"):
    sp = db.execute("SELECT name FROM species WHERE id=?",
                    (r["species_id"],)).fetchone()
    name = sp["name"] if sp else "id=%s" % r["species_id"]
    moves = json.loads(r["moves"] or "[]")
    detail = []
    for mid in moves:
        s = db.execute("SELECT name,power,cost,type_name FROM skills WHERE id=?",
                       (int(mid),)).fetchone()
        detail.append({
            "id": mid,
            "name": s["name"] if s else None,
            "power": s["power"] if s else None,
            "cost": s["cost"] if s else None,
            "type": s["type_name"] if s else None,
        })
    team[name] = {"slot": r["slot"], "moves": detail}
result["队伍配置"] = team

# ---------- 卡瓦重 / 冰钻布鲁斯 的可学技能 ----------
for nm in ("卡瓦重", "冰钻布鲁斯"):
    sp = db.execute("SELECT id,name,type_names FROM species WHERE name=?",
                    (nm,)).fetchone()
    if not sp:
        result.setdefault("可学技能", {})[nm] = "species 表查不到"
        continue
    rows = db.execute(
        "SELECT s.name,s.power,s.cost,s.type_name FROM species_skills ss "
        "JOIN skills s ON s.id=ss.skill_id "
        "WHERE ss.species_id=? ORDER BY s.power DESC", (sp["id"],)).fetchall()
    result.setdefault("可学技能", {})[nm] = {
        "species_id": sp["id"],
        "types": sp["type_names"],
        "total": len(rows),
        "top20": [
            {"name": x["name"], "power": x["power"], "cost": x["cost"],
             "type": x["type_name"]} for x in rows[:20]],
    }

# ---------- B. 实盘读数 ----------
rows = []
p = os.path.join("logs", "turns.jsonl")
if os.path.exists(p):
    for line in open(p, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass

for nm in ("卡瓦重", "冰钻布鲁斯"):
    sel = [r for r in rows if nm in (r.get("our_pet") or "")]
    sample = []
    for r in sel[-8:]:
        sk = []
        for s in (r.get("skills") or []):
            if not s:
                sk.append(None)
                continue
            sk.append({"i": s.get("i"), "v": s.get("v"), "c": s.get("c"),
                       "t": s.get("t"), "lit": s.get("lit")})
        sample.append({"ts": r.get("ts"), "turn": r.get("turn"),
                       "foe": r.get("foe_pet"), "action": r.get("action"),
                       "why": r.get("why"), "skills": sk})
    result.setdefault("实盘读数", {})[nm] = {
        "count": len(sel), "last8": sample}

# ---------- C. color 是否影响决策 ----------
import re
src_hits = {}
for fn in ("policy_v2.py", "perceive.py", "robot.py",
           "policy_legacy.py", "digitread.py"):
    fp = os.path.join("src", fn)
    if not os.path.exists(fp):
        continue
    txt = open(fp, encoding="utf-8", errors="ignore").read()
    hits = []
    for m in re.finditer(r'^.*\bcolor\b.*$', txt, re.M):
        line = m.group(0).strip()
        if line.startswith("#"):
            continue
        hits.append(line[:110])
    src_hits[fn] = hits[:14]
result["color_用法"] = src_hits

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=1)
print("已写入 %s" % OUT)
