# -*- coding: utf-8 -*-
"""查实盘队伍用到的技能 ID，准备写入 my_team。"""
import json
import sqlite3
import sys

sys.path.insert(0, "src")
import paths

db = sqlite3.connect(str(paths.DB))
db.row_factory = sqlite3.Row

TEAM = {
    "音速犬": ["借用", "火云车", "诋毁", "闪光冲击"],
    "冰钻布鲁斯": ["丢冰块", "暗突袭", "主轴", "扬沙"],
    "卡瓦重": ["筛管奔流", "扬沙", "虫击", "冰雹"],
    "银月狼王": ["岩脉崩毁", "月蚀", "撞鬼", "荆棘爪"],
    "恶魔狼": ["吞噬", "火焰切割", "蝙蝠", "惊吓盒子"],
}

out = {}
missing = []
for pet, moves in TEAM.items():
    # species
    sp = db.execute("SELECT id,name,type_names FROM species WHERE name=? "
                    "ORDER BY implemented DESC, total DESC LIMIT 1",
                    (pet,)).fetchone()
    if not sp:
        missing.append(("species", pet))
        continue
    ids = []
    detail = []
    for mv in moves:
        r = db.execute("SELECT id,name,power,cost,type_name FROM skills "
                       "WHERE name=? LIMIT 1", (mv,)).fetchone()
        if not r:
            missing.append(("skill", mv))
            detail.append({"name": mv, "id": None})
            continue
        ids.append(r["id"])
        detail.append({"name": r["name"], "id": r["id"],
                       "power": r["power"], "cost": r["cost"],
                       "type": r["type_name"]})
    out[pet] = {"species_id": sp["id"], "types": sp["type_names"],
                "move_ids": ids, "detail": detail}

print(json.dumps({"team": out, "missing": missing},
                 ensure_ascii=False, indent=1))
