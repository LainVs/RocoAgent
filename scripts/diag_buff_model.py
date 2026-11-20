# -*- coding: utf-8 -*-
"""诊断：真实 buff 模型下 BuffPolicy 为什么只有 13.7%。

旧模型：叠 3 层 = +60% 攻击 **且** +60% 防御（永久）-> 66.5%
新模型：一次性 +70% 攻击，不加防御 -> 13.7%

差距可能来自两处：
  1. 真实数据确实不给防御（只加攻击），所以 buff 变弱了
  2. 我改代码时引入了 bug

本脚本逐回合打印，确认是模型差异还是代码 bug。
"""
import sys

sys.path.insert(0, "src")
from simulator import (Dex, run_match, GreedyPolicy, BuffPolicy,
                       calc_damage, ATK_BUFF_MEDIAN)

dex = Dex()
A = [x for x in ["音速犬", "恶魔狼", "冥古龙", "卡瓦重",
                 "冰钻布鲁斯", "刨雪鹅"] if dex.species(x)]

print("ATK_BUFF_MEDIAN = %.2f" % ATK_BUFF_MEDIAN)


class TraceBuff(BuffPolicy):
    def choose(self, me, foe, bench, turn):
        r = super().choose(me, foe, bench, turn)
        tag = ""
        if r[0] == "buff":
            tag = "  <== BUFF (atk_buff 之后 %.2f)" % ATK_BUFF_MEDIAN
        print("  T%-2d %-10s hp=%-3d/%-3d atk_buff=%.2f  -> %s%s" % (
            turn, me.name, me.hp, me.max_hp, me.atk_buff, r[0], tag))
        return r


class TraceGreedy(GreedyPolicy):
    def choose(self, me, foe, bench, turn):
        r = super().choose(me, foe, bench, turn)
        print("  T%-2d %-10s hp=%-3d/%-3d atk_buff=%.2f  -> %s" % (
            turn, me.name, me.hp, me.max_hp, me.atk_buff, r[0]))
        return r


print()
print("=== 单局：BuffPolicy(左) vs GreedyPolicy(右) ===")
r = run_match(dex, A, A, TraceBuff(), TraceGreedy(), seed=0)
print("结果:", r)

# 验证 buff 确实生效：同一只精灵叠前后伤害差
print()
print("=== 验证 buff 是否真的提高伤害 ===")
from simulator import Pet
p = Pet(dex, "音速犬")
f = Pet(dex, "恶魔狼")
mv = p.moves[0]
d0 = calc_damage(p, f, mv)
p.atk_buff = ATK_BUFF_MEDIAN
d1 = calc_damage(p, f, mv)
print("  %s 打 %s：无 buff %d，有 buff %d（提升 %.1f%%）" % (
    p.name, f.name, d0, d1, 100.0 * (d1 - d0) / max(1, d0)))
