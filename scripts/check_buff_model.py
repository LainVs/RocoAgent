# -*- coding: utf-8 -*-
"""检查 buff 模型是否有真实依据。

问题：simulator.calc_damage 假设 buff 每层 +20% 攻防，叠满 3 层 = +60%。
如果游戏里没有这个机制，那基于它的 64.3% 胜率就是拟合虚构规则。
"""
import sys
import sqlite3

sys.path.insert(0, "src")
import paths

db = sqlite3.connect(str(paths.DB))
db.row_factory = sqlite3.Row

print("=== skills 表结构 ===")
for r in db.execute("PRAGMA table_info(skills)"):
    print("  %s %s" % (r["name"], r["type"]))

print()
print("=== 所有表 ===")
for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    print(" ", r["name"])

print()
print("=== power=0 的技能（候选 buff/状态技能）前 15 ===")
for r in db.execute(
        "SELECT name,power,cost,type_name FROM skills WHERE power=0 "
        "ORDER BY name LIMIT 15"):
    print("  %-14s cost=%s type=%s" % (r["name"], r["cost"], r["type_name"]))

print()
print("=== 技能表里是否有 buff/stat 相关列 ===")
cols = [r["name"] for r in db.execute("PRAGMA table_info(skills)")]
print(" ", cols)

print()
print("=== roco-cal 里 buff 的定义 ===")
import os
import json
base = os.path.join("data", "external", "roco-cal")
hits = 0
for root, dirs, files in os.walk(base):
    dirs[:] = [d for d in dirs if d not in ("node_modules", ".git")]
    for fn in files:
        if not fn.endswith((".json", ".py")):
            continue
        p = os.path.join(root, fn)
        try:
            txt = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        low = txt.lower()
        if "buff" in low or "stat_up" in low or "stage" in low:
            hits += 1
            if hits <= 8:
                print("  hit: %s" % p.replace(base, "..."))
print("  共 %d 个文件提到 buff/stat_up/stage" % hits)
