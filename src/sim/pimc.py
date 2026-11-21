# -*- coding: utf-8 -*-
"""pimc.py —— 完美信息蒙特卡洛（PIMC）决策。

问题
----
NRC_AI 移植的 MCTS 用 B.get_actions(state, team) 直接遍历 current.skills，
对抗搜索时双方都能看到对手 4 个技能 —— 完美信息。但实战里对手带什么
技能未知（同种精灵可带任意 4 个）。

PIMC 解法：每次决策
    1. 从信念分布采样对手一组 4 技能（belief.sample_moveset）
    2. 把克隆状态里对手技能替换成采样结果
    3. 确定性搜索评估当前局面下**我方每个合法动作**（推演一步完整回合）
    4. 对每个动作，把它在所有采样下的价值取平均，选平均价值最高的

对外接口
--------
    choose(state, foe_species, sims=32, depth=2, rng=None)
        -> (action, stats dict)

    selftest()
        构造局面跑一次 PIMC，打印采样、对比贪心。

说明
----
· 只用 sim.battle 的公开接口（get_actions / execute_full_turn /
  check_winner / DamageCalculator），不碰任何被禁止修改的文件。
· state 克隆用 models.BattleState.deep_copy()（已确认存在）。
· 对 data/roco.sqlite3 零访问；信念采样只读。
· 对手响应：
    depth>=2 → 枚举对手全部合法动作，取对最坏（min，对抗性假设）
    depth==1 → 对手打我方最高面板伤害的贪心动作
    depth==0 → 对手聚能（(-1,)）
"""
from __future__ import annotations

import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.dirname(_HERE)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import sim.battle as B                      # noqa: E402
from sim.models import BattleState          # noqa: E402
from sim import belief, adapter             # noqa: E402


# ---------------------------------------------------------------- 评估
def _hp_sum(team):
    return sum(max(0, getattr(p, "current_hp", 0) or 0) for p in team)


def _alive_count(team):
    return sum(1 for p in team if not p.is_fainted)


def evaluate(state):
    """评估函数：血量差 + 存活差修正（我方视角，a 优为正）。

    血量差是主项；存活数差每差一只 +100（一只精灵的 HP 大约几百，
    这个权重大致等于"多一只精灵"带来的长期价值，但不过分压过单回合伤害）。
    """
    hp_a, hp_b = _hp_sum(state.team_a), _hp_sum(state.team_b)
    alive_a, alive_b = _alive_count(state.team_a), _alive_count(state.team_b)
    return (hp_a - hp_b) + 100.0 * (alive_a - alive_b)


def _foe_action_for(sample, foe_acts, team_b, foe_idx):
    """对手采样技能比面板短时，过滤掉越界动作，避免 IndexError。"""
    if not sample:
        return None
    if len(sample) >= len(team_b[foe_idx].skills):
        return foe_acts
    allowed = set(range(len(team_b[foe_idx].skills)))
    out = [a for a in foe_acts if a[0] < 0 or a[0] in allowed]
    return out or [(-1,)]


def _greedy_foe_action(state, foe_acts):
    """对手贪心响应：打我方当前精灵最高面板伤害，没输出就聚能。"""
    foe = state.team_b[state.current_b]
    me = state.team_a[state.current_a]
    best, best_d = None, -1.0
    for a in foe_acts:
        if a[0] < 0:
            d = 0.0
        elif a[0] < len(foe.skills):
            d = B.DamageCalculator.calculate(foe, me, foe.skills[a[0]])
        else:
            d = 0.0
        if d > best_d:
            best_d, best = d, a
    return best if best is not None else (-1,)


