# -*- coding: utf-8 -*-
"""精灵的"随时间变弱"状态：等级衰减（冥古龙等）。

规则（用户实测确认）：
    冥古龙在场每回合结束，等级 -10
    换下场 **不重置** —— 整场累计

所以它整场能用的"等级总量"是固定的：
    60 级开局，站 1 回合 -> 50，站 2 回合 -> 40
    站满 6 回合 -> 0（基本等于废掉）

这决定了它的用法：**留到能一击必杀的时候再上**，不能当常规输出站场。
"""

DECAY_RULES = {
    # 特性/物种名 -> 每回合等级衰减
    "冥古龙": 10,
}

BASE_LEVEL = 60


def effective_level(name, turns_on_field, base=BASE_LEVEL):
    """某只精灵在场上站了 N 回合之后的实际等级（换下不重置，累积计算）。"""
    d = DECAY_RULES.get(name, 0)
    return max(0, base - d * int(turns_on_field))


def level_coef(level):
    """rocopvp 公式里的等级系数：(等级*45/100 + 10) / 41。"""
    return (level * 45.0 / 100.0 + 10.0) / 41.0


def damage_ratio(name, turns_on_field, base=BASE_LEVEL):
    """相对满级的伤害倍率（用来判断"还打不打得死"）。"""
    if not DECAY_RULES.get(name):
        return 1.0
    return level_coef(effective_level(name, turns_on_field, base)) / level_coef(base)


def is_decaying(name):
    return name in DECAY_RULES


if __name__ == "__main__":
    name = "冥古龙"
    print("%s 站场回合 -> 等级 / 伤害倍率" % name)
    for t in range(0, 7):
        print("  第 %d 回合  等级 %2d  伤害 %.0f%%"
              % (t, effective_level(name, t), damage_ratio(name, t) * 100))
    print("\n普通精灵: %.0f%%（不衰减）" % (damage_ratio("卡瓦重", 5) * 100))
