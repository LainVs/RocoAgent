# -*- coding: utf-8 -*-
"""在真实 buff 模型下搜索"什么时候该叠 buff"。

背景
----
旧模型（虚构）：叠 3 层 = +60% 攻 **且** +60% 防 -> buff 策略 66.5%
新模型（真实）：一次性 +70% 攻，不加防 -> buff 策略 13.7%

为什么反转
----------
叠 buff 的代价是**放弃这一回合的输出**，收益是后续每回合 +70%。
设叠完还能攻击 k 次：
    收益 ≈ 0.7 * k 次攻击
    代价 = 1 次攻击
    k >= 2 才划算
而本队精灵普遍 2 下就死（不叠防御），叠完通常只剩 1 次攻击 -> 净亏。

所以"叠不叠"取决于**还能活几回合**，不能无条件叠。
本脚本搜索这个门槛。
"""
import sys
from collections import Counter

sys.path.insert(0, "src")
from simulator import (Dex, run_match, GreedyPolicy, Policy, calc_damage,
                       ATK_BUFF_MEDIAN)

dex = Dex()
A = [x for x in ["音速犬", "恶魔狼", "冥古龙", "卡瓦重",
                 "冰钻布鲁斯", "刨雪鹅"] if dex.species(x)]


class GatedBuff(Policy):
    """只在"叠完还能打够 k 次"时叠 buff。

    survive_turns: 预计还能活几回合 = hp / incoming
    need_turns   : 需要打几次才回本（默认 2）
    """

    name = "gated"

    def __init__(self, need_turns=2.0, use_def=False):
        self._buffed = False
        self._need = need_turns
        self._use_def = use_def

    def choose(self, me, foe, bench, turn):
        affordable = [i for i, mv in enumerate(me.moves)
                      if mv["cost"] <= me.energy]
        if not affordable:
            return ("energy", None)
        dmgs = {i: calc_damage(me, foe, me.moves[i]) for i in affordable}
        best_i = max(dmgs, key=dmgs.get)
        best_d = dmgs[best_i]

        if best_d >= foe.hp:            # 能杀就杀
            return ("skill", best_i)
        if self._buffed or me.buffed:
            return ("skill", best_i)

        # 对手当前能量能打出的最高伤害
        aff = [mv for mv in foe.moves if mv["cost"] <= foe.energy]
        inc = max((calc_damage(foe, me, mv) for mv in aff), default=1)
        survive_turns = me.hp / max(1, inc)

        if survive_turns >= self._need and turn <= 4:
            self._buffed = True
            return ("buff", None)
        return ("skill", best_i)


def bench(pol_factory, n=300, label=""):
    w = Counter()
    turns = []
    for i in range(n):
        r = run_match(dex, A, A, pol_factory(), GreedyPolicy(), seed=i)
        w[r[0]] += 1
        turns.append(r[1])
    win = 100.0 * w["a"] / n
    print("  %-30s 胜率 %5.1f%%  平均 %d 回合" % (
        label, win, sum(turns) // len(turns)))
    return win


print("=== 真实 buff 模型（+70%% 攻，不加防）下搜索门槛 ===")
print("  基准（不叠，纯贪心 vs 贪心）：")
bench(lambda: GreedyPolicy(), label="greedy vs greedy")
print()
print("  不同 need_turns（叠完需要还能打几次才回本）：")
for need in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
    bench(lambda nd=need: GatedBuff(need_turns=nd),
          label="gated need_turns=%.1f" % need)

print()
print("  对照：无条件叠（旧 BuffPolicy 的时机，新模型）")
from simulator import BuffPolicy
bench(lambda: BuffPolicy(), label="buff (unconditional)")
