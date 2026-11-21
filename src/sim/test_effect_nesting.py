# -*- coding: utf-8 -*-
"""test_effect_nesting.py —— 防止双重嵌套 SkillEffect 让引擎崩溃。

背景（2026-11-21，S6 子代理发现）
--------------------------------
`skill_effects_generated.py` 里有 8 个技能被写成了：

    SE(timing, [SE(timing, [EffectTag(...)])])   <- 内层多包了一层

引擎的 `battle._iter_flat_tags` 只展平一层，遇到内层 SkillEffect 直接：

    AttributeError: 'SkillEffect' object has no attribute 'type'

实测受影响：富养化 / 风隐 / 雪球 / 架势 / 休息回复 / 徒长 / 根吸收 / 杠杆置换

修法：在 `skill_db._flatten_effects()` 加载时摊平（改生成文件会在
下次重新生成时丢掉）。

本测试守住这条：
    1. 全部 491 个技能的 effects 里不应有嵌套的 SkillEffect
    2. 这 8 个技能必须能正常执行
    3. 摊平后效果类型仍然正确

跑法：
    python src\\sim\\test_effect_nesting.py
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


# S6 实测发现的 8 个
NESTED = ["富养化", "风隐", "雪球", "架势", "休息回复", "徒长",
          "根吸收", "杠杆置换"]

# 摊平后应解析出的效果类型
EXPECT = {
    "富养化": "HEAL_ENERGY",
    "风隐": "FORCE_SWITCH",
    "雪球": "SELF_BUFF",
    "架势": "HEAL_HP",
    "休息回复": "HEAL_HP",
    "徒长": "HEAL_ENERGY",
    "根吸收": "HEAL_HP",
    "杠杆置换": "HEAL_ENERGY",
}


def kinds_of(skill):
    out = []
    for se in (getattr(skill, "effects", None) or []):
        for t in (getattr(se, "effects", None) or []):
            out.append(getattr(getattr(t, "type", None), "name", "?"))
    return out


def test_no_nesting_anywhere():
    """全部技能都不应有嵌套 SkillEffect。"""
    print("=== 1. 全部技能无嵌套 ===")
    import sim.skill_db as SD
    from sim.effect_models import SkillEffect
    db = SD.load_skills()
    bad = []
    for name, sk in db.items():
        for se in (getattr(sk, "effects", None) or []):
            for t in (getattr(se, "effects", None) or []):
                if isinstance(t, SkillEffect):
                    bad.append(name)
                    break
    check(not bad,
          "%d 个技能全部无嵌套%s" % (
              len(db), "" if not bad else "（仍有 %s）" % bad[:8]))


def test_nested_skills_flat():
    """S6 发现的 8 个必须已摊平且效果正确。"""
    print()
    print("=== 2. 8 个原嵌套技能已摊平 ===")
    import sim.skill_db as SD
    db = SD.load_skills()
    for name in NESTED:
        sk = db.get(name)
        if sk is None:
            check(False, "%s 不在技能库" % name)
            continue
        kinds = kinds_of(sk)
        want = EXPECT.get(name)
        ok = want in kinds
        check(ok, "%-10s %s%s" % (
            name, kinds, "" if ok else "（期望含 %s）" % want))


def test_execute_no_crash():
    """塞入这些技能执行一回合，不应崩。"""
    print()
    print("=== 3. 执行不崩 ===")
    import sim.adapter as A
    import sim.battle as B
    import sim.skill_db as SD
    from sim.models import BattleState

    db = SD.load_skills()
    a = A.build_pokemon("音速犬")
    crashed = []
    for name in NESTED:
        sk = db.get(name)
        if sk is None:
            continue
        b = A.build_pokemon("卡瓦重")
        b.skills = [sk]
        b.current_hp = b.hp
        st = BattleState(team_a=[a], team_b=[b])
        try:
            B.execute_full_turn(st, (1,), (0,))
        except Exception as e:
            crashed.append("%s: %s: %s" % (name, type(e).__name__, e))
    check(not crashed,
          "8 个技能都能执行%s" % ("" if not crashed else "（%s）" % crashed[:3]))


def test_flatten_preserves_timing():
    """摊平不能把 timing 弄丢。"""
    print()
    print("=== 4. 摊平保留 timing ===")
    import sim.skill_db as SD
    from sim.effect_models import SkillTiming
    db = SD.load_skills()
    bad = []
    for name in NESTED:
        sk = db.get(name)
        if sk is None:
            continue
        for se in (getattr(sk, "effects", None) or []):
            if not isinstance(getattr(se, "timing", None), SkillTiming):
                bad.append(name)
    check(not bad,
          "timing 全部有效%s" % ("" if not bad else "（%s）" % bad[:5]))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    test_no_nesting_anywhere()
    test_nested_skills_flat()
    test_execute_no_crash()
    test_flatten_preserves_timing()
    print()
    if FAILS:
        print("FAILED %d:" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS")
