# -*- coding: utf-8 -*-
"""Decide what the battle card number actually means.

Three competing readings:
  A. card = final damage            (dmg_calc.py assumes this)
  B. card = effective power         (pvp_engine's "shown")
  C. card = damage but scaled by an unknown k

Test: for each (foe, card, HP% drop) pair, compare
    card / foe_max_hp   vs   observed drop
where foe_max_hp comes from the pet DB.  Reading A predicts equality.
"""
from __future__ import annotations

import io
import json
import os
import sys

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
import sqlite3  # noqa: E402
import paths     # noqa: E402


def foe_max_hp(name):
    """Max HP for a foe, using the verified stat model (PvP = iv 60)."""
    import statmodel as S
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    try:
        r = db.execute("SELECT hp FROM species WHERE name=? "
                       "ORDER BY implemented DESC, total DESC LIMIT 1",
                       (name,)).fetchone()
        if not r or not r["hp"]:
            return None
        # PvP sets are iv=60 with an unknown nature; report the neutral value
        # and the +HP nature value as a band.
        lo = S.calc_real_stat(r["hp"], 60, 1.0, True)
        hi = S.calc_real_stat(r["hp"], 60, 1.2, True)
        return lo, hi
    finally:
        db.close()


def load_pairs():
    L = [json.loads(l) for l in io.open(
        os.path.join(ROOT, "logs", "turns.jsonl"), encoding="utf-8") if l.strip()]
    out = []
    prev = None
    for r in L:
        if prev:
            act = prev.get("action") or []
            if (prev.get("foe_pet") == r.get("foe_pet") and act
                    and act[0] == "skill"):
                b, a = prev.get("foe_pct"), r.get("foe_pct")
                if b is not None and a is not None and a < b - 0.5:
                    sk = [s for s in (prev.get("skills") or []) if s]
                    ch = [s for s in sk if s.get("i") == act[1]]
                    if ch and ch[0].get("v"):
                        out.append((prev.get("foe_pet"), ch[0]["v"], b, a))
        prev = r
    return out


def main():
    pairs = load_pairs()
    print("pairs:", len(pairs))

    print("\n=== reading A: card == real damage ===")
    print("For each foe with a known max HP, compare card/foe_max_hp to the"
          " observed drop.")
    from collections import defaultdict
    by_foe = defaultdict(list)
    for foe, card, b, a in pairs:
        if foe:
            by_foe[foe].append((card, b - a))

    ok = tot = 0
    for foe, rows in sorted(by_foe.items(), key=lambda kv: -len(kv[1])):
        if len(rows) < 5:
            continue
        hp = foe_max_hp(foe)
        if not hp:
            continue
        lo, hi = hp
        print("\n  %-14s n=%d  foe_max_hp %d~%d" % (foe, len(rows), lo, hi))
        for card, drop in rows[:6]:
            pred_lo = 100.0 * card / hi
            pred_hi = 100.0 * card / lo
            hit = pred_lo - 3 <= drop <= pred_hi + 3
            ok += 1 if hit else 0
            tot += 1
            print("     卡面 %4d  实测掉 %5.1f%%   预测 %5.1f~%5.1f%%  %s"
                  % (card, drop, pred_lo, pred_hi, "OK" if hit else "**no**"))
    if tot:
        print("\n  reading A holds on %d / %d = %.0f%%" % (ok, tot, 100.0 * ok / tot))


if __name__ == "__main__":
    main()
