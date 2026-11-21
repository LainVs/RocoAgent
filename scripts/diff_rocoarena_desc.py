# -*- coding: utf-8 -*-
"""diff_rocoarena_desc.py —— 对比站点技能描述与我们库的差异。

132 处不一致，需要判断是：
    a) 我们错了（数据过期/录入错）
    b) 站点更简略（不影响）
    c) 真的不同（需要核实）

跑法：
    python scripts\\diff_rocoarena_desc.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths

TEAMS = os.path.join(ROOT, "data", "external", "rocoarena", "teams.json")


def norm(s):
    s = (s or "").strip()
    for ch in "。，、；：（）() ":
        s = s.replace(ch, "")
    return s


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    d = json.load(open(TEAMS, encoding="utf-8"))
    pool = {}
    for t in d.get("teams") or []:
        for m in (t.get("members") or []):
            for sp in (m.get("skillPool") or []):
                n = sp.get("name")
                if n and n not in pool:
                    pool[n] = sp

    db = sqlite3.connect(str(paths.DB))
    ours = {}
    for r in db.execute("SELECT name,power,cost,type_name,desc FROM skills"):
        ours[r[0]] = {"power": r[1], "cost": r[2], "type": r[3], "desc": r[4]}
    db.close()

    diffs = []
    for n, sp in pool.items():
        o = ours.get(n)
        if not o:
            continue
        a = norm(o["desc"])
        b = norm(sp.get("effect"))
        if a == b:
            continue
        diffs.append({
            "name": n,
            "ours_desc": o["desc"],
            "site_desc": sp.get("effect"),
            "ours_power": o["power"],
            "site_power": sp.get("power"),
            "ours_cost": o["cost"],
            "site_cost": sp.get("energyCost"),
            "ours_type": o["type"],
            "site_type": sp.get("type"),
        })

    print("描述不一致: %d 个" % len(diffs))
    print()

    # 按性质分类
    num_mismatch = [x for x in diffs if x["ours_power"] != x["site_power"]]
    cost_mismatch = [x for x in diffs if x["ours_cost"] != x["site_cost"]]
    type_mismatch = [x for x in diffs
                     if (x["ours_type"] or "") != (x["site_type"] or "")]

    print("=== 数值差异（更严重）===")
    print("  威力不一致: %d" % len(num_mismatch))
    print("  耗能不一致: %d" % len(cost_mismatch))
    print("  属性不一致: %d" % len(type_mismatch))
    print()

    if num_mismatch:
        print("=== 威力不一致明细（前 25）===")
        for x in num_mismatch[:25]:
            print("  %-12s 我们 %-4s vs 站点 %-4s" % (
                x["name"], x["ours_power"], x["site_power"]))

    if cost_mismatch:
        print()
        print("=== 耗能不一致明细（前 25）===")
        for x in cost_mismatch[:25]:
            print("  %-12s 我们 %-2s vs 站点 %-2s" % (
                x["name"], x["ours_cost"], x["site_cost"]))

    if type_mismatch:
        print()
        print("=== 属性不一致明细（前 15）===")
        for x in type_mismatch[:15]:
            print("  %-12s 我们 %-4s vs 站点 %-4s" % (
                x["name"], x["ours_type"], x["site_type"]))

    # 只描述不同的
    only_desc = [x for x in diffs
                 if x["ours_power"] == x["site_power"]
                 and x["ours_cost"] == x["site_cost"]
                 and (x["ours_type"] or "") == (x["site_type"] or "")]
    print()
    print("=== 只有描述不同（数值一致）: %d 个 ===" % len(only_desc))
    for x in only_desc[:12]:
        print("  [%s]" % x["name"])
        print("    我们: %s" % (x["ours_desc"] or "")[:70])
        print("    站点: %s" % (x["site_desc"] or "")[:70])

    return 0


if __name__ == "__main__":
    sys.exit(main())
