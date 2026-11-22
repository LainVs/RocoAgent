# -*- coding: utf-8 -*-
"""test_damage_model.py - 伤害模型回归：官方公式 / 天气 / 印记 / 防御增益。

★ 这个测试的第一条断言是**外部标定**：洛克PVP助手的运行时里有一段
  可验算的注释（`producer-session-runtime.js` 的 `Xo()` 附近）：

      217/160，卡面威力 150，属性系数 0.5，
      带本系 1.25 -> 114；不带 -> 91

  把同样的数字代进我们的公式，必须得到 114 / 91。只要这条过，就说明
  「卡面伤害」和我们的模拟器是同一套算术（本作 PVP 无伤害浮动、
  卡面不含暴击，所以卡面数字 = 真打出去的伤害）。

其余断言覆盖：天气倍率、印记层数、防御类变化技能、减伤。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402,F401

import battle_fx  # noqa: E402
import simulator as SIM  # noqa: E402
import statmodel as S  # noqa: E402
import trait_lib  # noqa: E402

bad = 0


def check(cond, msg):
    global bad
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        bad += 1


print("--- 1. 官方公式标定（外部证据）---")
got = S.damage(217, 160, 150, type_eff=0.5, stab=1.25)
check(got == 114, "217/160 威力150 克制0.5 本系1.25 -> %d（应 114）" % got)
got2 = S.damage(217, 160, 150, type_eff=0.5, stab=1.0)
check(got2 == 91, "同上不带本系 -> %d（应 91）" % got2)
check(abs(battle_fx.LEVEL_TERM - 37.0) < 1e-9,
      "等级项 = 60*0.45+10 = 37（实际 %s）" % battle_fx.LEVEL_TERM)
# roco-cal 引擎必须给出同一个数
import damage_v2 as DMG  # noqa: E402
check(DMG.damage(217, 160, 150, type_eff=0.5, stab=1.25) == 114,
      "roco-cal 引擎同参数也是 114")

print()
print("--- 2. 天气 ---")
rain_w = battle_fx.weather_power_mult("rain", "水")
rain_f = battle_fx.weather_power_mult("rain", "火")
check(abs(rain_w - 1.75) < 1e-9, "雨天水系 x%.2f（数据 value=0.75）" % rain_w)
check(abs(rain_f - 1.0) < 1e-9, "雨天火系 x%.2f（不该受影响）" % rain_f)
dex = SIM.Dex()
bt = SIM.Battle(dex, ["音速犬"], ["冰钻布鲁斯"], seed=0)
mv = max(bt.me.moves, key=lambda m: m["power"])
base = SIM.calc_damage(bt.me, bt.foe, mv)
bt.set_weather("rain")
same = SIM.calc_damage(bt.me, bt.foe, mv)
check(base == same, "雨天对非水系技能无影响（%d == %d）" % (base, same))

print()
print("--- 3. 印记 ---")
mm = battle_fx.mark_mods({"attack_mark_stacks": 3}, "升龙咆哮")
check(abs(mm["power_mult"] - 1.3) < 1e-9,
      "攻击印记 3 层 -> 威力 x%.2f（每层 +10%%）" % mm["power_mult"])
mm0 = battle_fx.mark_mods({}, "升龙咆哮")
check(abs(mm0["power_mult"] - 1.0) < 1e-9, "没有印记 -> x1.0")
bt.set_weather("none")
b0 = SIM.calc_damage(bt.me, bt.foe, mv)
bt.set_marks("a", "attack_mark_stacks", 3)
b1 = SIM.calc_damage(bt.me, bt.foe, mv)
check(b1 > b0 * 1.25, "3 层攻击印记后伤害上升（%d -> %d）" % (b0, b1))

print()
print("--- 4. 防御类变化技能 ---")
v = SIM.skill_buff("防御")
check(bool(v) and v.get("def"), "「防御」的官方数值读取成功：%s" % v)
check(SIM.skill_buff("力量增效") and SIM.skill_buff("力量增效")["atk"] > 0,
      "「力量增效」攻击增益 %s" % SIM.skill_buff("力量增效"))
# 走一遍真实回合：我方用「防御」，之后受到的伤害应该更低
p = SIM.Pet(dex, "冰钻布鲁斯", iv=60)
q = SIM.Pet(dex, "音速犬", iv=60, nature="开朗", invest=["hp", "atk", "spe"])
foe_move = max(q.moves, key=lambda m: m["power"])
before = SIM.calc_damage(q, p, foe_move)
p.def_buff = SIM.skill_buff("防御")["def"]
after = SIM.calc_damage(q, p, foe_move)
check(after < before, "防御 +%d%% 后受击伤害 %d -> %d"
      % (round(100 * p.def_buff), before, after))

print()
print("--- 5. 数据文件不再依赖 Downloads ---")
import io  # noqa: E402
import json  # noqa: E402
p2 = os.path.join(paths.ROOT, "data", "external", "rkpp-official",
                  "skill_buff_calc.json")
check(os.path.exists(str(p2)), "skill_buff_calc.json 已入库")
if os.path.exists(str(p2)):
    d = json.load(io.open(str(p2), encoding="utf-8"))
    check(d.get("count", 0) > 30, "含 %d 个变化技能的增益" % d.get("count", 0))

print()
if bad:
    print("FAILED %d" % bad)
    sys.exit(1)
print("ALL PASS")
