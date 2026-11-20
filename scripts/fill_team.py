# -*- coding: utf-8 -*-
"""fill_team.py —— 按实盘观察补我方队伍配置（team_db）。

为什么需要
----------
team_db 里只配了 1 只（恶魔狼），而实盘出战的至少有 6 只。
后果：
  · my_card_costs 查不到技能 -> 卡槽耗能不准
  · bench_state 为空 -> 换人决策无数据
  · _fix_status_kinds 路线 1 失效

阵容来源
--------
logs/turns.jsonl 里 our_pet 字段的统计（2026-11-21 实测）：
    恶魔狼 190 / 音速犬 183 / 刨雪鹅 134 / 银月狼王 69 / 冥古龙 67 / 卡瓦重 43

技能配置
--------
从库里每只精灵"威力最高的 4 个技能"里挑，并保证至少 1 个变化技能
（变化技能是 buff 策略的前提）。

用法
----
    python scripts\\fill_team.py            # 预览
    python scripts\\fill_team.py --apply    # 写入
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402
import team_db  # noqa: E402

# 实盘观察到的我方阵容（按出现频率排）
TEAM = ["恶魔狼", "音速犬", "刨雪鹅", "银月狼王", "冥古龙", "卡瓦重"]


def pick_moves(db, name, n_attack=3):
    """选 4 个技能：n_attack 个高威力攻击 + 1 个变化技能。"""
    row = db.execute(
        "SELECT id FROM species WHERE name=? "
        "ORDER BY implemented DESC, total DESC LIMIT 1", (name,)).fetchone()
    if not row:
        return None, []
    sid = row[0]
    attacks = []
    for r in db.execute(
            "SELECT s.id,s.name,s.power,s.cost FROM species_skills ss "
            "JOIN skills s ON s.id=ss.skill_id "
            "WHERE ss.species_id=? AND s.power>0 "
            "ORDER BY s.power DESC LIMIT 8", (sid,)):
        attacks.append(r)
    dashes = []
    for r in db.execute(
            "SELECT s.id,s.name,s.power,s.cost FROM species_skills ss "
            "JOIN skills s ON s.id=ss.skill_id "
            "WHERE ss.species_id=? AND (s.power IS NULL OR s.power<=0) "
            "AND s.cost<=2 ORDER BY s.cost LIMIT 4", (sid,)):
        dashes.append(r)
    picks = [a[0] for a in attacks[:n_attack]]
    if dashes:
        picks.append(dashes[0][0])
    return sid, picks


def main():
    apply = "--apply" in sys.argv
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row

    plan = []
    for i, name in enumerate(TEAM, start=1):
        sid, moves = pick_moves(db, name)
        if not sid:
            print("  !! 库里没有 %s" % name)
            continue
        detail = []
        for m in moves:
            r = db.execute("SELECT name,power,cost FROM skills WHERE id=?",
                           (m,)).fetchone()
            if r:
                detail.append("%s(p%s/c%s)" % (r["name"], r["power"], r["cost"]))
        plan.append((i, name, sid, moves, detail))

    print("=== 队伍计划 ===")
    for slot, name, sid, moves, detail in plan:
        print("  slot%d %-10s id=%-6d moves=%s" % (slot, name, sid, detail))

    if not apply:
        print("\n（预览，加 --apply 执行）")
        db.close()
        return 0

    for slot, name, sid, moves, _d in plan:
        team_db.save_pet(slot, {"species_id": sid, "moves": moves,
                                "level": 60})
        print("  写入 slot%d %s" % (slot, name))

    print("\n=== 结果 ===")
    for p in team_db.dump_team():
        print("  slot%s %-10s moves=%s"
              % (p.get("slot"), p.get("name"), p.get("moves")))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
