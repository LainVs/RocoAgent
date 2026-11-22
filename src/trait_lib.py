# -*- coding: utf-8 -*-
"""trait_lib.py —— 特性效果的运行时规则表。

数据是 `data/traits.json`（由 `scripts/build_traits.py` 从 roco-cal 的
结构化特性生成，另加 3 条文本推导的吸血）。

为什么要有「支持白名单」而不是全量照搬
--------------------------------------
roco-cal 有 111 条特性带结构化效果组，但触发条件大量依赖本模拟器**没有
建模**的东西：天气、印记层数、等级、异常状态、全场死亡数、对手换人次数、
蓄力……硬套等于编数据。所以这里显式列出能判定的组合（SUPPORTED），
其余一律不应用，并由 `coverage()` 报出覆盖率 —— 宁可少算，不要算错。

当前覆盖（对应用户点名的两个例子）
---------------------------------
  · `专注力`（音速犬）：入场首回合物攻 +100%   -> turn_start/first_turn
  · `渴求` / `贪得无厌`：入场获得 30% 吸血      -> entrance/always（文本推导）
  · `先机虹吸`：先手攻击时额外吸血 30%          -> usage/firsthand（文本推导）
"""
from __future__ import annotations

import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "traits.json")

# (timing, mode) -> 允许的 effect kind。
# 只收「本模拟器能判定条件」的组合。
SUPPORTED = {
    ("turn_start", "first_turn"): {"stat_buff"},
    # 攻击技能的威力修正
    ("action", "skill_cost_0"): {"power_multiplier", "power_bonus"},
    ("action", "skill_cost_1"): {"power_multiplier", "power_bonus"},
    ("action", "skill_cost_gt_3"): {"power_multiplier", "power_bonus"},
    ("usage", "skill_cost_3"): {"power_multiplier", "power_bonus"},
    ("usage", "fire_skill"): {"power_multiplier", "power_bonus"},
    ("usage", "wing_skill"): {"power_multiplier", "power_bonus"},
    ("usage", "non_light_skill"): {"power_multiplier", "power_bonus"},
    ("default", "chongming_bonus"): {"power_multiplier", "power_bonus"},
    ("default", "non_light_skill"): {"power_multiplier", "power_bonus"},
    # 携带技能总耗能（整队配置可判定）
    ("carried", "skills_total_cost"): {"power_multiplier", "power_bonus"},
    ("carried", "skills_cost_lt_4"): {"stat_buff"},
    # 受伤减免
    ("incoming", "attack_skill_cost_lt_1"): {"damage_reduction"},
    # 吸血（文本推导）
    ("entrance", "always"): {"lifesteal"},
    ("usage", "firsthand"): {"lifesteal"},
}

# 元素名 -> mode 前缀判定
ELEM_OF_MODE = {"fire_skill": "火", "wing_skill": "翼"}

_CACHE = {"data": None, "at": 0.0}


def _load():
    if _CACHE["data"] is not None:
        return _CACHE["data"]
    try:
        with io.open(PATH, encoding="utf-8") as fh:
            _CACHE["data"] = (json.load(fh).get("traits") or {})
    except Exception:
        _CACHE["data"] = {}
    return _CACHE["data"]


def spec(trait_name):
    """特性名 -> {"desc","structured","rules"}；没有就是 None。"""
    if not trait_name:
        return None
    return _load().get(trait_name)


def supported_rules(trait_name):
    """只返回白名单内的规则，避免运行时到处判断。"""
    sp = spec(trait_name)
    if not sp:
        return []
    out = []
    for r in sp.get("rules") or []:
        key = (r.get("timing"), r.get("mode"))
        if r.get("kind") in SUPPORTED.get(key, ()):
            out.append(r)
    return out


def unsupported_rules(trait_name):
    sp = spec(trait_name)
    if not sp:
        return []
    out = []
    for r in sp.get("rules") or []:
        key = (r.get("timing"), r.get("mode"))
        if r.get("kind") not in SUPPORTED.get(key, ()):
            out.append(r)
    return out


def _mode_match(mode, move, pet):
    """mode 的判定：元素 / 耗能 / 技能名。"""
    if not move:
        return False
    elem = ELEM_OF_MODE.get(mode)
    if elem:
        return (move.get("type") or "") == elem
    if mode == "non_light_skill":
        return (move.get("type") or "") != "光"
    cost = int(move.get("cost") or 0)
    if mode == "skill_cost_0":
        return cost == 0
    if mode == "skill_cost_1":
        return cost == 1
    if mode == "skill_cost_3":
        return cost == 3
    if mode == "skill_cost_gt_3":
        return cost > 3
    if mode == "chongming_bonus":
        names = ((move.get("_filters") or {}).get("skill_names")
                 or ((move.get("filters") or {}).get("skill_names")) or [])
        if names:
            return move.get("name") in names
        # 没有 filters 时按「虫鸣」这类专名技能兜底
        return "虫鸣" in (move.get("name") or "")
    return False


