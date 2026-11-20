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
import paths

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, str(paths.TOOLS))

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

    # ---- 必死保护：这回合一定会死，且换人/治疗能活 -------------------
    #
    # 实测（2026-11-21 03:20 T27）：我血 66/366（18%），对面 367，
    # 承受 128 伤害必死，策略仍然选了攻击（score 0.0914）。
    # 这是致命缺陷：明知会死还打，等于白送一只精灵。
    #
    # 判据：承受伤害 >= 当前血 -> 这回合会死。
    #
    # 速度相同也要算"可能后手"：实测音速犬和火羽速度都是 225，
    # we_outspeed 判成 True，于是"必死保护"没触发，机器人拿 66 血
    # 硬吃 128 伤害。速度相同时谁先手由游戏决定，保守起来更安全。
    dying_this_turn = inc >= my_hp
    if dying_this_turn:
        # 换人：新精灵能扛住这一击才算有效
        for slot, pet in enumerate(bench or [], start=1):
            if not pet.get("name") or pet.get("hp", 0) <= 0:
                continue
            inc_new, _src = _incoming(foe_name, pet["name"],
                                      pet.get("hp_max") or 1)
            if inc_new < pet.get("hp", 0):
                return (("switch", slot),
                        "dying this turn (%d hp vs %d incoming) -> "
                        "switch to %s which survives"
                        % (my_hp, inc, pet["name"]), {})
        # 没有能活的替补：优先选"能反杀"的攻击（死前换掉对面）
        for s in me.get("skills") or []:
            if s.get("kind") != "num" or not s.get("lit", True):
                continue
            if (s.get("dmg") or 0) >= foe_hp:
                return (("skill", s["i"]),
                        "dying but can trade: %d dmg kills foe" % s["dmg"], {})

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
    # 实测结论（模拟器自对弈，2026-11-21）：
    #   buff 当"得分为 0 + 常量奖励"的候选参与打分 -> 胜率 50.7%
    #   buff 无条件优先（前两回合、血够、还没叠过） -> 胜率 66.7%
    # 差 16 个百分点。原因是收益结构不同：
    #   攻击 = 这一回合的一次输出；buff = 后续每一回合的 +20%。
    #
    # 所以这里改成"优先选择"：
    #   前两回合 + 血够 + 能放 + 打不死对面 -> 直接叠
    #   （能一击必杀时不叠，直接赢更好）
    # 判断"这回合能不能一击必杀"（决定要不要先叠 buff）
    # 注意：不能用字符串匹配 "kill"（reason 里可能碰巧含这个词），
    # 要真的比较伤害和对面血量。
    foe_killable_now = False
    for s in me.get("skills") or []:
        if s.get("kind") != "num" or not s.get("lit", True):
            continue
        d = s.get("dmg") or 0
        if d >= foe_hp:
            foe_killable_now = True
            break
    for s in me.get("skills") or []:
        if s.get("kind") != "dash" or not s.get("lit", True):
            continue
        cost = s.get("cost") or 0
        if cost > energy:
            continue
        survives = inc < my_hp * (1.0 - w.get("buff_survive_floor", 0.25))
        if not survives:
            cands.append((-w.get("buff_no_survive_penalty", 0.25),
                          ("skill", s["i"]),
                          "status %d: would likely die" % s["i"]))
            continue
        # 优先叠：给一个远高于任何攻击的分数（但能杀时不叠）
        #
        # 只能叠一次：policy_v2.decide 是纯函数，没有内部状态，
        # 所以"是否已经叠过"由调用方通过 state["buffed"] 传入。
        # 实测（2026-11-21 04:57）：不加这个判断会在 T1/T2 连续叠两次，
        # 第二次纯属浪费回合（buff 上限 3 层但第二层收益远低于一次攻击）。
        early = (state.get("turn", 1) or 1) <= 2
        already = bool(state.get("buffed"))
        if early and not already and not foe_killable_now:
            cands.append((9e9, ("skill", s["i"]),
                          "status %d: buff first (early turn, safe)" % s["i"]))
        else:
            cands.append((0.0, ("skill", s["i"]),
                          "status %d: buff (late/safe)" % s["i"]))

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
