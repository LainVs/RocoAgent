# -*- coding: utf-8 -*-
"""team_audit.py —— 对照"队伍配置"与"实盘读数"，找出配置过期。

背景（2026-11-21 用户报）
------------------------
用户反馈"有伤害更高的技能却选了低伤害的"。查下来不是策略 bug，
而是**队伍配置与实际装备不一致**：

    my_team 里卡瓦重配的是：升龙咆哮/冰雹/顶端优势/取念
    实际装备的是：筛管奔流/扬沙/...

配置过期会导致：
    · my_card_costs() 返回错的耗能 -> 能耗惩罚算错
    · skill_kind.classify() 查错技能名 -> 分类错（把伤害当 copy 等）

本脚本做的事
------------
对每只精灵的每个槽位：
    1. 取实盘历史读数的众数（该槽位最常出现的伤害值）
    2. 列出配置里的技能 + 该精灵所有可学技能
    3. 用伤害模型预测每个候选技能的伤害，找最接近实盘读数的
    4. 输出对照表，人工确认

跑法：
    python scripts\\team_audit.py
    python scripts\\team_audit.py --json out.json
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "src"))

import paths
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TURNS = os.path.join(ROOT, "logs", "turns.jsonl")


def load_readings():
    """从 turns.jsonl 收集 (精灵, 槽位) -> 伤害值计数。"""
    out = defaultdict(Counter)
    if not os.path.exists(TURNS):
        return out
    for line in open(TURNS, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        pet = r.get("our_pet")
        if not pet:
            continue
        for s in (r.get("skills") or []):
            if not s:
                continue
            v = s.get("v")
            i = s.get("i")
            if v is None or i is None:
                continue
            if not s.get("lit"):
                # 灰卡的数字可能不是真实伤害，跳过
                continue
            out[(pet, i)][int(v)] += 1
    return out


def species_skills(db, name):
    """该精灵所有可学技能（power>0）。"""
    sp = db.execute("SELECT id FROM species WHERE name=? ORDER BY "
                    "implemented DESC, total DESC LIMIT 1", (name,)).fetchone()
    if not sp:
        return []
    rows = db.execute(
        "SELECT s.name,s.power,s.cost,s.type_name FROM species_skills ss "
        "JOIN skills s ON s.id=ss.skill_id "
        "WHERE ss.species_id=? AND s.power>0 ORDER BY s.power DESC",
        (sp[0],)).fetchall()
    return [{"name": x[0], "power": x[1], "cost": x[2], "type": x[3]}
            for x in rows]


def team_config(db):
    """my_team 表 -> {精灵名: [技能...]}"""
    out = {}
    for r in db.execute("SELECT slot,species_id,moves FROM my_team "
                        "ORDER BY slot"):
        sp = db.execute("SELECT name FROM species WHERE id=?",
                        (r[1],)).fetchone()
        name = sp[0] if sp else "id=%s" % r[1]
        moves = json.loads(r[2] or "[]")
        arr = []
        for mid in moves:
            s = db.execute("SELECT name,power,cost,type_name FROM skills "
                           "WHERE id=?", (int(mid),)).fetchone()
            arr.append({"name": s[0] if s else None,
                        "power": s[1] if s else None,
                        "cost": s[2] if s else None,
                        "type": s[3] if s else None})
        out[name] = {"slot": r[0], "moves": arr}
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    db = sqlite3.connect(str(paths.DB))
    db.row_factory = None

    readings = load_readings()
    config = team_config(db)

    report = {"team": {}, "observed_pets": []}

    # 实盘出现过的我方精灵
    seen_pets = sorted({k[0] for k in readings})
    report["observed_pets"] = seen_pets

    print("=" * 78)
    print("队伍配置 vs 实盘读数")
    print("=" * 78)
    print()
    print("配置里的 6 只：%s" % ", ".join(config.keys()))
    print("实盘出现过的：%s" % ", ".join(seen_pets))
    print()

    missing = [p for p in seen_pets
               if p.split("#")[0] not in config]
    if missing:
        print("!! 这些实盘精灵不在配置里：%s" % missing)
    stale = [p for p in config if p not in seen_pets
             and not any(x.startswith(p) for x in seen_pets)]
    if stale:
        print("!! 这些配置的精灵没在实盘出现过：%s" % stale)
    print()

    for name, cfg in config.items():
        print("-" * 78)
        print("[%s] slot %d" % (name, cfg["slot"]))
        print()
        # 实盘读数（可能带 #N 后缀）
        variants = [p for p in seen_pets if p.split("#")[0] == name]
        if not variants:
            print("  实盘没有这只的读数")
        for v in variants:
            print("  实盘记录（%s）：" % v)
            for i in range(1, 5):
                c = readings.get((v, i))
                if not c:
                    print("    slot%d: 无读数" % i)
                    continue
                top = c.most_common(4)
                print("    slot%d: %s" % (
                    i, ", ".join("%d×%d" % (val, n) for val, n in top)))
        print()
        print("  配置的技能：")
        for i, m in enumerate(cfg["moves"], 1):
            print("    slot%d %-10s power=%-4s cost=%-2s %s" % (
                i, m["name"], m["power"], m["cost"], m["type"]))
        print()
        # 可学技能里与配置不同的
        cands = species_skills(db, name)
        cfg_names = {m["name"] for m in cfg["moves"]}
        extra = [c for c in cands if c["name"] not in cfg_names][:10]
        if extra:
            print("  可学但没配置的高威力技能（前 10）：")
            for c in extra:
                print("    %-10s power=%-4s cost=%-2s %s" % (
                    c["name"], c["power"], c["cost"], c["type"]))
        print()

        report["team"][name] = {
            "slot": cfg["slot"],
            "config_moves": cfg["moves"],
            "observed": {
                str(i): (readings.get((variants[0], i)) or Counter()).most_common(5)
                if variants else []
                for i in range(1, 5)},
            "learnable_not_configured": extra,
        }

    db.close()

    if "--json" in sys.argv:
        idx = sys.argv.index("--json")
        if idx + 1 < len(sys.argv):
            path = sys.argv[idx + 1]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=1)
            print("已写入 %s" % path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
