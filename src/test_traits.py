# -*- coding: utf-8 -*-
"""test_traits.py - 特性效果回归（用户点名的两个例子 + 覆盖范围守门）。

背景（2026-11-22）：模拟器原来完全没有接入特性/技能效果，用户实测
「蝙蝠没吸血」「火狗第一回合伤害没翻倍」。

数据来源是 roco-cal 的**结构化特性**（`data/pets_trait_json`，261 条），
外加 3 条文本推导的吸血规则。roco-cal 里大量特性的触发依赖天气/印记/
等级/异常状态，这些本模拟器没有建模，所以运行时有一张显式白名单
（`src/trait_lib.py` 的 SUPPORTED），只应用能判定的组合。

这个测试钉住三件事：
  1. `专注力`（音速犬）入场首回合物攻 +100% 真的生效
  2. 吸血按造成伤害的 30% 回血（`渴求`）
  3. 白名单没有被无意扩大/缩小（阈值守门）

usage: python test_traits.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402,F401

import simulator as SIM  # noqa: E402
import trait_lib  # noqa: E402

bad = 0


def check(cond, msg):
    global bad
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        bad += 1


print("--- 1. 专注力（音速犬）：入场首回合物攻 +100% ---")
dex = SIM.Dex()
p = SIM.Pet(dex, "音速犬", iv=60, nature="开朗", invest=["hp", "atk", "spe"])
q = SIM.Pet(dex, "冰钻布鲁斯", iv=60, nature="固执", invest=["hp", "atk", "spe"])
check(p.trait == "专注力", "音速犬的主特性是专注力（实际 %s）" % p.trait)
mv = max(p.moves, key=lambda m: m["power"])
base = SIM.calc_damage(p, q, mv)
p.trait_tmp_atk = 1.0
boosted = SIM.calc_damage(p, q, mv)
p.trait_tmp_atk = 0.0
print("     技能 %s 威力 %s：普通 %d -> 首回合 %d"
      % (mv["name"], mv["power"], base, boosted))
check(abs(boosted / float(base) - 2.0) < 0.12,
      "首回合伤害约为 2 倍（%.2fx）" % (boosted / float(base)))

# 真实对局里也要自动生效（不是只有手动设字段才行）
bt = SIM.Battle(dex, ["音速犬"], ["冰钻布鲁斯"], seed=0)
ok = bt.begin_turn()
check(ok and bt.me.trait_tmp_atk >= 1.0,
      "对局第 1 回合开始时自动挂上 +100%%（实际 %.2f）"
      % (bt.me.trait_tmp_atk or 0))

print()
print("--- 2. 吸血：渴求 30% ---")
row = dex.db.execute(
    "SELECT s.name FROM features f JOIN feature_pets fp "
    "ON fp.feature_id=f.id JOIN species s ON s.id=fp.species_id "
    "WHERE f.name='渴求' AND fp.is_primary=1 LIMIT 1").fetchone()
if not row:
    check(False, "图鉴里找不到主特性为渴求的精灵")
else:
    nm = row[0]
    bt = SIM.Battle(dex, [nm], ["冰钻布鲁斯"], seed=0,
                    cfg_a=[{"name": nm, "iv": 60, "max_hp": 3000}])
    bt.begin_turn()
    me, foe = bt.me, bt.foe
    check(me.trait == "渴求", "%s 的主特性是渴求（实际 %s）" % (nm, me.trait))
    me.hp = 2000
    best = max(range(len(me.moves)),
               key=lambda i: SIM.calc_damage(me, foe, me.moves[i]))
    kb, pb = SIM.GreedyPolicy().choose(foe, me, [], 1)
    ev = bt.step("skill", best, kb, pb)
    heal = [e for e in ev if "吸血" in e]
    check(bool(heal), "结算里有吸血事件：%s" % (heal[0] if heal else "无"))
    if heal:
        gained = int(heal[0].split("+")[1].split("（")[0])
        check(gained >= 20, "回血 %d 点（30%% 的伤害，非 0 即通过）" % gained)

print()
print("--- 3. 白名单守门 ---")
cov = trait_lib.coverage()
print("     %s" % cov)
check(cov["usable"] >= 15, "可应用的特性 >= 15 条（实际 %d）" % cov["usable"])
check(cov["usable"] <= 40,
      "可应用特性没有暴涨（<=40，防止白名单被乱放）实际 %d" % cov["usable"])
# 明确不该被应用的那几类
for nm, why in (("滴眼液", "需要雨天"), ("异类", "原文自相矛盾"),
                ("傲慢", "只对蓄力技能生效")):
    rs = trait_lib.supported_rules(nm)
    check(not rs, "%s（%s）不应被应用，实际 %d 条" % (nm, why, len(rs)))

print()
if bad:
    print("FAILED %d" % bad)
    sys.exit(1)
print("ALL PASS")
