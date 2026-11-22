# -*- coding: utf-8 -*-
"""S11 nrc 原语扩展测试（7 原语 / 10 特性）。

计划：docs/PLAN_S11_NRC.md
    向心力       永久威力 +30
    坠星/观星    守方每层星陨印记威力 +15%
    加个雪球/捉迷藏  施加冻结额外 +2 层
    浸润/浪潮    水系技能能耗 -1/-2
    煤渣         灼烧层数不衰减
    蚀刻         回合末敌方 2 层中毒 → 1 层中毒印记
    付给恶魔的赎价   击败敌方 → 敌方魔力 -1；被击败 → 自己 -1
"""
import sys
import types

sys.path.insert(0, "src")
import nrc_fx  # noqa: E402
import simulator as SIM  # noqa: E402

FAILS = []
D = SIM.Dex()


def check(cond, msg):
    print(("  \u2713 " if cond else "  \u2717 ") + msg)
    if not cond:
        FAILS.append(msg)


def stub(**kw):
    base = dict(trait="", name="\u6d4b", moves=[], hp=500, max_hp=500,
                alive=True, status={}, atk_buff=0.0, def_buff=0.0,
                taken_mult=1.0, energy=5, trait_atk_buff=0.0,
                trait_def_buff=0.0, trait_tmp_atk=0.0, trait_tmp_def=0.0,
                nrc_power_mult=1.0, nrc_power_bonus=0,
                atk=100, spa=100, dfn=100, spd=100, spe=100, types=[])
    base.update(kw)
    return type("P", (), base)()


