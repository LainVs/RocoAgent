# -*- coding: utf-8 -*-
"""nrc_fx.py —— nrc-ai 手写效果表（`data/nrc_effects.json`）的运行时。

覆盖范围（2026-11-22）
---------------------
nrc-ai 有 139 个技能 / 169 个特性的**手写语义**，共用到 175 种效果原语。
本模块只实现能映射到现有状态的那一批（状态 = 血量/能量/攻防增益/减伤/
异常状态），其余原语一律**不应用并计数**（`coverage()` 可查），宁可少算。

为什么不和 roco-cal 的特性规则叠加
----------------------------------
`专注力` 两个源里都有（roco-cal 的 `first_turn + stat_buff atk 1.0`，
nrc-ai 的 `ON_BATTLE_START + SELF_BUFF atk 1.0`）。两边都应用就会变成
**+200%**。所以规则是：

    roco-cal 的特性规则能应用 -> 只用 roco-cal 的（它带条件判定）
    否则 -> 用 nrc-ai 的

技能同理：属性增益优先用官方数值（`simulator.skill_buff`，来自
`skill_buff_calc.json`），nrc-ai 只补它没有的部分（回血/上状态/上印记）。
"""
from __future__ import annotations

import io
import json
import os
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "nrc_effects.json")

# 异常状态：名字 -> 每回合末按最大血量百分比扣血
STATUS_DOT = {"poison": 0.05, "burn": 0.08}

_CACHE = {"data": None}
_SKIP = Counter()
_APPLIED = Counter()


def _load():
    if _CACHE["data"] is not None:
        return _CACHE["data"]
    try:
        with io.open(PATH, encoding="utf-8") as fh:
            _CACHE["data"] = json.load(fh)
    except Exception:
        _CACHE["data"] = {"traits": {}, "skills": {}}
    return _CACHE["data"]


def trait_rows(name):
    return (_load().get("traits") or {}).get(name) or []


def skill_rows(name):
    return (_load().get("skills") or {}).get(name) or []


def has_trait(name):
    return bool(trait_rows(name))


def coverage():
    d = _load()
    return {"traits": len(d.get("traits") or {}),
            "skills": len(d.get("skills") or {}),
            "applied": dict(_APPLIED), "skipped": dict(_SKIP)}


# ---------------------------------------------------------------- 基础操作
def _norm_stat(params):
    """SELF_BUFF 的键 -> (atk_buff 增量, def_buff 增量)。

    nrc-ai 用 atk/spatk/def/spdef/speed，我们的模型只有攻/防两槽
    （双攻合并、双防合并），速度增益暂时不进减伤/出手判定以外的地方。
    """
    atk = float(params.get("atk") or 0) + float(params.get("spatk") or 0)
    dfn = float(params.get("def") or 0) + float(params.get("spdef") or 0)
    return atk, dfn


def _bump_status(pet, key, stacks):
    if stacks <= 0:
        return None
    st = getattr(pet, "status", None)
    if st is None:
        st = pet.status = {}
    st[key] = st.get(key, 0) + int(stacks)
    return "%s 获得 %s %d 层" % (pet.name, key, stacks)


# ---------------------------------------------------------------- 效果处理
def _h_self_buff(pet, foe, params, ctx):
    atk, dfn = _norm_stat(params)
    if atk > 0:
        pet.atk_buff = max(pet.atk_buff, pet.atk_buff + atk)
    if dfn > 0:
        pet.def_buff = max(pet.def_buff, pet.def_buff + dfn)
    bits = []
    if atk:
        bits.append("物攻 +%d%%" % round(100 * atk))
    if dfn:
        bits.append("双防 +%d%%" % round(100 * dfn))
    return "%s 属性提升（%s）" % (pet.name, "、".join(bits)) if bits else None


def _h_enemy_debuff(pet, foe, params, ctx):
    atk, dfn = _norm_stat(params)
    bits = []
    if atk:
        foe.atk_buff = max(0.0, foe.atk_buff + atk)   # atk 负值 = 降攻
        bits.append("对方物攻 %d%%" % round(100 * atk))
    if dfn:
        foe.def_buff = max(0.0, foe.def_buff + dfn)
        bits.append("对方双防 %d%%" % round(100 * dfn))
    return "%s 削弱对手（%s）" % (pet.name, "、".join(bits)) if bits else None


def _h_damage_reduction(pet, foe, params, ctx):
    pct = float(params.get("pct") or 0)
    if pct <= 0:
        return None
    pet.taken_mult = min(pet.taken_mult, 1.0 - pct)
    return "%s 减伤 %d%%" % (pet.name, round(100 * pct))


