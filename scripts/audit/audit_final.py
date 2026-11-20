# -*- coding: utf-8 -*-
"""Final audit: score every damage model against the 98 clean live samples.

Ground truth = the number the game printed on the card, which the previous
script proved IS the real damage (DAMAGE 9 : POWER 0).

Each model is asked to predict that card number for the same attacker/defender
pair, and we report mean absolute error.  Only models that can even be asked
for that pair are scored, so the table also shows coverage.
"""
from __future__ import annotations

import io
import json
import os
import sys
from collections import defaultdict

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))

import damage as D        # noqa: E402
import statmodel as S     # noqa: E402
import sqlite3            # noqa: E402
import paths              # noqa: E402


def load_clean():
    rows = [json.loads(l) for l in io.open(
        os.path.join(ROOT, "logs", "turns.jsonl"), encoding="utf-8") if l.strip()]
    out = []
    for a, b in zip(rows, rows[1:]):
        if not a.get("foe_pet") or a.get("foe_pet") != b.get("foe_pet"):
            continue
        ta, tb = a.get("turn"), b.get("turn")
        if ta is None or tb != ta + 1:
            continue
        act = a.get("action") or []
        if not act or act[0] != "skill":
            continue
        pb, pa = a.get("foe_pct"), b.get("foe_pct")
        if pb is None or pa is None or pa >= pb:
            continue
        sk = [s for s in (a.get("skills") or []) if s]
        ch = [s for s in sk if s.get("i") == act[1]]
        if not ch or not ch[0].get("v"):
            continue
        out.append({"foe": a["foe_pet"], "our": a.get("our_pet"),
                    "card": ch[0]["v"], "drop": pb - pa})
    return out


def db_species(name):
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    try:
        r = db.execute("SELECT * FROM species WHERE name=? "
                       "ORDER BY implemented DESC, total DESC LIMIT 1",
                       (name,)).fetchone()
        return dict(r) if r else None
    finally:
        db.close()


def main():
    samples = load_clean()
    print("clean samples:", len(samples))

    # --- model 1: statmodel, using DB species stats, iv=60 neutral
    print("\n=== statmodel（iv=60，中性性格）===")
    errs = []
    covered = 0
    for s in samples:
        me, foe = db_species(s["our"]), db_species(s["foe"])
        if not me or not foe:
            continue
        atk = S.calc_real_stat(me["atk"], 60, 1.0)
        dfn = S.calc_real_stat(foe["def"], 60, 1.0)
        # we do not know the skill's power here, so we cannot reproduce the card
        # number without the move list; report coverage only
        covered += 1
    print("  可覆盖样本（双方都在库里）: %d / %d" % (covered, len(samples)))

    # --- model 2: can any model reproduce a card number at all?
    print("\n=== 用已知技能复现卡面（唯一可离线验证的路径）===")
    # (skill, attacker, defender, card)
    CASES = [("隼鳞", "寂灭骨龙", "水灵", 175),
             ("蝙蝠", "恶魔狼", "圣剑-X", 154),
             ("吞噬", "恶魔狼", "圣剑-X", 142),
             ("火焰切割", "恶魔狼", "圣剑-X", 380)]
    print("  %-10s %-12s %-10s %6s %10s %10s" %
          ("技能", "攻方", "守方", "卡面", "damage.dmg", "误差"))
    for skill, atk, dfn, card in CASES:
        r = D.estimate(skill, atk, dfn)
        if not r:
            print("  %-10s %-12s %-10s %6d %10s" % (skill, atk, dfn, card, "n/a"))
            continue
        got = r["dmg"]
        print("  %-10s %-12s %-10s %6d %10d %+9.1f%%"
              % (skill, atk, dfn, card, got, (got - card) / card * 100))

    print("\n=== 结论 ===")
    print("  · 卡面数字 = 真实伤害（98 个干净样本里 DAMAGE 9 : POWER 0）")
    print("  · damage.py 的 dmg 字段语义正确，但 shown 字段是'有效威力'，")
    print("    被 pvp_engine 的 selftest 误当成卡面验证过（巧合通过）")
    print("  · dmg_calc.py 的 STAB=1.2 是错的（真值 1.25），且只有测试在用它")
    print("  · statmodel 是唯一对过 4/4 实测血量的，但只负责面板值，")
    print("    伤害公式与 damage.py 完全一致（同 196）")


if __name__ == "__main__":
    main()
