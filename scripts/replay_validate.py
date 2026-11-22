# -*- coding: utf-8 -*-
"""S18b 战斗状态回放对账：1v1 无换人场，逐回合对比。

回放 = Battle 用我方 my_team 实配 + 对方 species 缺省（等级 60），
逐回合灌入 recorded 动作（我方）/ 聚能（对方，未知时缺省），
对比每回合 foe_hp 变化（模拟）vs foe_pct 变化（实盘）。
"""
import io
import json
import sys
from collections import defaultdict

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

import paths  # noqa: E402
import sqlite3  # noqa: E402
import simulator as SIM  # noqa: E402
import statmodel as S  # noqa: E402


def load_1v1():
    recs = []
    for line in io.open("logs/turns.jsonl", encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
    mids = defaultdict(list)
    for r in recs:
        mids[r.get("mid")].append(r)
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    dex = SIM.Dex()
    team = {r["species_id"] for r in db.execute(
        "SELECT species_id FROM my_team")}
    for mid, rs in mids.items():
        rs = [r for r in rs if r.get("our_pet") and r.get("foe_pet")]
        if len(rs) < 3:
            continue
        if len({r["our_pet"] for r in rs}) > 1:
            continue
        if len({r["foe_pet"] for r in rs}) > 1:
            continue
        sp = dex.species(rs[0]["our_pet"])
        if not sp or sp["id"] not in team:
            continue
        if any(r.get("foe_pct") is None for r in rs):
            continue
        return mid, rs
    return None, None


def main():
    mid, rs = load_1v1()
    if not mid:
        print("no 1v1 match found")
        return 1
    print("replay: %s (%d turns) %s vs %s"
          % (mid, len(rs), rs[0]["our_pet"], rs[0]["foe_pet"]))

    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    dex = SIM.Dex()

    # 我方队伍：my_team 全量（回放里换人也可能发生）
    ours = []
    for r in db.execute("SELECT * FROM my_team ORDER BY slot"):
        sp = None
        for cand in dex.db.execute(
                "SELECT name FROM species WHERE id=?",
                (r["species_id"],)):
            sp = dex.species(cand["name"])
        if not sp:
            continue
        moves = json.loads(r["moves"]) if r["moves"] else []
        cfg = {"name": sp["name"], "iv": 60, "level": r["level"],
               "max_hp": r["max_hp"], "moves": moves}
        bl_row = db.execute("SELECT bloodline FROM my_team WHERE slot=?",
                            (r["slot"],)).fetchone()
        if bl_row and bl_row["bloodline"]:
            cfg["bloodline"] = bl_row["bloodline"]
        ours.append(cfg)
    our_names = [c["name"] for c in ours]

    foe_pet = rs[0]["foe_pet"]
    # 对方：species 缺省（等级 60，hp/atk/spe 投资近似）
    bt = SIM.Battle(dex, our_names, [foe_pet], seed=0,
                    cfg_a=ours,
                    cfg_b=[{"name": foe_pet, "iv": 60,
                            "invest": ["hp", "atk", "spe"]}])
    bt.begin_turn()

    # 对方 max_hp（species 60 级近似，hp 投资缺省）
    fsp = dex.species(foe_pet)
    b0 = fsp["base"]
    foe_max_hp = S.calc_real_stat(b0[0], 60, 1.0, is_hp=True)

    results = []
    for r in rs:
        act = r.get("action") or ["energy"]
        kind, payload = act[0], (act[1] if len(act) > 1 else None)
        if kind == "skill" and payload is not None:
            payload = max(0, min(payload, len(bt.me.moves) - 1))
        live_pct = r.get("foe_pct")
        # 模拟一回合（对方聚能占位——真实对方动作未知）
        ev = bt.step(kind if kind in ("skill", "energy", "switch")
                     else "energy", payload, "energy", None)
        # 模拟 foe 受到的伤害（事件里含对方 pet 名的负数）
        import re
        sim_dmg = 0
        for e in ev:
            m = re.search(re.escape(foe_pet) + r"\s*-\s*(\d+)", e)
            if m:
                sim_dmg = max(sim_dmg, int(m.group(1)))
        # 实盘 foe 伤害 = pct 变化（回合首的 foe_pct 已含本回合伤害？
        # turns 的 foe_pct 是回合末快照 → 与上一条记录差分）
        results.append((r.get("turn"), kind, sim_dmg, live_pct,
                        [e for e in ev if "-" in e][:2]))
        if bt.done:
            break
        bt.begin_turn()

    print("per-turn: turn kind sim_dmg live_foe_pct")
    for t, k, sd, lp, evs in results:
        print("  t%s %-6s sim=%-5s live_pct=%s" % (t, k, sd, lp))
    # 诊断：回放用的技能名 vs 实盘 why
    print("\ndiag: recorded actions + why")
    for r in rs:
        mv = None
        act = r.get("action") or []
        if act[0] == "skill" and act[1] and act[1] <= len(bt.me.moves):
            mv = bt.me.moves[act[1] - 1]["name"]
        print("  t%s act=%s -> %s | why=%s"
              % (r.get("turn"), act, mv, (r.get("why") or "")[:70]))
    print("(live_dmg = foe_pct 变化 × %.0f —— 对方面板未知，需反解)"
          % foe_max_hp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