def _total_cost(pet):
    try:
        return sum(int(m.get("cost") or 0) for m in pet.moves)
    except Exception:
        return 0


def attack_mods(pet, move, first=False, foe_total_cost=None):
    """攻击时的特性修正。

    返回 {"power_mult": float, "power_bonus": int, "lifesteal": float}
    `first` 表示本回合我方是否先手（先机虹吸要用）。
    `foe_total_cost` 对方携带技能的总耗能（`冰钻`/`烈冰狱` 这类特性要比较）。

    ⚠ 2026-11-22 修正：`skills_total_cost` 的语义是「敌方总耗能**每高**
    1 点」，也就是要和**自己的**总耗能比较取差值；一开始按敌方总耗能
    绝对值算，等于凭空多出 3 倍威力（实测冰雹打出 1074）。
    """
    out = {"power_mult": 1.0, "power_bonus": 0, "lifesteal": 0.0}
    if not move:
        return out
    for r in supported_rules(getattr(pet, "trait", None)):
        timing, mode, kind = r.get("timing"), r.get("mode"), r.get("kind")
        if mode == "always":
            if kind == "lifesteal":
                out["lifesteal"] += float(r.get("value") or 0)
            continue
        if timing == "usage" and mode == "firsthand":
            if kind == "lifesteal" and first:
                out["lifesteal"] += float(r.get("value") or 0)
            continue
        if timing == "carried" and mode == "skills_total_cost":
            per = float(r.get("value_per_stack") or r.get("value") or 0)
            diff = max(0, int(foe_total_cost or 0) - _total_cost(pet))
            if kind == "power_multiplier":
                out["power_mult"] *= 1.0 + per * diff
            elif kind == "power_bonus":
                out["power_bonus"] += int(per * diff)
            continue
        if not _mode_match(mode, move, pet):
            continue
        if kind == "power_multiplier":
            out["power_mult"] *= 1.0 + float(r.get("value") or 0)
        elif kind == "power_bonus":
            out["power_bonus"] += int(r.get("value") or 0)
    return out


def first_turn_buffs(pet):
    """入场首回合的 stat_buff -> (atk_buff, def_buff)。"""
    atk = dfn = 0.0
    for r in supported_rules(getattr(pet, "trait", None)):
        if (r.get("timing"), r.get("mode")) != ("turn_start", "first_turn"):
            continue
        if r.get("kind") != "stat_buff":
            continue
        vals = r.get("values") or {}
        atk += float(vals.get("atk") or 0) + float(vals.get("mag") or 0)
        dfn += float(vals.get("def") or 0) + float(vals.get("res") or 0)
    return atk, dfn


def carrier_buffs(pet):
    """「携带的技能都 <4 耗能」这类整队条件 -> (atk_buff, def_buff)。"""
    atk = dfn = 0.0
    for r in supported_rules(getattr(pet, "trait", None)):
        if (r.get("timing"), r.get("mode")) != ("carried",
                                                "skills_cost_lt_4"):
            continue
        if r.get("kind") != "stat_buff":
            continue
        costs = [int(m.get("cost") or 0) for m in getattr(pet, "moves", [])]
        if not costs or any(c >= 4 for c in costs):
            continue
        vals = r.get("values") or {}
        atk += float(vals.get("atk") or 0) + float(vals.get("mag") or 0)
        dfn += float(vals.get("def") or 0) + float(vals.get("res") or 0)
    return atk, dfn


def incoming_mult(defender, move):
    """受击时的伤害倍率（1.0 = 不变）。"""
    mul = 1.0
    for r in supported_rules(getattr(defender, "trait", None)):
        if r.get("kind") != "damage_reduction":
            continue
        if r.get("timing") != "incoming":
            continue
        cost = int((move or {}).get("cost") or 0)
        if r.get("mode") == "attack_skill_cost_lt_1" and cost >= 1:
            continue
        mul *= max(0.0, 1.0 - float(r.get("value") or 0))
    return mul


def coverage():
    """给 API 用：多少特性有规则、多少能真正应用。"""
    data = _load()
    total = len(data)
    with_rules = sum(1 for v in data.values() if v.get("rules"))
    usable = sum(1 for k in data if supported_rules(k))
    return {"traits": total, "with_rules": with_rules, "usable": usable}
