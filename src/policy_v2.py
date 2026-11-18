"""policy_v2.py - expected-value action selection for PvP.

Two references shaped this, both S5:

  * rocom-helper src/analysis/tactical_engine.py + tactical/scoring.py
    (1-ply expectation over the opponent's likely actions, linear score)
  * roco-claude data/mechanics/decision_value.md
    (net gain = resource advantage - loss; speed order decides everything;
     turns-to-KO = ceil(hp / damage))

What is deliberately different:
  * buffs are gated on survivability. rocom-helper just adds a flat bonus for
    "stat_up" tags, which is why a bot built that way will happily spend a turn
    buffing into a lethal hit. Here a non-damage skill is scored *after*
    checking that we live through the turn.
  * switch value is explicit: the incoming pet's own damage is compared with
    the current one's, so the bot rotates because it is better, not on a timer.

State is plain dicts so it can come from vision today and from the protocol
sniffer later without touching this file:

    me   : {name, hp, hp_max, energy, skills:[{i, dmg, cost, kind, lit}]}
    foe  : {name, hp, hp_max}
    bench: [{name, hp, hp_max, energy}]

usage:
    from policy_v2 import decide
    action, reason, detail = decide(state)
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, r"D:\Tools")

import damage as D
import pvp_engine as E

WEIGHTS_PATH = os.path.join(os.path.dirname(_HERE), "config", "policy_weights.json")
_W = None


def weights():
    global _W
    if _W is None:
        try:
            with open(WEIGHTS_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            _W = {k: float(v) for k, v in raw.items() if not k.startswith("_")}
        except Exception:
            _W = {}
    return _W


def _speed(name):
    p = E.pet(name)
    if not p:
        return 0
    try:
        return D.calc_stat(int(p["stats"]["speed"]))
    except Exception:
        return 0


def _type_mult(atk_name, def_name):
    """Best damage multiplier the attacker's own type applies to the defender."""
    p = E.pet(atk_name)
    if not p:
        return 1.0
    best = 1.0
    for t in p.get("attributes_list") or []:
        m = E.taken_multiplier(def_name, t)
        if m and m > best:
            best = m
    return best


def best_damage_from(name, foe_name):
    """Our best theoretical hit when the cards are not readable (e.g. on switch)."""
    best = 0
    for skill in E.move_pool(name):
        try:
            r = D.estimate(skill, name, foe_name)
        except Exception:
            continue
        if r and r.get("dmg"):
            best = max(best, int(r["dmg"]))
    return best


def _incoming(foe_name, my_name, my_max):
    """Damage the opponent can land on us this turn.

    If the opponent is unidentified (no OCR yet, or a pet missing from the
    dataset) worst_incoming() returns nothing - and an EV model that reads that
    as "zero damage taken" will happily stand still and die. Fall back to a
    conservative fraction of our own max HP instead, and let the caller record
    that the number is assumed rather than observed.
    """
    rows = E.worst_incoming(foe_name, my_name, top=1)
    if rows:
        return rows[0]["dmg"], "model"
    pct = weights().get("unknown_incoming_pct", 0.35)
    return int(max(1, round(my_max * pct))), "assumed"


def _outcome(our_dmg, our_kills, opp_dmg, they_kill_us, after_hp, max_hp,
             energy_after, count_delta, matchup):
    w = weights()
    s = (
        w.get("w_damage_dealt", 0.25) * _c01(our_dmg)
        + w.get("w_ko", 0.30) * (1.0 if our_kills else 0.0)
        - w.get("w_damage_taken", 0.15) * _c01(opp_dmg)
        - w.get("w_opp_ko", 0.20) * (1.0 if they_kill_us else 0.0)
        + w.get("w_type_matchup", 0.05) * _c01(matchup / 4.0)
        + w.get("w_energy", 0.03) * _c01(energy_after / 10.0)
        + w.get("w_pet_count", 0.02) * count_delta * 0.15
    )
    return s


def _c01(x):
    return max(0.0, min(1.0, float(x)))


