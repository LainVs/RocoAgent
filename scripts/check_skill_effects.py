# -*- coding: utf-8 -*-
"""检查：技能效果数据存了哪些，机器人决策用不用。

用户问："你知道技能效果吗？我们的机器人知道技能效果吗？"
"""
import json
import sqlite3
import sys

sys.path.insert(0, "src")
import paths

db = sqlite3.connect(str(paths.DB))
db.row_factory = sqlite3.Row

print("=== skills 表结构 ===")
for r in db.execute("PRAGMA table_info(skills)"):
    print("  %-12s %s" % (r["name"], r["type"]))

print()
print("=== 实盘队伍用到的技能，effects 字段内容 ===")
names = ["借用", "火云车", "诋毁", "闪光冲击",
         "丢冰块", "暗突袭", "主轴", "扬沙",
         "筛管奔流", "虫击", "冰雹",
         "岩脉崩毁", "月蚀", "撞鬼", "荆棘爪",
         "吞噬", "火焰切割", "蝙蝠", "惊吓盒子"]
for nm in names:
    r = db.execute("SELECT name,power,cost,type_name,effects,desc "
                   "FROM skills WHERE name=? LIMIT 1", (nm,)).fetchone()
    if not r:
        print("  %-10s (查不到)" % nm)
        continue
    print("  %-10s power=%-4s cost=%-2s type=%-4s effects=%-6s" % (
        r["name"], r["power"], r["cost"], r["type_name"], r["effects"]))
    print("      desc: %s" % (r["desc"] or "")[:100])

print()
print("=== effects 字段的取值分布 ===")
for r in db.execute("SELECT effects, COUNT(*) n FROM skills "
                    "WHERE effects IS NOT NULL GROUP BY effects "
                    "ORDER BY n DESC LIMIT 10"):
    print("  effects=%-6s %d 个技能" % (r["effects"], r["n"]))
db.close()
