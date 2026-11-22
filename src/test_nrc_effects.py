# -*- coding: utf-8 -*-
"""test_nrc_effects.py - nrc-ai 手写效果表接入的回归。

三条：
  1. 特性 `张弛有度`（上岸蛙）入场给双防增益（SELF_BUFF）
  2. 异常状态回合末扣血（status_tick）
  3. 未实现的原语不会被静默吞掉（coverage 里要能看到 skipped）

usage: python test_nrc_effects.py
"""
import os
import sys

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


print("--- 1. 特性 SELF_BUFF（张弛有度）---")
dex = SIM.Dex()
p = SIM.Pet(dex, "上岸蛙", iv=60)
print("     特性=%s  roco-cal 可用规则=%d  nrc 有=%s"
      % (p.trait, len(trait_lib.supported_rules(p.trait)),
         nrc_fx.has_trait(p.trait)))
q = SIM.Pet(dex, "冰钻布鲁斯", iv=60)
check(not trait_lib.supported_rules(p.trait),
      "这个特性 roco-cal 没有可用规则（所以该走 nrc）")
ctx = {}
evs = (nrc_fx.trait_fire(p, q, "ON_ENTER", ctx)
       + nrc_fx.trait_fire(p, q, "ON_BATTLE_START", ctx)
       + nrc_fx.trait_fire(p, q, "PASSIVE", ctx))
check(p.def_buff > 0 or p.atk_buff > 0,
      "入场后拿到增益 atk=%.2f def=%.2f（%s）"
      % (p.atk_buff, p.def_buff, evs))

print()
print("--- 2. 异常状态回合末扣血 ---")
p2 = SIM.Pet(dex, "上岸蛙", iv=60)
p2.hp = p2.max_hp
p2.status = {"poison": 2}
before = p2.hp
ev = nrc_fx.status_tick(p2)
check(p2.hp < before, "中毒 2 层后 %d -> %d（%s）"
      % (before, p2.hp, ev[0] if ev else "无事件"))

print()
print("--- 3. 未实现原语要能看到 ---")
# `圣火骑士` 用的是 COUNTER_SUCCESS_DOUBLE_DAMAGE（本模拟器没有「应对」判定），
# 必须被记成 skipped，而不是静默忽略
p3 = SIM.Pet(dex, "霹雳迪迪", iv=60)
# 直接用效果表里的特性名建个替身，避免被「同名多行物种」干扰
import types  # noqa: E402
stub = types.SimpleNamespace(trait="圣火骑士", name="替身", moves=[],
                             hp=1, max_hp=1, alive=True, status={},
                             atk_buff=0.0, def_buff=0.0, taken_mult=1.0,
                             energy=0)
n_before = sum((nrc_fx.coverage()["skipped"] or {}).values())
nrc_fx.trait_fire(stub, q, "ON_ENTER", {})
cov = nrc_fx.coverage()
n_after = sum((cov["skipped"] or {}).values())
print("     traits=%d skills=%d applied=%d 类 skipped=%d 类"
      % (cov["traits"], cov["skills"], len(cov["applied"]),
         len(cov["skipped"])))
check(cov["traits"] >= 100 and cov["skills"] >= 100,
      "效果表已加载（特性 %d / 技能 %d）" % (cov["traits"], cov["skills"]))
check(n_after > n_before,
      "未实现的原语被计入 skipped（%d -> %d）" % (n_before, n_after))

print()
if bad:
    print("FAILED %d" % bad)
    sys.exit(1)
print("ALL PASS")