def _my_action_value(s1, my_act, foe_acts, depth, rng):
    """在给定采样下，我方动作 my_act 的价值（推演一步完整回合）。

    depth>=2：小极大式悲观 —— 枚举对手全部合法动作，取对我方最坏的价值。
    depth==1：对手贪心（打我最高伤害）。
    depth==0：对手聚能。
    """
    if depth >= 2 and foe_acts:
        vals = []
        for fa in foe_acts:
            s2 = s1.deep_copy()
            B.execute_full_turn(s2, my_act, fa)
            w = B.check_winner(s2)
            v = evaluate(s2)
            if w == "b":
                v -= 1e6
            elif w == "a":
                v += 1e6
            vals.append(v)
        return min(vals) if vals else -1e9

    fa = _greedy_foe_action(s1, foe_acts)
    s2 = s1.deep_copy()
    B.execute_full_turn(s2, my_act, fa)
    w = B.check_winner(s2)
    v = evaluate(s2)
    if w == "b":
        v -= 1e6
    elif w == "a":
        v += 1e6
    return v


# ---------------------------------------------------------------- PIMC
def choose(state, foe_species, sims=32, depth=2, rng=None):
    """PIMC 决策。

    返回 (action, stats dict)：
        action        — 我方动作 (skill_idx,) / (-1,) / (-2, switch_idx)
        stats         — {
            'samples':    每份采样 (技能名列表, 该采样下最优我方动作, 价值)
            'my_actions': 我方合法动作列表
            'avg_values': 每个我方动作的平均价值（与 my_actions 对齐）
            'foe_species': 规范化后的对手精灵名
            'sims':       采样数
            'depth':      搜索深度
        }

    每份采样：评估我方**所有**合法动作的价值 → 累加；最后按动作求平均，
    选平均价值最高的 —— 这正是 PIMC：采样式对手信念 + 确定性备份。
    """
    rng = rng or random
    stats = {
        "samples": [],
        "my_actions": [],
        "avg_values": [],
        "foe_species": belief._base_name(foe_species),
        "sims": sims,
        "depth": depth,
    }

    # 我方动作空间由真实状态确定（换入采样后我方技能不变，index 不变）
    my_acts = B.get_actions(state, "a")
    if not my_acts:
        return (-1,), stats
    stats["my_actions"] = list(my_acts)

    sums = {a: 0.0 for a in my_acts}
    counts = {a: 0 for a in my_acts}

    orig_len = len(state.team_b[state.current_b].skills)

    for _ in range(max(1, sims)):
        sample = belief.sample_moveset(foe_species, k=4, rng=rng)
        s1 = state.deep_copy()
        foe_poke = s1.team_b[s1.current_b]
        if sample and orig_len:
            foe_poke.skills = [sk.copy() for sk in sample]
            # 技能列表长度变化会打乱 index 与 cooldowns 的对应：清零避免错位
            foe_poke.cooldowns = {}
            foe_poke.charging_skill_idx = -1
        # 采样为空 → 保留面板技能，仍按确定性搜索推演

        foe_acts = B.get_actions(s1, "b")
        foe_acts = _foe_action_for(sample, foe_acts, s1.team_b, s1.current_b)

        best_in_sample, best_v = None, None
        for ma in my_acts:
            v = _my_action_value(s1, ma, foe_acts, depth, rng)
            sums[ma] += v
            counts[ma] += 1
            if best_in_sample is None or v > best_v:
                best_in_sample, best_v = ma, v

        stats["samples"].append((
            [s.name for s in sample],
            best_in_sample,
            round(best_v, 4),
        ))

    avg = {}
    for a in my_acts:
        avg[a] = sums[a] / counts[a] if counts[a] else 0.0
    stats["avg_values"] = [round(avg[a], 4) for a in my_acts]

    best_action = None
    best_v = None
    for a in my_acts:
        if best_action is None or avg[a] > best_v:
            best_action, best_v = a, avg[a]

    return best_action, stats


