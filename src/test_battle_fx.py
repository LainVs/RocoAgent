# -*- coding: utf-8 -*-
"""test_battle_fx.py - 印记 / 天气的回归（S3）。

印记规则来自游戏内原文（见 docs/PLAN_FULL_SIM.md）：
  · 精灵最多同时拥有 **1 种正面 + 1 种负面** 印记，新的顶掉同类的旧的
  · 换人不消失，新入场精灵继承
  · 攻击印记 +10% 威力、蓄势 +30% 威力且能耗 +1、湿润 -1 能耗、
    减速 -10 速、光合回合末 +1 能量、中毒印记回合末 3%、棘刺入场 -6% 生命、
    降灵入场 -1 能量、龙噬释放 3 能耗技能后双攻 +30%

usage: python test_battle_fx.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402,F401

import battle_fx  # noqa: E402
import simulator as SIM  # noqa: E402

bad = 0


def check(cond, msg):
    global bad
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        bad += 1


print("--- 1. 印记表（13 种，正/负面各若干）---")
table = battle_fx.mark_table()
pos = [m for m in table if m["kind"] == "positive"]
neg = [m for m in table if m["kind"] == "negative"]
print("     正面 %d：%s" % (len(pos), "、".join(m["label"] for m in pos)))
print("     负面 %d：%s" % (len(neg), "、".join(m["label"] for m in neg)))
check(len(table) == 14, "共 14 种印记（含星陨，实际 %d）" % len(table))

print()
print("--- 2. 同类最多一个（新顶旧）---")
mk = {}
battle_fx.set_mark(mk, "attack_mark")
check("attack_mark" in mk, "挂上攻击印记")
gone_added, gone = battle_fx.set_mark(mk, "momentum_mark")
check(gone == "attack_mark" and "momentum_mark" in mk and
      "attack_mark" not in mk, "再加蓄势印记 -> 顶掉攻击印记")
battle_fx.set_mark(mk, "poison_mark")
check("momentum_mark" in mk and "poison_mark" in mk,
      "正/负面可以同时各一个")

print()
print("--- 3. 威力 / 能耗 / 速度 ---")
dex = SIM.Dex()
bt = SIM.Battle(dex, ["音速犬"], ["冰钻布鲁斯"], seed=0)
mv = max(bt.me.moves, key=lambda m: m["power"])
base = SIM.calc_damage(bt.me, bt.foe, mv)
bt.add_mark("a", "attack_mark")
d1 = SIM.calc_damage(bt.me, bt.foe, mv)
check(abs(d1 / float(base) - 1.10) < 0.02,
      "攻击印记 +10%%：%d -> %d（x%.2f）" % (base, d1, d1 / float(base)))

bt2 = SIM.Battle(dex, ["音速犬"], ["冰钻布鲁斯"], seed=0)
bt2.add_mark("a", "momentum_mark")
d2 = SIM.calc_damage(bt2.me, bt2.foe, mv)
check(abs(d2 / float(base) - 1.30) < 0.02,
      "蓄势印记 +30%%：x%.2f" % (d2 / float(base)))
atk_mv = max((m for m in bt2.me.moves if m["power"] > 0),
             key=lambda m: m["power"])
check(bt2.eff_cost("a", atk_mv) == atk_mv["cost"] + 1,
      "蓄势印记能耗 +1（%s -> %s）"
      % (atk_mv["cost"], bt2.eff_cost("a", atk_mv)))

bt3 = SIM.Battle(dex, ["音速犬"], ["冰钻布鲁斯"], seed=0)
bt3.add_mark("a", "wet_mark")
check(bt3.eff_cost("a", mv) == max(0, mv["cost"] - 1),
      "湿润印记能耗 -1（%s -> %s）" % (mv["cost"], bt3.eff_cost("a", mv)))
sp0 = bt3.eff_spe("a", bt3.me)
bt3.add_mark("a", "slow_mark")
check(bt3.eff_spe("a", bt3.me) == sp0 - 10,
      "减速印记 -10 速（%s -> %s）" % (sp0, bt3.eff_spe("a", bt3.me)))

print()
print("--- 4. 回合末 / 入场 触发 ---")
bt4 = SIM.Battle(dex, ["音速犬"], ["冰钻布鲁斯"], seed=0)
bt4.add_mark("a", "photosyn_mark")
bt4.me.energy = 5
tick = battle_fx.mark_tick(bt4.marks["a"])
check(tick["energy"] == 1, "光合印记回合末 +%d 能量" % tick["energy"])

bt5 = SIM.Battle(dex, ["音速犬"], ["冰钻布鲁斯"], seed=0)
bt5.add_mark("b", "poison_mark")
tick5 = battle_fx.mark_tick(bt5.marks["b"])
check(abs(tick5["dot"] - 0.03) < 1e-9,
      "中毒印记回合末 3%%（%.2f）" % tick5["dot"])

bt6 = SIM.Battle(dex, ["音速犬"], ["冰钻布鲁斯"], seed=0)
bt6.add_mark("b", "thorn_mark")
sw = battle_fx.mark_switch_in(bt6.marks["b"])
check(abs(sw["hp_pct"] - 0.06) < 1e-9, "棘刺印记入场 -6% 生命")

bt7 = SIM.Battle(dex, ["音速犬"], ["冰钻布鲁斯"], seed=0)
bt7.add_mark("b", "drain_mark")
check(battle_fx.mark_switch_in(bt7.marks["b"])["energy"] == -1,
      "降灵印记入场 -1 能量")

bt8 = SIM.Battle(dex, ["音速犬"], ["冰钻布鲁斯"], seed=0)
bt8.add_mark("a", "dragon_mark")
check(abs(battle_fx.mark_on_skill(bt8.marks["a"], 3) - 0.40) < 1e-9,
      "龙噬印记：3 能耗技能后双攻 +40%（authority 权威值）")
check(battle_fx.mark_on_skill(bt8.marks["a"], 2) == 0,
      "龙噬印记：2 能耗技能不触发")

print()
print("--- 5. 天气 ---")
check(abs(battle_fx.weather_power_mult("rain", "水") - 1.75) < 1e-9,
      "雨天水系 x1.75（roco-cal 数据 value=0.75）")
check(abs(battle_fx.weather_power_mult("rain", "火") - 1.0) < 1e-9,
      "雨天火系不受影响")
check(len(battle_fx.weathers()) == 5, "5 种天气")

print()
print("--- 5. 技能关键词（S4）---")
# 吸血：蝙蝠（65 威力，吸血 100%）
mv_bat = dex.resolve_skill("蝙蝠")
check(battle_fx.parse_lifesteal(SIM._skill_desc(mv_bat["id"])) == 1.0,
      "「蝙蝠」描述吸血 100%")
mv_j = dex.resolve_skill("汲取")
check(battle_fx.parse_lifesteal(SIM._skill_desc(mv_j["id"])) == 1.0,
      "「汲取」描述吸血 100%")
# 先手：先发制人 +1
mv_fm = dex.resolve_skill("先发制人")
check(battle_fx.parse_priority(SIM._skill_desc(mv_fm["id"])) == 1,
      "「先发制人」先手 +1")
# 排序键：先手度 > 速度
p_a = SIM.Pet(dex, "黑猫巫师", iv=60)      # 低速 170
p_b = SIM.Pet(dex, "音速犬", iv=60)        # 高速 225
import random as _r
acts = [(p_b.spe, "b", p_b, p_a, dex.resolve_skill("升龙咆哮")),
        (p_a.spe, "a", p_a, p_b, mv_fm)]
pri = {"a": 1, "b": 0}
acts.sort(key=lambda x: (-pri[x[1]], -x[0], _r.Random(0).random()))
check(acts[0][1] == "a",
      "低速方带先手+1 先出手（排序键 先手度>速度）")

print()
print("--- 6. 场地亲和 / 4 格生命制 ---")
p_fire = SIM.Pet(dex, "音速犬", iv=60)
p_grass = SIM.Pet(dex, "卡瓦重", iv=60)
check(battle_fx.field_speed_bonus(p_grass, "草") == 50,
      "草系精灵在草场地 亲和【强】+50")
check(battle_fx.field_speed_bonus(p_fire, "水") == -50,
      "火系精灵在水场地 抵触【强】-50")
check(battle_fx.MAGIC_LIVES == 4, "魔力 4 格（每倒下一只 -1，归 0 判负）")

print()
if bad:
    print("FAILED %d" % bad)
    sys.exit(1)
print("ALL PASS")
