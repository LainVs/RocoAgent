# -*- coding: utf-8 -*-
"""tune_policy.py —— 用模拟器搜索最优策略参数。

为什么可以这么做
----------------
模拟器复用真实的伤害模型和数据，跑几千局只要几秒。
参数搜索的结论可以直接写回 config/policy_weights.json。

搜索维度
--------
    buff_bonus          叠 buff 的奖励（当前 0.06，模拟显示太保守）
    buff_survive_floor  什么血线以下不许叠
    charge_score        聚能的评分（当前 0.06）

方法
----
网格搜索 + 同队自对弈（排除队伍差异），每档 300 局。
"""
from __future__ import annotations

import io
import json
import os
import sys
from collections import Counter

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))

import simulator as SIM  # noqa: E402


class TunablePolicy(SIM.Policy):
    """可调参数的策略：用 buff_bonus 控制叠 buff 的倾向。

    简化模拟：
      · buff_bonus 高 -> 更愿意在第 1 回合叠 buff
      · 叠完后全力输出
    """

    name = "tunable"

    def __init__(self, buff_bonus=0.06, survive_floor=0.35):
        self.buff_bonus = buff_bonus
        self.survive_floor = survive_floor
        self._buffed = False

    def choose(self, me, foe, bench, turn):
        affordable = [i for i, mv in enumerate(me.moves)
                      if mv["cost"] <= me.energy]
        if not affordable:
            return ("energy", None)
        dmgs = {i: SIM.calc_damage(me, foe, me.moves[i]) for i in affordable}
        best_i = max(dmgs, key=dmgs.get)
        best_d = dmgs[best_i]

        if best_d >= foe.hp:
            return ("skill", best_i)

        # 叠 buff 的决策：血线够 + 还没叠过 + buff_bonus 超过阈值
        incoming = max((SIM.calc_damage(foe, me, mv) for mv in foe.moves),
                       default=0)
        survives = me.hp - incoming > me.max_hp * self.survive_floor
        if (not self._buffed and survives and self.buff_bonus >= 0.10
                and turn <= 2 and best_d < foe.hp):
            self._buffed = True
            return ("buff", None)

        return ("skill", best_i)


def evaluate(bonus, floor, n=300, team=None):
    dex = SIM.Dex()
    team = team or [x for x in SIM.DEFAULT_TEAM_A if dex.species(x)]
    wins = Counter()
    for i in range(n):
        r = SIM.run_match(dex, team, team,
                          TunablePolicy(bonus, floor), SIM.GreedyPolicy(),
                          seed=i)
        wins[r[0]] += 1
    return 100.0 * wins["a"] / n


def main():
    team = None
    print("=== buff_bonus 网格搜索（同队自对弈，各 300 局）===")
    print("%-14s %10s" % ("buff_bonus", "胜率"))
    print("-" * 26)
    best = (0, -1)
    for bonus in (0.0, 0.06, 0.10, 0.14, 0.18, 0.22, 0.26):
        rate = evaluate(bonus, 0.35)
        mark = ""
        if rate > best[1]:
            best = (bonus, rate)
            mark = "  <- best"
        print("%-14.2f %9.1f%%%s" % (bonus, rate, mark))

    print("\n最佳 buff_bonus = %.2f（胜率 %.1f%%）" % best)

    # 再搜 survive_floor
    print("\n=== buff_survive_floor 网格（bonus=%.2f）===" % best[0])
    print("%-14s %10s" % ("survive_floor", "胜率"))
    print("-" * 26)
    best2 = (0, -1)
    for floor in (0.25, 0.35, 0.45, 0.55):
        rate = evaluate(best[0], floor)
        mark = ""
        if rate > best2[1]:
            best2 = (floor, rate)
            mark = "  <- best"
        print("%-14.2f %9.1f%%%s" % (floor, rate, mark))

    print("\n建议写入 config/policy_weights.json：")
    print("  buff_bonus        = %.2f" % best[0])
    print("  buff_survive_floor= %.2f" % best2[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
