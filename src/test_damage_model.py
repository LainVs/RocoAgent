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
# 2026-11-22：battle_fx 改成新口径（每类最多 1 个印记，见 test_battle_fx.py），
# "3 层攻击印记"不再是合法状态。这条测试改为验证新口径下的行为。
mm = battle_fx.mark_mods({"attack_mark": 1}, "升龙咆哮")
check(abs(mm["power_mult"] - 1.10) < 1e-9,
      "攻击印记 1 个 -> 威力 x%.2f（每类最多 1 个）" % mm["power_mult"])
mm0 = battle_fx.mark_mods({}, "升龙咆哮")
check(abs(mm0["power_mult"] - 1.0) < 1e-9, "没有印记 -> x1.0")
bt.set_weather("none")
b0 = SIM.calc_damage(bt.me, bt.foe, mv)
bt.set_marks("a", "attack_mark", 3)   # set_marks 内部会归一成 1 个
b1 = SIM.calc_damage(bt.me, bt.foe, mv)
check(b1 > b0 * 1.05, "攻击印记后伤害上升（%d -> %d）" % (b0, b1))

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
print("--- 6. 强化等级 / 属性克制（以用户实测为准）---")
# 用户实测：力量增效叠 10 层 = 物理攻击翻倍 -> 每层 +10%，线性叠加
check(abs(S.level_mult(1) - 1.1) < 1e-9,
      "强化 +1 级 = 1.10 倍（实际 %.2f）" % S.level_mult(1))
check(abs(S.level_mult(10) - 2.0) < 1e-9,
      "强化 +10 级 = 2.00 倍（实际 %.2f）" % S.level_mult(10))
check(abs(S.level_mult(-1) - 1 / 1.1) < 1e-9,
      "弱化 -1 级 = 1/1.1（实际 %.3f）" % S.level_mult(-1))
check(abs(S.type_effect("火", ["草", "虫"]) - 3.0) < 1e-9,
      "双克制是**加算** 1+1+1 = 3（实际 %.2f）" % S.type_effect("火", ["草", "虫"]))
check(abs(S.type_effect("火", ["水", "龙"]) - 0.25) < 1e-9,
      "双抵抗是乘算 0.5*0.5 = 0.25（实际 %.2f）"
      % S.type_effect("火", ["水", "龙"]))

print()
print("--- 7. 增益线性叠加（力量增效 10 层 = 翻倍）---")
pp = SIM.Pet(dex, "音速犬", iv=60, nature="开朗", invest=["hp", "atk", "spe"])
qq = SIM.Pet(dex, "冰钻布鲁斯", iv=60, nature="固执",
             invest=["hp", "atk", "spe"])
mvx = max(pp.moves, key=lambda m: m["power"])
d0 = SIM.calc_damage(pp, qq, mvx)
SIM.add_buff(pp, 0.1)
d1 = SIM.calc_damage(pp, qq, mvx)
check(abs(d1 / float(d0) - 1.10) < 0.02,
      "1 层：x%.2f（应 1.10）" % (d1 / float(d0)))
for _ in range(4):
    SIM.add_buff(pp, 0.1)
d5 = SIM.calc_damage(pp, qq, mvx)
check(abs(d5 / float(d0) - 1.50) < 0.02,
      "5 层：x%.2f（应 1.50）" % (d5 / float(d0)))
for _ in range(5):
    SIM.add_buff(pp, 0.1)
d10 = SIM.calc_damage(pp, qq, mvx)
check(abs(d10 / float(d0) - 2.00) < 0.02,
      "10 层：x%.2f（应 2.00，用户实测就是翻倍）" % (d10 / float(d0)))
SIM.add_buff(pp, 0.1)
check(abs(pp.atk_buff - SIM.BUFF_CAP) < 1e-9,
      "超过 10 层封顶在 +%.0f%%（实际 %.2f）"
      % (100 * SIM.BUFF_CAP, pp.atk_buff))

print()
print("--- 8. foe_hp.outgoing：自算伤害必须含本系 + 克制 ---")
# 这是 robot._calc_card_dmg 的唯一实现。旧版本在机器人里传
# foe_types=None 且 stab=1.0，等于把本系和克制全丢了；一张"本系+双克制"
# 的卡会被低估 3.75 倍，再被 0.35~2.5 的交叉校验判成"读数离谱"。
import foe_hp as FH  # noqa: E402

super_ = FH.outgoing("音速犬", "冰钻布鲁斯", "火云车")          # 火克冰
neutral = FH.outgoing("音速犬", "冰钻布鲁斯", "火云车",
                      foe_types=["普通"])
check(super_ == 2 * neutral,
      "火打冰是 2 倍（%s vs %s，允许 floor 差 0）" % (super_, neutral))
nostab = FH.outgoing("音速犬", "音速犬", "火云车", my_types=["水"])
stab = FH.outgoing("音速犬", "音速犬", "火云车")
check(abs(stab / float(nostab) - 1.25) < 0.02,
      "本系 1.25 倍（%d / %d = %.3f）" % (stab, nostab, stab / float(nostab)))
check(FH.outgoing("音速犬", "不存在", "火云车") is None,
      "未知对手 -> None（绝不编造）")
check(FH.outgoing("音速犬", "冰钻布鲁斯", "不存在的技能") is None,
      "未知技能 -> None")
# 显式传 HUD 直读的属性时，必须优先于查库
check(FH.outgoing("音速犬", "冰钻布鲁斯", "火云车",
                  foe_types=["水"]) < super_,
      "显式属性优先于查库（水抵抗火）")

print()
print("--- 9. battle_fx 印记新口径（另一会话改的，确认兼容）---")
# mark_mods 现在收 {mark_id: True}（每类最多 1 个），不再是 xxx_stacks。
# test_battle_fx.py 是新口径的权威测试；这里只确认旧调用方式的行为
# 文档化：normalize_marks 把层数字段翻译成"有一个"。
mm = battle_fx.mark_mods({"attack_mark": 1}, "升龙咆哮")
check(abs(mm["power_mult"] - 1.10) < 1e-9,
      "攻击印记 1 个 -> 威力 x%.2f（新口径：每类最多 1）" % mm["power_mult"])
check(battle_fx.normalize_marks({"attack_mark_stacks": 3}) == {"attack_mark": 1},
      "旧层数字段归一成 1 个")

print()
if bad:
    print("FAILED %d" % bad)
    sys.exit(1)
print("ALL PASS")
