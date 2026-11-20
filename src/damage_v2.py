# -*- coding: utf-8 -*-
"""damage_v2.py —— 伤害引擎（移植自 roco-cal 的 damresult.py）。

为什么换
--------
roco-cal 的引擎比我们的 statmodel 完整得多：

    statmodel             damage_v2（roco-cal）
    ------------------    ----------------------
    单一公式              完整乘区
    —                     攻击/防御 buff（增减分开算）
    —                     威力倍率 / 固定加成
    —                     减伤数组（彼此乘算）
    —                     连击 / 使用次数
    —                     特性修正（stat_buff / power_multiplier / …）
    —                     天气 / 印记 / 愿力
    —                     性格反推（±20%/-10% 的候选枚举）
    —                     区间枚举（攻/防/血三种场景）

关键验证：`calculate_damage(atk=244, defense=196, skill_power=140,
type_bonus=0.25)` 返回 `(196, None, 175)` ——
伤害 196、有效威力 175，与我们 statmodel 的结果**完全一致**。

兼容层设计
----------
对外提供与 statmodel 相同的 `damage()` 签名，内部走 roco-cal。
引擎不可用时自动退回 statmodel（fail-safe）。
"""
from __future__ import annotations

import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_ROOT = os.path.join(ROOT, "data", "external", "roco-cal")

_LOCK = threading.Lock()
_ENGINE = None
_STATMODEL = None


def _engine():
    """惰性导入 roco-cal 的引擎。失败返回 None。"""
    global _ENGINE
    with _LOCK:
        if _ENGINE is not None:
            return _ENGINE if _ENGINE is not False else None
    try:
        if ENGINE_ROOT not in sys.path:
            sys.path.insert(0, ENGINE_ROOT)
        import core.damresult as D
        with _LOCK:
            _ENGINE = D
        return D
    except Exception:
        with _LOCK:
            _ENGINE = False
        return None


def _fallback():
    global _STATMODEL
    if _STATMODEL is None:
        import statmodel
        _STATMODEL = statmodel
    return _STATMODEL


def available():
    return _engine() is not None


# ---------------------------------------------------------------- 主接口

def damage(atk, dfn, power, type_eff=1.0, stab=1.0, trait=1.0,
           extra=1.0, reduce=1.0, hits=1, **kw):
    """与 statmodel.damage 同签名的兼容接口。

    roco-cal 的引擎用 type_bonus（本系加成，默认 0.25）和 advantage（克制倍率）
    两个独立参数，这里做映射：
        stab      -> type_bonus = stab - 1
        type_eff  -> advantage
        hits      -> combo
        reduce    -> damage_reductions
    """
    D = _engine()
    if D is None:
        return _fallback().damage(atk, dfn, power, type_eff=type_eff,
                                  stab=stab, trait=trait, extra=extra,
                                  reduce=reduce, hits=hits)
    try:
        dmg, _detail, _eff = D.calculate_damage(
            atk=atk,
            defense=dfn,
            skill_power=power,
            type_bonus=(stab - 1.0),
            advantage=type_eff,
            combo=max(1, int(hits)),
            damage_reductions=[1 - reduce] if reduce < 1 else None,
            damage_multiplier=[trait - 1] if trait != 1 else None,
        )
        return int(dmg)
    except Exception:
        return _fallback().damage(atk, dfn, power, type_eff=type_eff,
                                  stab=stab, trait=trait, extra=extra,
                                  reduce=reduce, hits=hits)


def full(atk, dfn, power, *, atk_buff=0.0, def_buff=0.0,
         power_multipliers=None, power_bonus=0, damage_reductions=None,
         damage_multipliers=None, combo=None, usage_time=1,
         type_bonus=0.25, advantage=1.0, atk_lv=60):
    """完整接口，暴露 roco-cal 引擎的全部参数。

    返回 (总伤害, 明细文本或 None, 有效威力)。
    引擎不可用时返回 (0, "engine unavailable", 0)。
    """
    D = _engine()
    if D is None:
        return 0, "engine unavailable", 0
    try:
        return D.calculate_damage(
            atk=atk, defense=dfn, skill_power=power,
            atk_buff=atk_buff, enemy_def_buff=def_buff,
            power_multiplier=power_multipliers, power_bonus=power_bonus,
            damage_reductions=damage_reductions,
            damage_multiplier=damage_multipliers,
            combo=combo, usage_time=usage_time,
            type_bonus=type_bonus, advantage=advantage, atk_lv=atk_lv)
    except Exception as e:
        return 0, "%s: %s" % (type(e).__name__, e), 0


def with_trait(atk, dfn, power, trait_name, ctx=None,
               type_eff=1.0, stab=1.0, combo=None):
    """带特性修正的伤害。

    特性修正来自 trait_effect.modifiers()，按 roco-cal 的语义合并：
        stat_buff          -> atk_buff
        power_multipliers  -> power_multiplier（列表，相加后 1+sum）
        power_bonus        -> power_bonus
        damage_multipliers -> damage_multiplier
        damage_reductions  -> damage_reductions
        combo_plus         -> combo 增加
    """
    import trait_effect
    m = trait_effect.modifiers(trait_name, ctx)
    c = combo
    if m["combo_plus"]:
        c = (c or 1) + m["combo_plus"]
    return full(atk, dfn, power,
                atk_buff=m["atk_buff"],
                power_multipliers=m["power_multipliers"] or None,
                power_bonus=m["power_bonus"],
                damage_reductions=m["damage_reductions"] or None,
                damage_multipliers=m["damage_multipliers"] or None,
                combo=c,
                type_bonus=(stab - 1.0), advantage=type_eff)


# ---------------------------------------------------------------- 自检

def _selftest():
    import statmodel as S
    print("引擎可用:", available())
    if not available():
        print("!! roco-cal 引擎导入失败，会退回 statmodel")
        return 1

    cases = [
        # (atk, def, power, type_eff, stab)
        (244, 196, 140, 1.0, 1.25),
        (300, 175, 105, 1.0, 1.25),
        (244, 196, 100, 2.0, 1.0),
        (200, 200, 80, 0.5, 1.0),
    ]
    print("\n%-28s %10s %10s %s" % ("输入", "v2(roco-cal)", "statmodel", "一致"))
    print("-" * 62)
    ok = 0
    for atk, dfn, pw, te, st in cases:
        a = damage(atk, dfn, pw, type_eff=te, stab=st)
        b = S.damage(atk, dfn, pw, type_eff=te, stab=st)
        same = (a == b)
        ok += 1 if same else 0
        print("atk=%d def=%d pw=%d eff=%.1f stab=%.2f  %10d %10d  %s"
              % (atk, dfn, pw, te, st, a, b, "OK" if same else "DIFF"))

    print("\n=== 完整接口（带 buff / 特性）===")
    d, detail, eff = full(244, 196, 140, atk_buff=0.2,
                          power_multipliers=[0.2], type_bonus=0.25)
    print("  atk_buff=0.2 power_mult=0.2 -> dmg=%d eff_power=%d" % (d, eff))
    d2, _x, eff2 = with_trait(244, 196, 140, "三鼓作气",
                              {"stacks": {"skill_cost_3": 2}},
                              type_eff=1.0, stab=1.25)
    print("  三鼓作气 x2 -> dmg=%d eff_power=%d" % (d2, eff2))

    print("\n%d/%d 与 statmodel 一致" % (ok, len(cases)))
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    sys.exit(_selftest())
