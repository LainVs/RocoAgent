# -*- coding: utf-8 -*-
"""test_sim_effects.py —— 验证模拟器效果移植的正确性。

覆盖 2026-11-21 移植时踩的坑：
    1. 六维必须与游戏面板 30/30 一致（5 只 × 6 维）
    2. 技能数值以我们库为准（NRC 的月蚀/撞鬼数值是错的）
    3. NRC 里 effects 为空的技能要用手动配置补上
    4. 我们队伍 19 个技能全部有效果
    5. 两个新增的 condition（self_hp_gt_80 / enemy_hp_lt_50）可用
    6. 悲悯的 scope 是 all（算双方力竭数）

跑法：
    python src\\sim\\test_sim_effects.py
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


# 游戏面板实测值（用户 2026-11-21 截图）
OBSERVED = {
    "音速犬":     (366, 221, 97, 171, 150, 260),
    "冰钻布鲁斯": (345, 220, 80, 185, 157, 192),
    "卡瓦重":     (352, 168, 73, 147, 140, 280),
    "银月狼王":   (417, 234, 116, 186, 168, 273),
    "恶魔狼":     (417, 218, 104, 160, 129, 260),
}


def effect_kinds(skill):
    kinds = []
    for se in (getattr(skill, "effects", None) or []):
        for t in (getattr(se, "effects", None) or []):
            kinds.append(getattr(getattr(t, "type", None), "name", "?"))
    return kinds


def test_stats():
    print("=== 1. 六维与游戏面板一致（30 项）===")
    import adapter
    bad = 0
    for name, want in OBSERVED.items():
        p = adapter.build_pokemon(name)
        got = (p.hp, p.attack, p.sp_attack, p.defense, p.sp_defense, p.speed)
        ok = got == want
        if not ok:
            bad += 1
        check(ok, "%s %s%s" % (name, got, "" if ok else " != %s" % (want,)))
    check(bad == 0, "30 项全部一致（不一致 %d 只）" % bad)


def test_skill_values():
    print()
    print("=== 2. 技能数值以我们库为准 ===")
    import adapter
    # NRC 的 nrc.db 里这两个是 power=40 cost=2
    cases = [
        ("银月狼王", "月蚀", 130, 5),
        ("银月狼王", "撞鬼", 120, 4),
    ]
    for pet, sname, want_p, want_c in cases:
        p = adapter.build_pokemon(pet)
        sk = next((s for s in p.skills if s.name == sname), None)
        check(sk is not None, "%s 有技能 %s" % (pet, sname))
        if sk:
            check(sk.power == want_p,
                  "%s power=%d（期望 %d，NRC 里是 40）" % (
                      sname, sk.power, want_p))
            check(sk.energy_cost == want_c,
                  "%s cost=%d（期望 %d，NRC 里是 2）" % (
                      sname, sk.energy_cost, want_c))


def test_effects_present():
    print()
    print("=== 3. 我们队伍 19 个技能全部有效果 ===")
    import adapter
    no_effect = []
    total = 0
    for name in OBSERVED:
        p = adapter.build_pokemon(name)
        for s in p.skills:
            total += 1
            kinds = effect_kinds(s)
            if not kinds:
                no_effect.append("%s/%s" % (name, s.name))
    check(not no_effect,
          "%d 个技能全部有效果%s" % (
              total, "" if not no_effect else "（缺 %s）" % no_effect))


def test_patch_effects():
    print()
    print("=== 4. 本项目补的 5 个效果 ===")
    import adapter
    expect = {
        "丢冰块": "ENEMY_DEBUFF",
        "筛管奔流": "POWER_DYNAMIC",
        "惊吓盒子": "ENEMY_LOSE_ENERGY",
        "月蚀": "ENERGY_COST_DYNAMIC",
        "撞鬼": "DAMAGE",
    }
    # 找到每个技能所属的精灵
    owner = {}
    for name in OBSERVED:
        p = adapter.build_pokemon(name)
        for s in p.skills:
            owner[s.name] = (name, s)
    for sname, want in expect.items():
        if sname not in owner:
            check(False, "%s 不在队伍技能里" % sname)
            continue
        _, sk = owner[sname]
        kinds = effect_kinds(sk)
        check(want in kinds,
              "%s 含 %s（实际 %s）" % (sname, want, kinds))


def test_new_conditions():
    print()
    print("=== 5. 新增的 condition 能跑 ===")
    from sim.effect_models import E, EffectTag, SkillTiming, SkillEffect
    from sim.effect_engine import EffectExecutor
    from sim.models import Pokemon, Skill, BattleState, Type, SkillCategory

    def mk(hp_cur, hp_max):
        p = Pokemon(name="t", pokemon_type=Type.NORMAL, hp=hp_max,
                    attack=100, defense=100, sp_attack=100, sp_defense=100,
                    speed=100, skills=[])
        p.current_hp = hp_cur
        return p

    sk = Skill(name="测试", skill_type=Type.NORMAL,
               category=SkillCategory.PHYSICAL, power=80, energy_cost=2)
    sk.effects = [
        SkillEffect(timing=SkillTiming.PRE_USE, effects=[
            EffectTag(E.POWER_DYNAMIC, {"condition": "self_hp_gt_80",
                                        "bonus": 75})]),
        SkillEffect(timing=SkillTiming.ON_USE,
                    effects=[EffectTag(E.DAMAGE, {})]),
    ]

    # 血 90% -> 应加成
    a = mk(90, 100)
    b = mk(100, 100)
    st = BattleState(team_a=[a], team_b=[b])
    r = EffectExecutor.execute_skill(st, a, b, sk, sk.effects, team="a")
    bonus = r.get("_power_bonus", 0)
    check(bonus == 75, "self_hp_gt_80 在 90%% 血时 +75（实际 %s）" % bonus)

    # 血 50% -> 不加成
    a2 = mk(50, 100)
    b2 = mk(100, 100)
    st2 = BattleState(team_a=[a2], team_b=[b2])
    r2 = EffectExecutor.execute_skill(st2, a2, b2, sk, sk.effects, team="a")
    bonus2 = r2.get("_power_bonus", 0)
    check(bonus2 == 0, "self_hp_gt_80 在 50%% 血时 +0（实际 %s）" % bonus2)


def test_beimin_scope():
    print()
    print("=== 6. 悲悯 scope=all（算双方力竭）===")
    from sim.effect_data import ABILITY_EFFECTS
    effs = ABILITY_EFFECTS.get("悲悯") or []
    scope = None
    for ae in effs:
        for t in (getattr(ae, "effects", None) or []):
            if getattr(getattr(t, "type", None), "name") == "FAINTED_ALLIES_BUFF":
                scope = (t.params or {}).get("scope")
    check(scope == "all",
          "悲悯的 scope=all（实际 %r，上游是 allies）" % scope)


def test_selfplay_runs():
    print()
    print("=== 7. 自对弈能跑完 ===")
    import selfplay
    w, t = selfplay.run_one(verbose=False, seed=0)
    check(w in ("a", "b"), "分出胜负: %s" % w)
    check(0 < t <= 80, "回合数 %d 在合理范围" % t)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    test_stats()
    test_skill_values()
    test_effects_present()
    test_patch_effects()
    test_new_conditions()
    test_beimin_scope()
    test_selfplay_runs()
    print()
    if FAILS:
        print("FAILED %d:" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS")