def _h_heal_hp(pet, foe, params, ctx):
    pct = float(params.get("pct") or 0)
    if pct <= 0:
        return None
    before = pet.hp
    pet.hp = min(pet.max_hp, pet.hp + max(1, int(pet.max_hp * pct)))
    return ("%s 回复 %d" % (pet.name, pet.hp - before)) if pet.hp > before else None


def _h_heal_energy(pet, foe, params, ctx):
    amt = int(params.get("amount") or 0)
    if amt <= 0:
        return None
    from simulator import ENERGY_CAP
    before = pet.energy
    pet.energy = min(ENERGY_CAP, pet.energy + amt)
    return ("%s 回能 +%d" % (pet.name, pet.energy - before)) if pet.energy > before else None


def _h_status(key):
    def fn(pet, foe, params, ctx):
        if params.get("target") == "self":
            return _bump_status(pet, key, int(params.get("stacks") or 1))
        stacks = params.get("stacks")
        if stacks is None:
            stacks = 1
        return _bump_status(foe, key, int(stacks))
    return fn


def _h_power_mod(pet, foe, params, ctx):
    """给「本回合的威力」挂一个倍率，由调用方收集。"""
    pct = float(params.get("bonus_pct") or 0)
    if pct <= 0:
        return None
    ctx["power_mult"] = ctx.get("power_mult", 1.0) * (1.0 + pct)
    return None


def _h_entry_per_skill(pet, foe, params, ctx):
    """入场时按「携带的技能里满足条件的个数」叠加。"""
    per = params.get("per_count") or {}
    elem = params.get("count_key")
    n = 0
    for m in getattr(pet, "moves", []):
        if elem and (m.get("type") or "") != elem:
            continue
        n += 1
    if not n:
        return None
    bonus = int(per.get("power_bonus") or 0) * n
    if bonus:
        pet.trait_atk_buff += 0.0      # 威力加成走 ctx
        ctx["power_bonus"] = ctx.get("power_bonus", 0) + bonus
    return None


HANDLERS = {
    "SELF_BUFF": _h_self_buff,
    "ENEMY_DEBUFF": _h_enemy_debuff,
    "DAMAGE_REDUCTION": _h_damage_reduction,
    "HEAL_HP": _h_heal_hp,
    "HEAL_ENERGY": _h_heal_energy,
    "POISON": _h_status("poison"),
    "BURN": _h_status("burn"),
    "MOISTURE_MARK": _h_status("moisture"),
    "DAMAGE_MOD_NON_LIGHT": _h_power_mod,
    "FIRST_STRIKE_POWER_BONUS": _h_power_mod,
    "ENTRY_BUFF_PER_SKILL_COUNT": _h_entry_per_skill,
}

# 允许的技能/特性时机
TRAIT_TIMINGS = {"ON_ENTER", "ON_BATTLE_START", "ON_TURN_START",
                 "ON_USE_SKILL", "ON_TURN_END", "PASSIVE", "ON_TAKE_HIT"}
SKILL_TIMINGS = {"ON_USE", "PRE_USE", "POST_USE"}


def _run(pet, foe, rows, timings, ctx):
    out = []
    for r in rows:
        if r.get("timing") not in timings:
            # 时机没建模：把这个组里的原语记成 skipped，
            # 否则「没应用」会被误读成「没有效果」
            for e in r.get("effects") or []:
                _SKIP["timing:" + str(r.get("timing"))] += 1
            continue
        for e in r.get("effects") or []:
            fn = HANDLERS.get(e["type"])
            if fn is None:
                _SKIP[e["type"]] += 1
                continue
            _APPLIED[e["type"]] += 1
            msg = fn(pet, foe, e.get("params") or {}, ctx)
            if msg:
                out.append(msg)
    return out


def trait_fire(pet, foe, timing, ctx=None):
    """触发特性的某个时机，返回事件文本列表。"""
    ctx = ctx if ctx is not None else {}
    return _run(pet, foe, trait_rows(getattr(pet, "trait", None)),
                {timing}, ctx)


def skill_fire(pet, foe, skill_name, timing, ctx=None):
    ctx = ctx if ctx is not None else {}
    return _run(pet, foe, skill_rows(skill_name), {timing}, ctx)


def status_tick(pet):
    """回合末结算异常状态，返回事件文本。"""
    st = getattr(pet, "status", None)
    if not st:
        return []
    out = []
    for key, pct in STATUS_DOT.items():
        n = int(st.get(key, 0) or 0)
        if n <= 0 or not pet.alive:
            continue
        dmg = max(1, int(pet.max_hp * pct * min(n, 5)))
        pet.hp = max(0, pet.hp - dmg)
        out.append("%s 受到 %s 伤害 -%d（剩 %d/%d）"
                   % (pet.name, key, dmg, pet.hp, pet.max_hp))
        if not pet.alive:
            out.append("%s 倒下" % pet.name)
    return out
