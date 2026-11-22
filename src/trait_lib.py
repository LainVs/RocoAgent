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
    # 按当前倒下数动态计算（悲悯/悼亡：每只力竭精灵双攻+30%，原文
    # timing=current —— 是**持续状态**，不是入场一次性；倒下数变化立即生效）
    ("current", "pets_dead_count"): {"stat_buff"},
    # 持续状态类（timing=current）：条件可判定的，每次出手前重算
    ("current", "bonus_count"): {"stat_buff"},
    ("current", "starfall_marks"): {"stat_buff"},
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
    `foe_total_cost` 对方携带技能的总耗能（`冰钻`/`烈冰狱` 这类特性要它）。

    ★ `冰钻` 的正确语义（2026-11-22 用户当面纠正）
    ------------------------------------------------
    游戏内原文是「敌方携带技能总能耗**每有** 1 点，自己攻击时威力 +10%」，
    也就是**直接按敌方的总耗能点数**算，不是「敌方比自己高多少点」。
    roco-cal 的 JSON 描述写成了「每高1点」，我照着它实现成差值，是错的；
    用户对着游戏文本指出后改回绝对值。

        敌方 4 个技能总耗能 20 -> 威力 x(1 + 0.1*20) = x3.0
    """
    trait = getattr(pet, "trait", "") or ""
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
            points = max(0, int(foe_total_cost or 0))
            if kind == "power_multiplier":
                out["power_mult"] *= 1.0 + per * points
            elif kind == "power_bonus":
                out["power_bonus"] += int(per * points)
            continue
        if not _mode_match(mode, move, pet):
            continue
        if kind == "power_multiplier":
            out["power_mult"] *= 1.0 + float(r.get("value") or 0)
        elif kind == "power_bonus":
            out["power_bonus"] += int(r.get("value") or 0)
    # 光度换算特判：原文「携带的火系技能获得选择：使用后失去15%生命，
    # 光系技能威力永久+30」。roco-cal 把 mode 写成 fire_skill（数值挂错了
    # 面向），按原文修正：光系威力 +30（flat）；火系使用后自伤 15%
    # （由 Battle 结算后处理，out["self_damage_pct"] 通道）。
    if trait == "光度换算" and move:
        if (move.get("type") or "") == "光":
            out["power_bonus"] += 30
        elif (move.get("type") or "") == "火":
            out["self_damage_pct"] = 0.15
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
    """「携带的技能都 <4 耗能」这类整队条件 -> (atk_buff, def_buff)。

    双面向口径见下方注释块：atk/mag 是同一份加成取 atk，def/res 取 def。
    """
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
        atk += _dual_stat(vals.get("atk"), vals.get("mag"),
                          vals.get("def"), vals.get("res"),
                          vals.get("spd"), vals.get("spd_flat"))[0]
        dfn += _dual_stat(vals.get("atk"), vals.get("mag"),
                          vals.get("def"), vals.get("res"),
                          vals.get("spd"), vals.get("spd_flat"))[1]
    return atk, dfn


# ★ 双面向数值的口径（2026-11-22 悲悯/悼亡事故后定死）
# ------------------------------------------------------------------
# 「双攻+30%」= 物攻+30% 和 魔攻+30% —— 是**同一份加成的两个面向**
# （物理打物攻、魔法打魔攻），不是相加成 60%。
# `values` 里 atk/mag 同时出现 -> 模型单槽取 **atk**（magic 用 matk
# 时模型也走这个槽）；def/res 同理取 def。
# 这是"一条规则覆盖双面向"的语义（roco-cal 没有 filter 区分物理/魔法）。
def _dual_stat(atk, mag, dfn, res, spd, spd_flat):
    """把 roco-cal 的多面向数值折算成模型的 (atk, dfn) 两槽。"""
    a = float(atk or 0)                 # 物攻/魔攻共用一份加成
    d = float(dfn or 0)                 # 物防/魔防共用一份加成
    return a, d


def dynamic_buffs(pet):
    """timing=current 的**持续状态**特性 -> (atk_buff, def_buff)。

    每回合开始时重算（Battle.begin_turn），倒下数/印记等条件取当前值。
    只处理条件可判定的 mode；不可判定的在 build_s5 时就已跳过并计数。

    返回 (atk, dfn)；pet 上需要 _battle/_side。
    """
    trait = getattr(pet, "trait", "") or ""
    battle = getattr(pet, "_battle", None)
    side = getattr(pet, "_side", "a")
    if battle is None:
        return 0.0, 0.0
    total_a = total_d = 0.0
    for r in supported_rules(trait):
        if r.get("timing") != "current" or r.get("kind") != "stat_buff":
            continue
        mode = r.get("mode")
        # 数值可能在 values（一次性）或 values_per_stack（按层数/计数乘）
        vals = r.get("values") or r.get("values_per_stack") or {}
        a, d = _dual_stat(vals.get("atk"), vals.get("mag"),
                          vals.get("def"), vals.get("res"),
                          vals.get("spd"), vals.get("spd_flat"))
        if mode == "pets_dead_count":
            dead = _dead_count(pet, battle, side, trait)
            total_a += a * dead
            total_d += d * dead
        # bonus_count / starfall_marks 的参数在 values 里为空
        # （roco-cal 没写数值），没有可靠数字就不硬造 —— 跳过
    return total_a, total_d


def _dead_count(pet, battle, side, trait):
    if "悼亡" in trait:
        return sum(1 for p in battle.a if not p.alive) + \
            sum(1 for p in battle.b if not p.alive)
    return sum(1 for p in battle.a if side == "a" and not p.alive) + \
        sum(1 for p in battle.b if side == "b" and not p.alive)


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
        # 双面向口径：atk/mag 是同一份加成，取 atk；def/res 取 def
        atk += float(vals.get("atk") or 0)
        dfn += float(vals.get("def") or 0)
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
