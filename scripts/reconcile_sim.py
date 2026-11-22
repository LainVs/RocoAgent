# -*- coding: utf-8 -*-
"""S18 实战对账 v2：模拟器伤害 vs 游戏技能卡预览值。

数据源：logs/turns.jsonl 里 skill_source=team_db 的回合：
  - skills[i].v = 游戏显示的技能卡伤害预览（含克制/STAB）
  - 槽位 i → my_team.moves[i-1] → 技能 id
  - 我方面板：my_team 的实配 atk
  - 守方：turns 里的 foe_pet → species 防御（等级 60 近似）

用法：python scripts/reconcile_sim.py [--limit N]
"""
import io
import json
import re
import sys
import types
from collections import defaultdict

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

import paths  # noqa: E402
import sqlite3  # noqa: E402
import simulator as SIM  # noqa: E402


def main():
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    dex = SIM.Dex()

    # my_team 面板缓存
    panels = {}
    for r in db.execute("SELECT * FROM my_team"):
        panels[r["species_id"]] = r

    recs = []
    for line in io.open("logs/turns.jsonl", encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            pass

    rows = []
    skip = defaultdict(int)
    for r in recs:
        src = r.get("skill_source")
        if not src or not isinstance(src, list) or "team_db" not in src:
            continue
        act = r.get("action") or []
        if act[0] != "skill":
            continue
        slot = act[1] if len(act) > 1 else None
        if not slot or slot < 1 or slot > len(src):
            continue
        our_pet = r.get("our_pet")
        sp = dex.species(our_pet) if our_pet else None
        if not sp or sp["id"] not in panels:
            skip["no-panel"] += 1
            continue
        panel = panels[sp["id"]]
        moves = panel["moves"] or []
        if isinstance(moves, str):
            try:
                moves = json.loads(moves)
            except Exception:
                moves = []
        if not isinstance(moves, list) or len(moves) < slot:
            skip["no-moves"] += 1
            continue
        skill_id = moves[slot - 1]
        mv = dex.resolve_skill(skill_id)
        if not mv or not (mv.get("power") or 0):
            skip["no-skill"] += 1
            continue
        # 卡牌预览值
        card_v = None
        for sk in (r.get("skills") or []):
            if sk.get("i") == slot:
                try:
                    card_v = int(sk.get("v"))
                except (TypeError, ValueError):
                    card_v = None
        if not card_v:
            skip["no-card"] += 1
            continue
        # 守方防御（种族，等级 60 近似）
        foe_pet = r.get("foe_pet")
        foe_sp = dex.species(foe_pet) if foe_pet else None
        if not foe_sp:
            skip["no-foe"] += 1
            continue
        frow = db.execute("SELECT def FROM species WHERE id=?",
                          (foe_sp["id"],)).fetchone()
        if not frow:
            skip["no-foe-def"] += 1
            continue
        rows.append({
            "mid": r.get("mid"), "turn": r.get("turn"),
            "our_pet": our_pet, "foe_pet": foe_pet,
            "skill": mv["name"], "power": mv["power"],
            "type": mv.get("type"), "card": card_v,
            "atk": panel["atk"], "dfn": frow["def"],
        })

    print("reconcilable:", len(rows), " skip:", dict(skip))
    if limit:
        rows = rows[:limit]

    errs = []
    for r in rows:
        # 用 Battle.calc_damage 本体（自带 STAB/克制/dt 路径）
        _atk = types.SimpleNamespace(
            atk=r["atk"], spa=r["atk"], dfn=r["dfn"], spd=r["dfn"],
            types=(dex.species(r["our_pet"]) or {}).get("types") or [],
            hp=999, max_hp=999, energy=10, alive=True,
            status={}, atk_buff=0.0, def_buff=0.0,
            trait_tmp_atk=0.0, trait_tmp_def=0.0,
            taken_mult=1.0, nrc_power_mult=1.0, nrc_power_bonus=0,
            name=r["our_pet"], trait="", nature=None, invest=[],
            _battle=None, _side="a")
        _dfd = types.SimpleNamespace(
            atk=r["dfn"], spa=r["dfn"], dfn=r["dfn"], spd=r["dfn"],
            types=(dex.species(r["foe_pet"]) or {}).get("types") or [],
            hp=999, max_hp=999, energy=10, alive=True,
            status={}, atk_buff=0.0, def_buff=0.0,
            trait_tmp_atk=0.0, trait_tmp_def=0.0,
            taken_mult=1.0, nrc_power_mult=1.0, nrc_power_bonus=0,
            name=r["foe_pet"], trait="", nature=None, invest=[])
        mv = {"id": 0, "name": r["skill"], "power": r["power"],
              "cost": 1, "type": r["type"], "damage_type": 2}
        try:
            sim = SIM.calc_damage(_atk, _dfd, mv)
        except Exception:
            # 回退：手写公式（base×STAB×克制）
            sim = r["power"] * r["atk"] / max(1.0, r["dfn"]) * 0.5
            if r["type"] in (_atk.types or []):
                sim *= 1.25
            try:
                import statmodel
                sim *= statmodel.type_effect(r["type"], _dfd.types)
            except Exception:
                pass
        errs.append((r, sim, r["card"]))

    if not errs:
        print("no rows computed")
        return
    # 比值分布：card / sim_base（不含 STAB/克制）——找系统性因子
    ratios = defaultdict(list)
    for r, e, c in errs:
        base = r["power"] * r["atk"] / max(1.0, r["dfn"]) * 0.5
        if base > 0:
            ratios["all"].append(c / base)
            ratios["skill:%s" % r["skill"]].append(c / base)
    allr = sorted(ratios["all"])
    n = len(allr)
    print("ratio card/base: p10=%.2f p25=%.2f p50=%.2f p75=%.2f p90=%.2f"
          % (allr[n // 10], allr[n // 4], allr[n // 2], allr[3 * n // 4],
             allr[9 * n // 10]))
    # 1.25/1.5/2.0 的倍数聚集？
    mult_cnt = defaultdict(int)
    for x in allr:
        for m in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0):
            if abs(x - m) <= 0.08 * m:
                mult_cnt[m] += 1
                break
    print("ratio clusters:", dict(sorted(mult_cnt.items())))
    mae = sum(abs(e - c) for _, e, c in errs) / len(errs)
    rel = sum(abs(e - c) / max(1, c) for _, e, c in errs) / len(errs)
    within10 = sum(1 for _, e, c in errs if abs(e - c) <= 0.1 * max(1, c))
    within20 = sum(1 for _, e, c in errs if abs(e - c) <= 0.2 * max(1, c))
    print("computed: %d  MAE: %.1f  rel: %.1f%%" % (len(errs), mae,
                                                    100 * rel))
    print("within ±10%%: %.0f%%  ±20%%: %.0f%%"
          % (100.0 * within10 / len(errs), 100.0 * within20 / len(errs)))
    errs.sort(key=lambda x: -abs(x[1] - x[2]))
    for r, e, c in errs[:6]:
        print("  worst: %s/%s %s->%s %s(p%d %s): sim %.0f card %d"
              % (r["mid"], r["turn"], r["our_pet"], r["foe_pet"],
                 r["skill"], r["power"], r["type"], e, c))


if __name__ == "__main__":
    main()
