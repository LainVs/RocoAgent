# -*- coding: utf-8 -*-
"""analyze_rocoarena.py —— 分析 ROCO ARENA 样本库能补什么。

重点回答三个问题：
    1. 我们队伍里的精灵，站点上别人怎么配？（同一精灵的多种配法）
    2. 技能池里有多少技能是我们库里没有的？
    3. 技能效果描述能补多少 roco-cal / nrc-ai 的漏？

跑法：
    python scripts\\analyze_rocoarena.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths

TEAMS = os.path.join(ROOT, "data", "external", "rocoarena", "teams.json")

# 我们的队伍
OUR_TEAM = ["音速犬", "冰钻布鲁斯", "卡瓦重", "银月狼王", "恶魔狼"]


def base_name(name):
    """'音速犬（看守麦田的样子）' -> '音速犬'"""
    if not name:
        return name
    for sep in ("（", "("):
        if sep in name:
            return name.split(sep)[0]
    return name


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if not os.path.exists(TEAMS):
        print("先跑 scripts\\fetch_rocoarena.py")
        return 1

    d = json.load(open(TEAMS, encoding="utf-8"))
    teams = d.get("teams") or []
    print("样本库: %d 个阵容" % len(teams))

    # 收集所有成员的配法
    builds = defaultdict(list)      # 精灵名 -> [(技能名列表, 特性, 性格)]
    pool = {}                       # 技能名 -> 技能信息
    abilities = {}
    for t in teams:
        for m in (t.get("members") or []):
            nm = base_name(m.get("name"))
            if not nm:
                continue
            skills = tuple(s.get("name") for s in (m.get("skills") or []))
            builds[nm].append({
                "skills": skills,
                "ability": m.get("abilityName"),
                "nature": m.get("nature"),
                "team": t.get("name"),
            })
            for sp in (m.get("skillPool") or []):
                n = sp.get("name")
                if n and n not in pool:
                    pool[n] = sp
            ab = m.get("abilityName")
            if ab and ab not in abilities:
                abilities[ab] = m.get("abilityEffect")

    print("涉及精灵: %d 种" % len(builds))
    print("技能池: %d 个技能" % len(pool))
    print("特性: %d 个" % len(abilities))
    print()

    # 1. 我们队伍的配法多样性
    print("=" * 70)
    print("一、我们队伍里的精灵，站点上别人怎么配")
    print("=" * 70)
    for nm in OUR_TEAM:
        rows = builds.get(nm)
        if not rows:
            print()
            print("  [%s] 站点没有收录" % nm)
            continue
        print()
        print("  [%s] 共 %d 种配法" % (nm, len(rows)))
        cnt = Counter(r["skills"] for r in rows)
        for skills, n in cnt.most_common(6):
            print("    %dx  %s" % (n, " / ".join(x for x in skills if x)))
        # 特性
        abs_ = Counter(r["ability"] for r in rows if r["ability"])
        if abs_:
            print("    特性: %s" % ", ".join(
                "%s×%d" % (a, n) for a, n in abs_.most_common(3)))

    # 2. 技能池 vs 我们的库
    print()
    print("=" * 70)
    print("二、技能池 vs 我们的库")
    print("=" * 70)
    db = sqlite3.connect(str(paths.DB))
    ours = {r[0] for r in db.execute("SELECT name FROM skills")}
    ours_cost = {}
    for r in db.execute("SELECT name,power,cost,type_name,desc FROM skills"):
        ours_cost[r[0]] = r
    db.close()

    only_site = [n for n in pool if n not in ours]
    both = [n for n in pool if n in ours]
    print("  站点技能池 %d 个：我们库有 %d，没有 %d" % (
        len(pool), len(both), len(only_site)))
    if only_site:
        print("  我们没有的（前 20）:")
        for n in sorted(only_site)[:20]:
            sp = pool[n]
            print("    %-12s p=%-4s c=%-2s %s" % (
                n, sp.get("power"), sp.get("energyCost"), sp.get("type")))

    # 3. 效果描述覆盖
    print()
    print("=" * 70)
    print("三、效果描述能否补 roco-cal / nrc-ai 的漏")
    print("=" * 70)
    try:
        sys.path.insert(0, os.path.join(ROOT, "data", "external", "roco-cal"))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "nrc_ed",
            os.path.join(ROOT, "data", "external", "nrc-ai", "src",
                         "effect_data.py"))
        # nrc 的 effect_data 依赖 src.effect_models，这里只做粗略统计
        nrc_has = None
    except Exception:
        nrc_has = None

    # 用 desc 对比
    filled = 0
    empty = 0
    diff = 0
    for n, sp in pool.items():
        eff = (sp.get("effect") or "").strip()
        if not eff:
            empty += 1
            continue
        filled += 1
        row = ours_cost.get(n)
        if row and (row[4] or "").strip() != eff:
            diff += 1
    print("  站点技能池有 effect 描述: %d / %d" % (filled, len(pool)))
    print("  与我们的 desc 不一致: %d" % diff)

    # 4. 我们队伍 19 个技能在站点池里的情况
    print()
    print("=" * 70)
    print("四、我们队伍 19 个技能在站点池里")
    print("=" * 70)
    our_skills = ["借用", "火云车", "诋毁", "闪光冲击",
                  "丢冰块", "暗突袭", "主轴", "扬沙",
                  "筛管奔流", "虫击", "冰雹",
                  "岩脉崩毁", "月蚀", "撞鬼", "荆棘爪",
                  "吞噬", "火焰切割", "蝙蝠", "惊吓盒子"]
    hit = 0
    for n in our_skills:
        if n in pool:
            hit += 1
            eff = (pool[n].get("effect") or "")[:50]
            print("  %-10s OK  %s" % (n, eff))
        else:
            print("  %-10s --  站点池里没有" % n)
    print()
    print("  覆盖 %d / %d" % (hit, len(our_skills)))

    return 0


if __name__ == "__main__":
    sys.exit(main())
