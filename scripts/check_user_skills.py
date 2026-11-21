# -*- coding: utf-8 -*-
"""查用户提到的技能与对手，输出 JSON 避免控制台乱码。"""
import json
import sqlite3
import sys

sys.path.insert(0, "src")
import paths
import statmodel as S

db = sqlite3.connect(str(paths.DB))
db.row_factory = sqlite3.Row

out = {"skills": {}, "species": {}}

for r in db.execute(
        "SELECT name,power,cost,type_name FROM skills WHERE name IN "
        "('筛管奔流','扬沙','丢冰块','主轴')"):
    out["skills"][r["name"]] = {
        "power": r["power"], "cost": r["cost"], "type": r["type_name"]}

for r in db.execute(
        "SELECT name,type_names,atk,spa FROM species WHERE name IN "
        "('卡瓦重','冰钻布鲁斯','白金独角兽','独角兽')"):
    out["species"][r["name"]] = {
        "types": r["type_names"], "atk": r["atk"], "spa": r["spa"]}

# 克制矩阵
if "白金独角兽" in out["species"]:
    types = json.loads(out["species"]["白金独角兽"]["types"] or "[]")
    out["白金独角兽_克制"] = {
        t: S.type_effect(t, types)
        for t in ("冰", "地", "机械", "普通", "草", "水", "火")}

print(json.dumps(out, ensure_ascii=False, indent=1))
