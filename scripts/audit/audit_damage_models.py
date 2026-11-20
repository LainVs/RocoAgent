# -*- coding: utf-8 -*-
"""Which damage model actually matches reality?

Ground truth: 779 (card number, actual HP% drop) pairs mined from
logs/turns.jsonl.  The card number is what the game printed; the HP drop is
what actually happened next turn.  A model is only trustworthy if its output
tracks the card number, because the card number IS the game's own damage
figure for that matchup.

We compare the four candidate models on the same inputs.
"""
from __future__ import annotations

import io
import json
import os
import sys

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))

import dmg_calc as DC      # noqa: E402
import sim                 # noqa: E402
import statmodel as S      # noqa: E402


def load_pairs():
    """(card_shown, foe_hp_pct_before, foe_hp_pct_after, foe_pet)"""
    L = [json.loads(l) for l in io.open(
        os.path.join(ROOT, "logs", "turns.jsonl"), encoding="utf-8") if l.strip()]
    out = []
    prev = None
    for r in L:
        if prev:
            same_foe = prev.get("foe_pet") == r.get("foe_pet")
            act = prev.get("action") or []
            if same_foe and act and act[0] == "skill":
                before, after = prev.get("foe_pct"), r.get("foe_pct")
                if before is not None and after is not None and after < before - 0.5:
                    sk = [s for s in (prev.get("skills") or []) if s]
                    chosen = [s for s in sk if s.get("i") == act[1]]
                    if chosen and chosen[0].get("v"):
                        out.append((chosen[0]["v"], before, after,
                                    prev.get("foe_pet")))
        prev = r
    return out


def main():
    pairs = load_pairs()
    print("ground-truth pairs:", len(pairs))

    # The card number is the game's own figure.  If the HP drop were exactly
    # card/foe_max_hp, then card = pct/100 * foe_max_hp.  We do not know
    # foe_max_hp, but for a FIXED foe we can solve it and check consistency.
    print("\n--- 1. is the card number equal to the real damage? ---")
    from collections import defaultdict
    by_foe = defaultdict(list)
    for card, before, after, foe in pairs:
        by_foe[foe].append((card, before - after))
    checked = 0
    for foe, rows in sorted(by_foe.items(), key=lambda kv: -len(kv[1]))[:6]:
        if len(rows) < 4:
            continue
        # implied max HP for each row: card / (drop/100)
        implied = [card / (drop / 100.0) for card, drop in rows if drop > 1]
        if not implied:
            continue
        lo, hi = min(implied), max(implied)
        print("  %-14s n=%-3d  implied foe_max_hp %.0f ~ %.0f  (spread %.0f%%)"
              % (foe, len(rows), lo, hi, (hi - lo) / lo * 100))
        checked += 1
    print("  -> a tight spread means 'card number == real damage'")

    print("\n--- 2. STAB: which value reproduces the known cards? ---")
    # 隼鳞 龙 140, 本系, 卡面 175
    print("  隼鳞 140 本系 -> 卡面 175:  1.25 -> %.1f   1.20 -> %.1f"
          % (140 * 1.25, 140 * 1.2))
    # 蝙蝠 恶 65, 本系, 卡面 154, k=1.8954
    print("  蝙蝠  65 本系 -> 卡面 154:  1.25 -> %.1f   1.20 -> %.1f"
          % (65 * 1.8954 * 1.25, 65 * 1.8954 * 1.2))

    print("\n--- 3. dmg_calc (STAB=%.2f, k=%.3f) ---" % (DC.STAB, DC.DEFAULT_K))
    bad = 0
    for card, power, stab, mult in ((175, 140, 1.25, 1.0),
                                    (154, 65, 1.25, 1.0),
                                    (142, 150, 1.0, 0.5),
                                    (380, 100, 1.0, 2.0)):
        got = DC.predict(power, DC.DEFAULT_K, stab, mult)
        err = (got - card) / float(card) * 100
        ok = abs(err) < 1.0
        bad += 0 if ok else 1
        print("  卡面 %3d 预测 %3d  误差 %+6.1f%%  %s"
              % (card, got, err, "OK" if ok else "**FAIL**"))
    print("  failures:", bad)

    print("\n--- 4. sim (k solved from the same cards) ---")
    known = [("隼鳞", 175, 140, 1.25, 1.0),
             ("蝙蝠", 154, 65, 1.25, 1.0),
             ("吞噬", 142, 150, 1.0, 0.5),
             ("火焰切割", 380, 100, 1.0, 2.0)]
    mean, spread, ks = sim.fit(known)
    print("  拟合 k = %.4f  相对离散 %.2f%%" % (mean, spread * 100))
    for name, k in ks:
        print("    %-8s k = %.4f" % (name, k))
    for name, card, power, stab, mult in known:
        pred = sim.damage(power, mean, stab, mult)
        print("  卡面 %3d 预测 %3d  误差 %+6.1f%%" % (card, pred,
                                                     (pred - card) / card * 100))


if __name__ == "__main__":
    main()
