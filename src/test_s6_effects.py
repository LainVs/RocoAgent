# -*- coding: utf-8 -*-
"""test_nrc_effects.py 的 S6 扩展：新 handler 的回归。"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402,F401

import nrc_fx  # noqa: E402
import simulator as SIM  # noqa: E402
import trait_lib  # noqa: E402

bad = 0


def check(cond, msg):
    global bad
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        bad += 1


def stub(trait, name="测试"):
    return types.SimpleNamespace(trait=trait, name=name, moves=[], hp=1,
                                 max_hp=1, alive=True, status={},
                                 atk_buff=0.0, def_buff=0.0, taken_mult=1.0,
                                 energy=10)


print("--- S6: 新 handler ---")
dex = SIM.Dex()
q = SIM.Pet(dex, "冰钻布鲁斯", iv=60)

# 恶魔的晚宴（库内名「恶魔的晚餐」，靠 trait_rows_fuzzy 命中）
s1 = stub("恶魔的晚餐")
evs = nrc_fx.trait_fire(s1, q, "ON_KILL", {})
check(s1.atk_buff >= 0.5,
      "恶魔的晚餐：击杀加双攻（模糊命中 nrc 的晚宴）{}".format(evs or ["无"]))

# 小偷小摸（对手被吸 2 能量）
q.energy = 8
s2 = stub("小偷小摸")
evs = nrc_fx.trait_fire(s2, q, "ON_ENEMY_SWITCH", {})
check(q.energy == 6, "小偷小摸：对手能量 8 -> %d（-2）" % q.energy)

# 悲悯（入场按倒下队友数）
p3 = SIM.Pet(dex, "恶魔狼", iv=60)
bt = SIM.Battle(dex, ["叮叮恶魔", "冰钻布鲁斯"], ["音速犬"], seed=0)
p3._battle = bt
p3._side = "a"
bt.a[1].hp = 0
evs = nrc_fx.trait_fire(p3, q, "ON_ENTER", {})
check(p3.atk_buff >= 0.3, "悲悯：1 只队友倒下 -> 双攻 +%.0f%%"
      % (100 * p3.atk_buff))

# 圣火骑士（应对成功后威力翻倍 -> power_mult 2.0）
s4 = stub("圣火骑士")
ctx = {}
nrc_fx.trait_fire(s4, q, "ON_COUNTER_SUCCESS", ctx)
check(abs(ctx.get("power_mult", 1.0) - 2.0) < 1e-9,
      "圣火骑士：应对成功后威力 x2（实际 %.2f）" % ctx.get("power_mult", 1.0))

# 灵魂灼伤（给对手上灼烧）
s5 = stub("灵魂灼伤")
nrc_fx.trait_fire(s5, q, "ON_USE_SKILL", {})
check(q.status.get("burn", 0) > 0,
      "灵魂灼伤：对手获得灼烧 %d 层" % q.status.get("burn", 0))

# 恶魔的晚餐在库内的名字不被 roco-cal 命中（防止双重应用）
check(not trait_lib.supported_rules("恶魔的晚餐"),
      "恶魔的晚餐不在 roco-cal 可用规则里（只会走 nrc 一层）")

print()
if bad:
    print("FAILED %d" % bad)
    sys.exit(1)
print("ALL PASS")
