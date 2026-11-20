# -*- coding: utf-8 -*-
"""Is the number printed on a skill card DAMAGE or EFFECTIVE POWER?

This decides whether the decision layer may treat the card number as HP damage.
The 隼鳞 evidence says "power" (140 x 1.25 STAB = 175, and the card read 175
against two different defenders).  The 吞噬/火焰切割/蝙蝠 evidence says
"damage" (they need a k of ~1.897 to close).  Both cannot be true.

Method: mine only CLEAN turn transitions - same foe, same turn index advancing
by exactly 1, and exactly one skill action in between - then compare the card
number against the HP points actually removed.
"""
from __future__ import annotations

import io
import json
import os
import sys
from collections import defaultdict

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
import sqlite3  # noqa: E402
import paths     # noqa: E402


def foe_hp_band(name):
    import statmodel as S
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    try:
        r = db.execute("SELECT hp FROM species WHERE name=? "
                       "ORDER BY implemented DESC, total DESC LIMIT 1",
                       (name,)).fetchone()
        if not r or not r["hp"]:
            return None
        return (S.calc_real_stat(r["hp"], 60, 1.0, True),
                S.calc_real_stat(r["hp"], 60, 1.2, True))
    finally:
        db.close()


def clean_transitions():
    """(foe, card_value, hp_points_removed) for unambiguous single-skill turns."""
    rows = [json.loads(l) for l in io.open(
        os.path.join(ROOT, "logs", "turns.jsonl"), encoding="utf-8") if l.strip()]
    out = []
    for a, b in zip(rows, rows[1:]):
        if a.get("foe_pet") != b.get("foe_pet"):
            continue
        if a.get("foe_pet") is None:
            continue
        ta, tb = a.get("turn"), b.get("turn")
        if ta is None or tb is None or tb != ta + 1:
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
        out.append((a["foe_pet"], ch[0]["v"], pb, pa))
    return out


def main():
    tr = clean_transitions()
    print("clean single-skill turn transitions:", len(tr))

    by_foe = defaultdict(list)
    for foe, card, pb, pa in tr:
        by_foe[foe].append((card, pb, pa))

    print("\nfoe                  n   card   hp%%drop   card/hp_band   verdict")
    checked = agree_power = agree_damage = 0
    for foe, rows in sorted(by_foe.items(), key=lambda kv: -len(kv[1])):
        band = foe_hp_band(foe)
        if not band or len(rows) < 3:
            continue
        lo, hi = band
        for card, pb, pa in rows[:5]:
            drop_pct = pb - pa
            # if card were damage: drop_pct should equal 100*card/hp
            dmg_drop_lo = 100.0 * card / hi
            dmg_drop_hi = 100.0 * card / lo
            # if card were power: damage = card * k, k~1.897
            pw_drop_lo = 100.0 * card * 1.897 / hi
            pw_drop_hi = 100.0 * card * 1.897 / lo
            v = "?"
            if dmg_drop_lo - 4 <= drop_pct <= dmg_drop_hi + 4:
                v = "DAMAGE"
                agree_damage += 1
            elif pw_drop_lo - 4 <= drop_pct <= pw_drop_hi + 4:
                v = "POWER"
                agree_power += 1
            checked += 1
            print("  %-16s %4d %5.1f  %6.1f   dmg %.0f~%.0f pw %.0f~%.0f  %s"
                  % (foe, card, drop_pct, card,
                     dmg_drop_lo, dmg_drop_hi,
                     pw_drop_lo, pw_drop_hi, v))

    print("\nchecked %d samples: DAMAGE %d, POWER %d"
          % (checked, agree_damage, agree_power))


if __name__ == "__main__":
    main()