def decide(state):
    """Return (action, reason, detail). action is ("skill", n) | ("energy",) | ("switch", n)."""
    w = weights()
    me = state["me"]
    foe = state["foe"]
    bench = state.get("bench") or []

    my_name, foe_name = me["name"], foe["name"]
    my_hp = me["hp"]
    my_max = me["hp_max"]
    foe_hp = foe["hp"]
    foe_max = foe["hp_max"]
    energy = me.get("energy", 0)

    inc, inc_src = _incoming(foe_name, my_name, my_max)
    we_outspeed = _speed(my_name) >= _speed(foe_name)
    matchup = _type_mult(my_name, foe_name)

    cands = []

    # ---- damaging skills -------------------------------------------------
    for s in me.get("skills") or []:
        if s.get("kind") != "num" or not s.get("lit", True):
            continue
        dmg = s.get("dmg")
        if not dmg:
            continue
        cost = s.get("cost") or 0
        energy_after = max(0, energy - cost)
        # Whoever is faster acts first; that decides whether a kill lands
        # before the retaliation.
        if we_outspeed:
            kills = dmg >= foe_hp
            taken = 0 if kills else inc
        else:
            dies_first = inc >= my_hp
            kills = (not dies_first) and dmg >= foe_hp
            taken = inc
        score = _outcome(dmg / max(1, foe_max), kills,
                         taken / max(1, my_max), (not we_outspeed) and inc >= my_hp,
                         my_hp - taken, my_max, energy_after, 0, matchup)
        cands.append((score, ("skill", s["i"]),
                      "skill %d: %d dmg, %d taken, %s" % (
                          s["i"], dmg, taken,
                          "kill" if kills else ("outspeed" if we_outspeed else "slower"))))

    # ---- non-damaging skills (buffs / heals / shields) -------------------
    # Scored through the same outcome function as attacks: they deal nothing and
    # we still eat the hit, so they cannot quietly look "safe" while we die.
    for s in me.get("skills") or []:
        if s.get("kind") == "dash" and s.get("lit", True) and s.get("i") not in \
                [c[1][1] for c in cands if c[1][0] == "skill"]:
            cost = s.get("cost") or 0
            survives = inc < my_hp * (1.0 - w.get("buff_survive_floor", 0.35))
            score = _outcome(0.0, False, inc / max(1, my_max), inc >= my_hp,
                             my_hp - inc, my_max, max(0, energy - cost), 0, matchup)
            score += w.get("buff_bonus", 0.06) if survives else \
                -w.get("buff_no_survive_penalty", 0.25)
            cands.append((score, ("skill", s["i"]),
                          "status %d: %s (energy cost %d)" % (
                              s["i"], "safe to buff" if survives else "would likely die", cost)))

    # ---- charge ----------------------------------------------------------
    # Charging is not free: we still eat the incoming hit, and if that hit is
    # lethal it kills us just as surely as standing there. Scoring it as a
    # risk-free option made a nearly-dead bot press F while it died.
    streak = state.get("charge_streak", 0)
    charge_outcome = _outcome(0.0, False,
                              inc / max(1, my_max), inc >= my_hp,
                              my_hp - inc, my_max,
                              min(10, energy + 5), 0, matchup)
    charge_outcome += w.get("charge_score", 0.06) - \
        w.get("charge_interrupt_penalty", 0.10) * _c01(streak / 2.0)
    cands.append((charge_outcome, ("energy",),
                  "charge F (streak %d, +5 energy)" % streak))

    # ---- switches --------------------------------------------------------
    for slot, pet in enumerate(bench or [], start=1):
        if not pet.get("name") or pet.get("hp", 0) <= 0:
            continue
        dmg = best_damage_from(pet["name"], foe_name)
        inc_new, _src = _incoming(foe_name, pet["name"], pet.get("hp_max") or 1)
        # The switch itself eats the turn, so the incoming hit lands unopposed.
        survives = inc_new < pet.get("hp", 0)
        score = _outcome(dmg / max(1, foe_max), dmg >= foe_hp,
                         inc_new / max(1, pet.get("hp_max", 1)), not survives,
                         pet.get("hp", 0) - inc_new, pet.get("hp", 1),
                         pet.get("energy", 10), 0,
                         _type_mult(pet["name"], foe_name))
        score -= w.get("switch_cost", 0.12)
        cands.append((score, ("switch", slot),
                      "switch %d -> %s: %d dmg vs %d taken%s" % (
                          slot, pet["name"], dmg, inc_new,
                          "" if survives else " (cannot survive)")))

    if not cands:
        return ("energy",), "no readable options", {}

    cands.sort(key=lambda c: c[0], reverse=True)
    best_score, action, why = cands[0]
    detail = {
        "score": round(best_score, 4),
        "incoming": inc,
        "incoming_source": inc_src,
        "outspeed": we_outspeed,
        "matchup": matchup,
        "runner_up": (round(cands[1][0], 4), cands[1][1]) if len(cands) > 1 else None,
        "all": [(round(s, 4), a) for s, a, _ in cands[:6]],
    }
    return action, why, detail
