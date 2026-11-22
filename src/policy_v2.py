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

# 能量项的分母（把"剩余能量"归一到 0..1）。
#
# 踩过的坑（2026-11-21，两次）
# ---------------------------
# 1) 最初写死 10.0，假设 energy 是 0~10 的能量值。但实测 perceive 传进来的
#    energy 是**能量点亮数**，取值几乎总是 0~2（328 条样本：0 出现 33 次、
#    1 出现 293 次、2 出现 2 次）。用 10 作分母把信号压到接近 0。
#
# 2) 改成 2.0 之后又出问题：技能耗能是 1~6，energy=10 时
#    energy_after = 10-1 = 9 -> _c01(9/2) = 1.0
#    energy_after = 10-6 = 4 -> _c01(4/2) = 1.0
#    两者都被 clamp 到 1.0，**能耗差异完全消失**。
#    实测：cost=1 和 cost=6 的得分完全相同（0.1150 vs 0.1150）。
#
# 正确做法：分母用**能量上限**（10），这样 energy_after 在 0~10 之间
# 线性映射到 0~1，cost 的差异才真正体现出来。
ENERGY_SCALE = 10.0


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

    ★ 2026-11-22：改为概率加权承伤（pvp_engine.weighted_incoming）
    ----------------------------------------------------------------
    旧实现 worst_incoming(top=1) 对对面**整个可学技能池**取 max，
    实测高估真实承伤 1.5~3 倍（详见 docs/PLAN_POLICY_INCOMING.md 的
    12 组对位对照）—— EV 模型因此过度保守，明明能站桩输出却频繁换人。

    新实现：max(学习分布的期望, 0.75×最坏)。地板保留"最坏情况也扛得住"
    的安全语义（绝不在必死范围里站桩），常见对位的高估被拉回现实。
    source 字段进 detail，turns.jsonl 可追踪新旧差。
    """
    dmg, src = E.weighted_incoming(
        foe_name, my_name,
        blend=weights().get("incoming_blend", 0.75))
    if dmg:
        return dmg, src
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
        + w.get("w_energy", 0.03) * _c01(energy_after / ENERGY_SCALE)
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

    # ---- 对面行为预判（opponent_model -> anticipate）------------------
    #
    # 用户口径："对面行为的概率分布 我们可以统计下来 A 放技能
    # a1 攻击 a2 防御 a3 状态 B 换人 换哪些人 概率分布"。
    #
    # 模型来自 logs/opponent_actions.jsonl（每回合记录对面做了什么），
    # 分层池化后即使对位样本少也能用（见 opponent_model.build_from_actions）。
    # 拿不到可靠样本时 adv=None，下面的逻辑完全退化成旧行为。
    adv = None
    try:
        import anticipate
        adv = anticipate.advice(
            me=my_name, foe=foe_name,
            my_pct=(100.0 * my_hp / my_max) if my_max else None,
            foe_pct=(100.0 * foe_hp / foe_max) if foe_max else None,
            my_hp=my_max, foe_hp=foe_max, bench=bench)
    except Exception:
        adv = None
    p_atk = (adv or {}).get("p_attack")
    p_buf = (adv or {}).get("p_buff")

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
        # ---- 先发制人：priority>0 的技能在对面出手**之前**结算 -------------
        #
        # 用户明确要求"先发制人的什么时候使用"。判据很干净：
        #   这回合会死 + 先发技能能反杀 -> 直接打，不用换人。
        # 换人会白送一次出场，而且新精灵下回合还要面对同一个对手。
        for s in me.get("skills") or []:
            if (s.get("kind") == "num" and s.get("lit", True)
                    and int(s.get("priority") or 0) > 0
                    and (s.get("dmg") or 0) >= foe_hp):
                return (("skill", s["i"]),
                        "priority %+d kills before foe acts (%d dmg >= %d hp)"
                        % (int(s.get("priority")), s["dmg"], foe_hp), {})
        # 换人：新精灵能扛住这一击才算有效。
        #
        # ⚠ 2026-11-22 修正：原来是"第一个能扛住的就选"，但 bench 的顺序
        # 是槽位顺序，与"谁最健康"无关。实测选中的替补自己也只有
        # 115 血（对面 238 伤害），换上去白送。
        # 现在按"扛完还剩多少血"排序，选最优的那只。
        best = None
        for slot, pet in enumerate(bench or [], start=1):
            if not pet.get("name") or pet.get("hp", 0) <= 0:
                continue
            inc_new, _src = _incoming(foe_name, pet["name"],
                                      pet.get("hp_max") or 1)
            if inc_new < pet.get("hp", 0):
                after = pet["hp"] - inc_new
                if best is None or after > best[0]:
                    best = (after, slot, pet, inc_new)
        if best is not None:
            _after, slot, pet, inc_new = best
            return (("switch", slot),
                    "dying this turn (%d hp vs %d incoming) -> "
                    "switch to %s (survives with %d hp)"
                    % (my_hp, inc, pet["name"], _after), {})
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
        # 出手顺序：先发制人（priority>0）在速度之前结算，所以它一定先动；
        # 否则比速度，谁快谁先手。
        pri = int(s.get("priority") or 0)
        acts_first = we_outspeed or pri > 0
        if acts_first:
            kills = dmg >= foe_hp
            taken = 0 if kills else inc
        else:
            dies_first = inc >= my_hp
            kills = (not dies_first) and dmg >= foe_hp
            taken = inc
        score = _outcome(dmg / max(1, foe_max), kills,
                         (inc if p_atk is None else
                          int(round(inc * max(p_atk, 0.35)))) / max(1, my_max),
                         (not acts_first) and inc >= my_hp,
                         my_hp - taken, my_max, energy_after, 0, matchup)
        cands.append((score, ("skill", s["i"]),
                      "skill %d: %d dmg, %d taken%s, %s" % (
                          s["i"], dmg, taken,
                          "" if p_atk is None else " (p_atk %.0f%%)"
                          % (p_atk * 100),
                          ("priority %+d" % pri) if pri > 0
                          else ("kill" if kills
                                else ("outspeed" if we_outspeed
                                      else "slower")))))

    # ---- non-damaging skills (buffs / heals / shields / copy) ------------
    # 实测结论（模拟器自对弈，2026-11-21）：
    #   buff 当"得分为 0 + 常量奖励"的候选参与打分 -> 胜率 50.7%
    #   buff 无条件优先（前两回合、血够、还没叠过） -> 胜率 66.7%
    # 差 16 个百分点。原因是收益结构不同：
    #   攻击 = 这一回合的一次输出；buff = 后续每一回合的 +20%。
    #
    # **但 2026-11-21 晚发现这个 66.7% 站不住**：
    #   模拟器假设 buff "每层 +20% 攻防、最多 3 层"，而 roco-cal 的
    #   真实数据里没有叠层机制 —— 每个 buff 技能是一次性固定数值
    #   （攻 buff 中位 +70%，防 buff 中位 +90%，且是不同技能）。
    #   更严重的是真实队伍 24 个技能位里 **0 个 buff 技能**：
    #   所有 slot 4 都是「借用」/「取念」（每回合随机变成别人技能）。
    #   旧代码把这些 power=0 的复制技能一律当 buff，导致实盘连续 7 局
    #   在 T1/T2 白打一回合。日志证据：`T1 SKILL 4 (buff first)`。
    #
    # 现在按 skill_kind 的真实分类处理：
    #   self_buff     -> 前两回合优先叠（真正的增益，值得抢节奏）
    #   copy          -> 明确排除，得分为 0（打了纯亏一回合）
    #   reduction/heal/enemy_debuff/other/unknown -> 得分为 0 参与打分
    #
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

    # 兼容旧调用方：老代码传 kind="dash" 表示"无伤害技能"。
    # 那种情况下没有名字可查，按 unknown 处理（不再无脑当 buff）。
    BUFF_KINDS = ("self_buff",)
    NON_BUFF_DASH = ("copy", "reduction", "heal", "enemy_debuff",
                     "other", "unknown", "dash")

    for s in me.get("skills") or []:
        kind = s.get("kind")
        if kind == "num":
            continue
        if kind not in BUFF_KINDS and kind not in NON_BUFF_DASH:
            continue
        if not s.get("lit", True):
            continue
        cost = s.get("cost") or 0
        if cost > energy:
            continue

        if kind not in BUFF_KINDS:
            # 无伤害但**不是**自身增益：复制技能 / 减伤 / 治疗 / 挂减益。
            #
            # 它们不是"免费的 0 分"。用掉这一回合照样要挨对面一下，
            # 所以必须和攻击/蓄能一样扣 w_damage_taken 和 w_opp_ko。
            #
            # 实测（2026-11-21，test_policy 第 3 例）：只剩一张 dash、
            # 承受 273 >= 当前 200 血、没有替补。旧代码给它硬编码 0.0，
            # 结果"必死"的局面里它是全场最高分 —— 引擎认为这一手不亏。
            # 而蓄能因为老实扣了"被打死"的 0.20，反而只有 -0.18。
            # 于是机器人在必死的回合选了变化技能，白白丢一手。
            #
            # 现在这个基线和别的候选同源：0 输出 + 吃满这一击。
            cost_e = max(0, energy - cost)
            base = _outcome(0.0, False,
                            inc / max(1, my_max), inc >= my_hp,
                            my_hp - inc, my_max, cost_e, 0, matchup)
            # 复制技能（借用/取念）额外惩罚：它是随机的，期望收益低于
            # 任何确定的动作，而且这一回合必定没有输出。
            pen = base + (-0.15 if kind == "copy" else 0.0)
            cands.append((pen, ("skill", s["i"]),
                          "status %d: %s (no priority, %s)"
                          % (s["i"], kind,
                             "lethal incoming" if inc >= my_hp else "takes %d"
                             % inc)))
            continue

        survives = inc < my_hp * (1.0 - w.get("buff_survive_floor", 0.25))
        if not survives:
            cands.append((-w.get("buff_no_survive_penalty", 0.25),
                          ("skill", s["i"]),
                          "status %d: would likely die" % s["i"]))
            continue

        # 关键：buff 只在"叠完还能打够 N 次"时才值得。
        #
        # 实测（2026-11-21，真实 buff 模型 +70% 攻、不加防）：
        #   无条件叠（前两回合）        -> 13.7%
        #   预计还能活 2 回合以上才叠    -> 61.3%
        #   纯贪心不叠                  -> 50.3%
        #
        # 原因：叠 buff 的代价是放弃这一回合的输出，收益是后续每回合 +70%。
        # 叠完还能打 k 次，k >= 2 才回本。真实 buff 不加防御，精灵普遍
        # 2 下就死，无条件叠通常只剩 1 次攻击 -> 净亏。
        #
        # 旧代码（虚构的 +60% 攻防、可叠 3 层）无条件叠能到 66.5%，
        # 那是因为防御 buff 让精灵多活了几个回合，收益结构完全不同。
        #
        # 只能叠一次：policy_v2.decide 是纯函数，没有内部状态，
        # 所以"是否已经叠过"由调用方通过 state["buffed"] 传入。
        need_turns = w.get("buff_need_turns", 2.0)
        survive_turns = my_hp / max(1, inc)
        early = (state.get("turn", 1) or 1) <= w.get("buff_max_turn", 4)
        already = bool(state.get("buffed"))
        # 对面大概率攻击 -> 叠 buff 的代价更高，多要求半回合生存。
        # 反之对面大概率也在叠 buff/防御（p_buf 高），这回合更安全。
        risky = p_atk is not None and p_atk >= 0.7
        need = need_turns + (0.5 if risky else 0.0)
        if early and not already and not foe_killable_now \
                and survive_turns >= need:
            cands.append((9e9, ("skill", s["i"]),
                          "status %d: buff first (survive %.1f turns%s)" % (
                              s["i"], survive_turns,
                              "" if p_atk is None
                              else ", p_atk %.0f%%" % (p_atk * 100))))
        else:
            cands.append((0.0, ("skill", s["i"]),
                          "status %d: buff (no priority, survive %.1f)" % (
                              s["i"], survive_turns)))

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
    # ★ 2026-11-22：换人的 KO 判定打折（见循环内注释）。
    # 旧版还给换人设 margin 门槛，但那挡不住 KO 奖励级别的落差（+0.30），
    # 根因是 KO 奖励本身不该建立在"池 max"上。margin 留在权重表里备用。
    margin = w.get("switch_margin", 0.04)
    # 换人的 KO 加成基于 best_damage_from（整池取 max）—— 那是**池子幻想**：
    # 替补的 4 个技能还没揭晓，池 max 只是"它可能带的最强一击"。
    # 实测（音速犬 vs 白发路路）：池 max 339 >= 300 触发 w_ko=+0.30，
    # 把换人抬到 0.37，而最佳攻击只有 0.11 —— 健康满能量也换人，
    # 每 2~3 回合白丢一次输出节奏。
    #
    # 修法：只有池 max **打六折后**仍 >= 对面当前血量，才认 KO
    # （即"连平凡发挥都能杀"）。339*0.6=203 < 300 -> 不给 KO 加成；
    # 而真正的必杀（比如池 max 500+）依然能触发。
    SWITCH_KO_DISCOUNT = w.get("switch_ko_discount", 0.6)
    for slot, pet in enumerate(bench or [], start=1):
        if not pet.get("name") or pet.get("hp", 0) <= 0:
            continue
        dmg = best_damage_from(pet["name"], foe_name)
        inc_new, _src = _incoming(foe_name, pet["name"], pet.get("hp_max") or 1)
        # The switch itself eats the turn, so the incoming hit lands unopposed.
        survives = inc_new < pet.get("hp", 0)
        # 池 max 的 KO 判定打折：连 60% 发挥都能杀才算"换上去能秒"
        switch_kills = dmg >= foe_hp and (dmg * SWITCH_KO_DISCOUNT) >= foe_hp
        score = _outcome(dmg / max(1, foe_max), switch_kills,
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
        "anticipate": adv,
        "runner_up": (round(cands[1][0], 4), cands[1][1]) if len(cands) > 1 else None,
        "all": [(round(s, 4), a) for s, a, _ in cands[:6]],
    }
    return action, why, detail
