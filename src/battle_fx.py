# -*- coding: utf-8 -*-
"""battle_fx.py —— 天气 / 印记 / 防御减伤 的数据与修正。

数据来源：`data/external/roco-cal/data/battle_effects/`（roco-cal，MIT）
  · `weather.json` —— 5 种天气，只有 `rain` 带威力修正
  · `marks.json`   —— 4 种印记（蓄力/疾风/攻击/气势）

★ 官方「字面伤害」是怎么算的（2026-11-22 查证，三方一致）
----------------------------------------------------------
1. **roco-cal 引擎**（`engine/damresult.py`）：

       effective_power = ((skill_power + power_bonus) * (1 + Σpower_mult))
                         * (1 + atk_up + enemy_def_down) / (1 + atk_down + enemy_def_up)
                         * (1 + type_bonus) * advantage
       damage = floor(effective_power * atk * (atk_lv*0.45+10)/41 / defense
                      * reduction_product * damage_multiplier)

2. **洛克PVP助手的运行时**（`producer-session-runtime.js` 里 `Xo()`）：

       S = effective_power * levelTerm(attackerLevel) * (atk / (41 * defense))
       damage = floor(S * effectiveness * stab * 各百分比乘区)

   它自己的标定注释就是一行可验算的实例：
       「217/160, eff 0.5, 卡面威力 150 -> 带本系 1.25 得 114，不带得 91」

       150 * 37 * 217/(41*160) * 0.5 * 1.25 = 114.75 -> floor 114  ✓
       同上不带 1.25        = 91.8           -> floor  91  ✓

   即 `levelTerm(60) = 60*0.45+10 = 37`，与 roco-cal 的 `atk_lv=60` 完全一致。

3. **我们自己的实测**：`robot._calc_card_dmg` 的 docstring 记了 4 个卡面对照，
   公式同源（`statmodel.damage` 用的就是 37/41）。

结论：**卡面上的数字 = 这套公式在「当前面板/当前 buff/当前天气印记」下
算出的值，不含暴击、不含随机**（本作 PVP 无伤害浮动）。所以模拟器只要把
输入（天气/印记/防御/特性）喂对，算出来的就是玩家在卡面上看到的数字。
"""
from __future__ import annotations

import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "data", "external", "roco-cal", "data",
                   "battle_effects")

# 攻击方等级 60 的等级项：(60*0.45+10)/41 = 37/41 ≈ 0.9024
LEVEL_TERM = 37.0

_CACHE = {}


def _load(name):
    if name in _CACHE:
        return _CACHE[name]
    try:
        with io.open(os.path.join(DIR, "%s.json" % name),
                     encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}
    _CACHE[name] = data.get("items") or []
    return _CACHE[name]


def weathers():
    """[{"id","label","effects":[...]}]"""
    return _load("weather")


def marks():
    return _load("marks")


def weather_ids():
    return [w.get("id") for w in weathers() if w.get("id")]


def weather_label(wid):
    for w in weathers():
        if w.get("id") == wid:
            return w.get("label") or wid
    return wid or "无"


def weather_power_mult(weather_id, element):
    """天气给这个属性技能的威力倍率（1.0 = 不变）。

    数据里 `value` 是**加成比例**（roco-cal 的语义：`1 + Σvalue`），
    所以 rain 对水系 `value=0.75` -> x1.75。
    """
    if not weather_id or weather_id == "none":
        return 1.0
    for w in weathers():
        if w.get("id") != weather_id:
            continue
        total = 0.0
        for e in w.get("effects") or []:
            if e.get("kind") != "power_multiplier":
                continue
            elems = ((e.get("filters") or {}).get("skill_elements") or [])
            if elems and element not in elems:
                continue
            try:
                total += float(e.get("value") or 0)
            except (TypeError, ValueError):
                continue
        return 1.0 + total
    return 1.0


def mark_mods(side_marks, skill_name, element=None, first_attack=False):
    """印记给这一手的修正。

    side_marks: {"attack_mark": 3, ...} 我方（施法者）身上的印记层数
    返回 {"power_mult": float, "power_bonus": int, "combo_plus": int}

    印记的条件有两种：
      · `conditions[].mode`（如 burst / first_attack）：满足才生效
      · 没有 conditions：只要层数 > 0 就生效
    语义照抄 roco-cal 的 `will_power.resolve_mark_modifiers`。
    """
    out = {"power_mult": 1.0, "power_bonus": 0, "combo_plus": 0}
    if not side_marks:
        return out
    for mk in marks():
        field = mk.get("stack_field")
        if not isinstance(field, str):
            continue
        try:
            stacks = max(0, int(side_marks.get(field, 0) or 0))
        except (TypeError, ValueError):
            stacks = 0
        if stacks <= 0:
            continue
        conds = mk.get("conditions") or []
        if conds:
            modes = {c.get("mode") for c in conds if isinstance(c, dict)}
            if "first_attack" in modes and not first_attack:
                continue
            if "burst" in modes:
                # 蓄力印记只在「爆发」技能上生效；我们没有爆发标记，
                # 按技能名/属性判断不可靠，直接跳过（宁可少算）。
                continue
        for e in mk.get("effects") or []:
            if e.get("kind") == "power_bonus":
                out["power_bonus"] += int(float(
                    e.get("value_per_stack") or 0) * stacks)
            elif e.get("kind") == "power_multiplier":
                out["power_mult"] *= 1.0 + float(
                    e.get("value_per_stack") or 0) * stacks
            elif e.get("kind") == "combo_plus":
                out["combo_plus"] += int(float(
                    e.get("value_per_stack") or 0) * stacks)
    return out


def mark_defs():
    """给 UI 用的印记清单（id/label/说明/字段名）。"""
    out = []
    for mk in marks():
        out.append({
            "id": mk.get("id"),
            "label": mk.get("label") or mk.get("id"),
            "desc": mk.get("description") or "",
            "field": mk.get("stack_field"),
            "effects": mk.get("effects") or [],
        })
    return out
