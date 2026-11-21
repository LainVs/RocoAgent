# -*- coding: utf-8 -*-
"""selfplay.py —— 用我们真实队伍跑自对弈。

验证目标（S3）
--------------
    · 引擎能跑完整对局，不崩
    · 六维/技能/特性都正确注入
    · 效果能触发（吸血、回能、威力加成）

跑法：
    python src\\sim\\selfplay.py              # 1 局，打印每回合
    python src\\sim\\selfplay.py --n 100      # 100 局，统计胜率
"""
from __future__ import annotations

import argparse
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import adapter  # noqa: E402
import sim.battle as B  # noqa: E402
from sim.models import BattleState  # noqa: E402


def pick_action(state, team):
    """简单策略：能杀就打最高伤害，否则打最高伤害，没能量就聚能。

    刻意做得简单 —— 这一步只验证引擎，不验证策略。
    """
    acts = B.get_actions(state, team)
    if not acts:
        return (-1,)
    cur = state.team_a[state.current_a] if team == "a" \
        else state.team_b[state.current_b]
    foe = state.team_b[state.current_b] if team == "a" \
        else state.team_a[state.current_a]

    best = (-1,)
    best_dmg = -1
    for act in acts:
        if len(act) == 1 and act[0] >= 0:
            sk = cur.skills[act[0]]
            if sk.power <= 0:
                continue
            d = B.DamageCalculator.calculate(cur, foe, sk)
            if d > best_dmg:
                best_dmg = d
                best = act
    if best_dmg < 0:
        # 没有可用攻击技能：聚能
        return (-1,)
    return best


def run_one(verbose=False, seed=0):
    random.seed(seed)
    names = adapter.our_team()
    a = [adapter.build_pokemon(n) for n in names]
    b = [adapter.build_pokemon(n) for n in names]
    st = BattleState(team_a=a, team_b=b)

    if verbose:
        print("=== 队伍（双方相同）===")
        for p in a:
            print("  %-10s hp=%-4d atk=%-4d spe=%-4d 技能=%s" % (
                p.name, p.hp, p.attack, p.speed,
                "/".join(s.name for s in p.skills)))
        print()

    max_turns = 80
    for turn in range(1, max_turns + 1):
        B.auto_switch(st)
        w = B.check_winner(st)
        if w:
            if verbose:
                print("  第 %d 回合前分出胜负: %s" % (turn, w))
            return w, turn

        pa = st.team_a[st.current_a]
        pb = st.team_b[st.current_b]
        act_a = pick_action(st, "a")
        act_b = pick_action(st, "b")

        if verbose:
            print("  T%-2d %-10s(hp%3d) %-10s(hp%3d)  %s vs %s" % (
                turn, pa.name, pa.current_hp, pb.name, pb.current_hp,
                _act_name(pa, act_a), _act_name(pb, act_b)))

        B.execute_full_turn(st, act_a, act_b)

        if verbose:
            print("       -> %-10s(hp%3d) %-10s(hp%3d)" % (
                pa.name, pa.current_hp, pb.name, pb.current_hp))

    ha = sum(p.current_hp for p in st.team_a)
    hb = sum(p.current_hp for p in st.team_b)
    return ("a" if ha > hb else "b"), max_turns


def _act_name(p, act):
    if act == (-1,):
        return "聚能"
    if act[0] == -2:
        return "换人"
    if 0 <= act[0] < len(p.skills):
        return p.skills[act[0]].name
    return "?"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.n <= 1:
        w, t = run_one(verbose=not args.quiet, seed=args.seed)
        print()
        print("结果: 胜方=%s  回合=%d" % (w, t))
        return 0

    from collections import Counter
    wins = Counter()
    turns = []
    for i in range(args.n):
        w, t = run_one(verbose=False, seed=i)
        wins[w] += 1
        turns.append(t)
    print("=== %d 局（同队自对弈）===" % args.n)
    print("  A 胜 %d (%.1f%%)   B 胜 %d (%.1f%%)" % (
        wins["a"], 100.0 * wins["a"] / args.n,
        wins["b"], 100.0 * wins["b"] / args.n))
    turns.sort()
    print("  平均回合 %.1f  中位 %d" % (
        sum(turns) / len(turns), turns[len(turns) // 2]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
