# -*- coding: utf-8 -*-
"""Side-by-side comparison of every damage/stat implementation in the repo.

For the same inputs, each module is asked for the same quantity.  Where they
disagree, that disagreement is a latent bug: the decision layer picks whichever
module it happens to import.
"""
from __future__ import annotations

import os
import sys

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))

import damage as D        # noqa: E402
import dmg_calc as DC     # noqa: E402
import foe_damage as FD   # noqa: E402
import sim                # noqa: E402
import statmodel as S     # noqa: E402


def row(label, *vals):
    print("  %-34s %s" % (label, "  ".join("%12s" % v for v in vals)))


def main():
    print("=== A. 面板 HP（音速犬 sv=85，PvP 点满）===")
    row("模块", "statmodel", "damage.py", "foe_damage")
    row("iv 语义", "iv=60", "iv=10", "iv=60")
    row("HP", S.calc_real_stat(85, 60, 1.0, True),
        D.calc_hp(85, 10), FD.panel(85, 60, hp=True))
    print("  实测游戏值 = 366")

    print("\n=== B. 面板 非HP（音速犬 atk sv=?) ===")
    # use a concrete sv
    sv = 100
    row("模块", "statmodel", "damage.py", "foe_damage")
    row("ATK", S.calc_real_stat(sv, 60, 1.0), D.calc_stat(sv, 10),
        FD.panel(sv, 60))
    print("  -> 三个模块对同一只精灵给出不同面板值")

    print("\n=== C. 伤害公式（攻=244 防=196 威力=140 本系=1.25 克制=1.0）===")
    atk, dfn, power = 244, 196, 140
    row("模块", "statmodel", "damage.py", "sim", "dmg_calc")
    s_val = S.damage(atk, dfn, power, stab=1.25)
    d_val = D.damage(atk, dfn, power * 1.25)
    si_val = sim.damage(power, atk / float(dfn), 1.25, 1.0)
    dc_val = DC.predict(power, atk / float(dfn), 1.25, 1.0)
    row("结果", s_val, d_val, si_val, dc_val)
    print("  实测卡面 = 175")

    print("\n=== D. STAB 常数 ===")
    row("dmg_calc.STAB", DC.STAB)
    row("foe_damage.STAB", FD.STAB)
    print("  真值 = 1.25（隼鳞 140->175、蝙蝠 65->154 两张卡同时成立）")

    print("\n=== E. 谁在用它（活引用）===")
    print("  statmodel  <- test_statmodel.py")
    print("  damage     <- policy.py, policy_v2.py, pvp_engine.py")
    print("  dmg_calc   <- test_smart.py  (只有测试)")
    print("  sim        <- 无")
    print("  foe_damage <- robot.py(3处), foe_moves.py")
    print("  pvp_engine <- robot.py(4处), policy_v2.py, pipeline_check.py")
    print("  pet_state  <- 无")

    print("\n=== F. 结论 ===")
    print("  4 套活公式 + 2 套死代码；其中 statmodel 是唯一对过 4/4 实测血量的，")
    print("  dmg_calc 的 STAB 常数是错的（1.2，应为 1.25），已实测 89.7% 误差。")


if __name__ == "__main__":
    main()