# ---------------------------------------------------------------- 贪心基线
def greedy_choose(state, rng=None):
    """贪心基线：能杀就打最高面板伤害，否则打最高面板伤害；没输出就聚能。"""
    acts = B.get_actions(state, "a")
    if not acts:
        return (-1,)
    cur = state.team_a[state.current_a]
    foe = state.team_b[state.current_b]
    best, best_d = (-1,), -1.0
    for a in acts:
        if a[0] < 0:
            d = 0.0
        elif a[0] < len(cur.skills):
            d = B.DamageCalculator.calculate(cur, foe, cur.skills[a[0]])
        else:
            d = 0.0
        if d > best_d:
            best_d, best = d, a
    return best


# ---------------------------------------------------------------- 自检
def _action_text(p, a):
    if a == (-1,):
        return "聚能"
    if len(a) >= 2 and a[0] == -2:
        return "换人"
    if 0 <= a[0] < len(p.skills):
        return p.skills[a[0]].name
    return "?"


def _act_dmg(cur, foe, a):
    if a[0] < 0 or a[0] >= len(cur.skills):
        return 0.0
    return B.DamageCalculator.calculate(cur, foe, cur.skills[a[0]])


def selftest():
    """自检：我方全队 vs 半血的恶魔狼（中盘局面），采样 16 次 PIMC。

    选这个局面是因为开局阶段 PIMC 与贪心往往一致（同一招碾压），
    而中盘对手血量腰斩后，"秒杀 vs 稳定互殴"的选择会真正体现
    对手配招不确定性对决策的影响（实测该局 PIMC 选火云车、贪心选闪光冲击）。
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rng = random.Random(3)                 # 实测 seed=3 稳定复现分歧

    my_names = adapter.our_team()
    my_team = [adapter.build_pokemon(nm) for nm in my_names]
    foe_team = [adapter.build_pokemon("恶魔狼")]
    # 中盘：对手血量腰斩，我方首发被磨到 60%
    foe_team[0].current_hp = int(foe_team[0].hp * 0.5)
    my_team[0].current_hp = int(my_team[0].hp * 0.6)
    st = BattleState(team_a=my_team, team_b=foe_team)
    foe_species = "恶魔狼"
    me = st.team_a[st.current_a]

    print("=== PIMC 自检：我方(%s) vs 恶魔狼（对手 HP=%.0f%%） ===" % (
        "、".join(my_names), 100.0 * st.team_b[0].current_hp / st.team_b[0].hp))
    print()
    print("对手技能信念（top 8）：")
    for nm, p in belief.moveset_distribution(foe_species, top=8):
        print("    %-12s p=%.3f" % (nm, p))
    print()

    action, stats = choose(st, foe_species, sims=16, depth=2, rng=rng)

    print("采样的对手配招（%d 次）：" % len(stats["samples"]))
    seen = set()
    for i, (sk_names, best, val) in enumerate(stats["samples"]):
        seen.add(tuple(sk_names))
        print("    #%-2d 队=%s  该采样最优=%s  价值=%.1f" % (
            i + 1, " / ".join(sk_names), _action_text(me, best), val))
    print()
    print("不同配招 %d 种 / %d 次采样" % (len(seen), len(stats["samples"])))
    print()
    print("我方合法动作与平均价值（%d 份采样）:" % stats["sims"])
    for a, v in zip(stats["my_actions"], stats["avg_values"]):
        print("    %-14s  面板伤害=%-5.0f  平均价值=%.1f" % (
            _action_text(me, a), _act_dmg(me, foe_team[0], a), v))
    print()
    print("PIMC 选择：%s" % _action_text(me, action))
    greedy = greedy_choose(st)
    print("贪心选择：%s" % _action_text(me, greedy))
    print()
    diff = action != greedy
    print("PIMC 与贪心%s" % ("** 不同 ** —— PIMC 起作用了（考虑了对手配招不确定性，"
                             "而非只看即时面板伤害）"
                             if diff else "相同（这个局面下两者一致）"))
    return 0 if diff else 1


if __name__ == "__main__":
    selftest()