def main():
    print("--- 1. \u5411\u5fc3\u529b\uff08PERMANENT_MOD \u5a01\u529b+30\uff09---")
    p = stub(trait="\u5411\u5fc3\u529b")
    evs = nrc_fx.trait_fire(p, None, "PASSIVE")
    print("  ", evs)
    check(getattr(p, "_perm_power_delta", 0) == 30, "\u5a01\u529b +30 \u6302\u8f7d")
    # calc_damage 消费
    mv = {"id": 0, "name": "x", "power": 50, "cost": 1, "type": "\u666e\u901a",
          "damage_type": 2}
    a1 = stub(atk=100)
    d1 = stub(dfn=100)
    dmg0 = SIM.calc_damage(a1, d1, mv)
    a1._perm_power_delta = 30
    dmg1 = SIM.calc_damage(a1, d1, mv)
    check(dmg1 > dmg0, "\u5411\u5fc3\u529b\u4f24\u5bb3\u63d0\u5347\uff08%d\u2192%d\uff09"
          % (dmg0, dmg1))

    print("--- 2. \u5760\u661f\uff08\u661f\u9695\u5370\u8bb0\u5a01\u529b\uff09---")
    p2 = stub(trait="\u5760\u661f")
    evs2 = nrc_fx.trait_fire(p2, None, "PASSIVE")
    check(getattr(p2, "_meteor_pct", 0) == 0.15, "\u5b9e\u4f8b\u5316 15%/\\u5c42")
    # 端到端：守方带 2 层星陨
    d2 = stub(dfn=100)
    d2._side = "b"
    a2 = stub(atk=100)
    a2._side = "a"
    a2._meteor_pct = 0.15
    a2._battle = types.SimpleNamespace(marks={"b": {"star_mark": 2}},
                                       weather="none")
    dmg_base = SIM.calc_damage(a2, d2, mv)
    a2b = stub(atk=100)
    a2b._side = "a"
    a2b._battle = types.SimpleNamespace(marks={"b": {}}, weather="none")
    dmg_nostar = SIM.calc_damage(a2b, d2, mv)
    print("  star2=%d nostar=%d" % (dmg_base, dmg_nostar))
    check(dmg_base > dmg_nostar, "2 \u5c42\u661f\u9695 \u2192 \u5a01\u529b +30%")

    print("--- 3. \u6d78\u6da6/\u6d6a\u6f6e\uff08\u80fd\u8017 -N\uff09---")
    bt = SIM.Battle(D, ["\u6c34\u84dd\u84dd"], ["\u97f3\u901f\u72ac"], seed=0,
                    cfg_a=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                            "max_hp": 9999, "moves": ["\u5929\u6d2a"]}],
                    cfg_b=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                            "max_hp": 9999}])
    bt.begin_turn()
    pet = bt.me
    evs3 = nrc_fx.trait_fire(pet, bt.foe, "ON_USE_SKILL",
                             {"mv_type": "\u6c34", "mv_cost": 7})
    print("  ", evs3)
    check(getattr(pet, "_elem_cost_reduce", {}).get("\u6c34") == 1,
          "\u6d78\u6da6\u6302\u8f7d\uff08\u6c34 -1\uff09")
    # 天洪水系 cost=7 → eff_cost 6
    check(bt.eff_cost("a", pet.moves[0]) == 7 - 1,
          "eff_cost \u6d88\u8d39\uff087\u2192%d\uff09" % bt.eff_cost("a", pet.moves[0]))

    print("--- 4. \u7164\u6e23\uff08\u707c\u70e7\u4e0d\u8870\u51cf\uff09---")
    p4 = stub(trait="\u7164\u6e23\u8349")
    p4.status = {"burn": 4}
    nrc_fx.trait_fire(p4, None, "PASSIVE")
    check(p4._burn_no_decay is True, "\u6302\u8f7d")
    evs4 = nrc_fx.status_tick(p4)
    print("  ", evs4)
    check(p4.status["burn"] == 4, "\u707c\u70e7\u5c42\u6570\u4e0d\u8870\u51cf\uff084\u2192%d\uff09"
          % p4.status["burn"])
    # 对照：无特性衰减
    p4b = stub()
    p4b.status = {"burn": 4}
    nrc_fx.status_tick(p4b)
    check(p4b.status["burn"] == 2, "\u65e0\u7279\u6027\u2192\u8870\u51cf\uff084\u21922\uff09")

    print("--- 5. \u8680\u523b\uff08\u4e2d\u6bd2\u2192\u5370\u8bb0\uff09---")
    bt5 = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u97f3\u901f\u72ac"], seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}],
                     cfg_b=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt5.begin_turn()
    bt5.me.trait = "\u8680\u523b"          # 蚀刻载体
    foe5 = bt5.foe
    foe5.status["poison"] = 5
    evs5 = nrc_fx.trait_fire(bt5.me, foe5, "ON_TURN_END")
    print("  ", evs5)
    check(foe5.status["poison"] == 1, "5 \u5c42\u4e2d\u6bd2 -4\uff082 \u5c42:1 \u5370\u8bb0\uff09")
    check((bt5.marks.get("b") or {}).get("poison_mark") == 2,
          "\u5370\u8bb0 +2 \u5c42")

    print("--- 6. \u4ed8\u7ed9\u6076\u9b54\u7684\u8d4e\u4ef7\uff08\u9b54\u529b -1\uff09---")
    bt6 = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u97f3\u901f\u72ac"], seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}],
                     cfg_b=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt6.begin_turn()
    atk6, dfn6 = bt6.me, bt6.foe
    atk6.trait = "\u4ed8\u7ed9\u6076\u9b54\u7684\u8d4e\u4ef7"
    dfn6.trait = "\u4ed8\u7ed9\u6076\u9b54\u7684\u8d4e\u4ef7"
    dfn6.hp = 0                 # ON_KILL 时机：foe 已倒下
    m0 = bt6.magic["b"]
    evs6 = nrc_fx.trait_fire(atk6, dfn6, "ON_KILL")
    print("  ", evs6)
    check(bt6.magic["b"] == m0 - 1, "\u51fb\u6740 \u2192 \u654c\u65b9\u9b54\u529b -1")
    m0a = bt6.magic["a"]
    m0b = bt6.magic["b"]
    dfn6.hp = 0                 # ON_FAINT 时机：自己已倒下
    evs6b = nrc_fx.trait_fire(dfn6, atk6, "ON_FAINT")
    # ON_FAINT：被击败者（dfn6，b 侧）自己魔力 -1
    check(bt6.magic["b"] == m0b - 1, "\u88ab\u51fb\u8d25 \u2192 \u81ea\u5df1\u9b54\u529b -1\uff08b \u4fa7\uff09")

    print("--- 7. \u51bb\u7ed3\u9644\u52a0\u5c42 ---")
    p7 = stub(trait="\u52a0\u4e2a\u96ea\u7403")
    evs7 = nrc_fx.trait_fire(p7, None, "PASSIVE")
    check(getattr(p7, "_freeze_extra", 0) == 2, "\u5b9e\u4f8b\u5316 +2")
    tgt = stub()
    nrc_fx._bump_status(tgt, "freeze", 1)
    # 模拟 Battle 冻结附加（加个雪球载体施加）
    owner = stub(trait="\u52a0\u4e2a\u96ea\u7403")
    nrc_fx.trait_fire(owner, None, "PASSIVE")
    evs7b = nrc_fx._bump_status(tgt, "freeze", 1)
    _fe = int(getattr(owner, "_freeze_extra", 0) or 0)
    if _fe:
        nrc_fx._bump_status(tgt, "freeze", _fe)
    check(tgt.status.get("freeze", 0) == 4, "\u51bb\u7ed3 1+1+2=4 \u5c42\uff08\u5b9e\u9645 %s\uff09"
          % tgt.status.get("freeze"))

    print()
    if FAILS:
        print("FAILED %d" % len(FAILS))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
