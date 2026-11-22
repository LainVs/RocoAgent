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


def mark_defs():
    """给 UI 用的印记清单。"""
    return [{"id": m["id"], "label": m["label"], "desc": m["desc"],
             "kind": m["kind"], "field": None, "effects": [m["effect"]]}
            for m in MARK_TABLE]


def mark_mods(side_marks, skill_name=None, element=None, first_attack=False,
              is_attack=True):
    """印记给这一手的修正（兼容旧签名）。

    side_marks 是 {mark_id: 层数/1}；每类最多一个由 set_mark 保证。
    """
    return {"power_mult": mark_power_mult(side_marks, is_attack, first_attack),
            "power_bonus": mark_power_bonus(side_marks, is_attack),
            "combo_plus": 0}


def normalize_marks(raw):
    """把手填的层数字段（旧 UI 的 xxx_stacks）翻译成新印记 id。

    旧：{"attack_mark_stacks": 3, "momentum_mark_stacks": 1}
    新：{"attack_mark": 1, "momentum_mark": 1}
    """
    out = {}
    for k, v in (raw or {}).items():
        mid = k[:-7] if k.endswith("_stacks") else k
        if mid in MARKS_BY_ID and int(v or 0) > 0:
            out[mid] = 1
    return out


# 印记全集（2026-11-22，抄自游戏内原文：rocokingdomtools / jinidex 术语表）
#
# 规则：「精灵最多同时拥有 1 种正面印记和 1 种负面印记」；换人不消失，
# 新入场精灵继承（所以印记挂在**side**上，不是挂在宠物上）。
#
# 每条：id / 名称 / 正负面 / 效果(kind, value) / 触发时机
MARK_TABLE = [
    # ---- 正面 ----
    {"id": "attack_mark", "label": "攻击印记", "kind": "positive",
     "effect": ("power_mult", 0.10, "all"),
     "desc": "全技能威力+10%"},
    {"id": "momentum_mark", "label": "蓄势印记", "kind": "positive",
     "effect": ("power_mult", 0.30, "attack"), "cost_delta": 1,
     "desc": "全攻击技能威力+30%，且能耗+1"},
    {"id": "wind_mark", "label": "风起印记", "kind": "positive",
     "effect": ("power_mult", 0.20, "first"),
     "desc": "先手攻击时，本次技能威力+20%"},
    {"id": "wet_mark", "label": "湿润印记", "kind": "positive",
     "effect": ("cost_delta", -1, "all"),
     "desc": "全技能能耗-1"},
    {"id": "photosyn_mark", "label": "光合印记", "kind": "positive",
     "effect": ("energy_tick", 1, "turn_end"),
     "desc": "回合结束时获得1能量"},
    {"id": "dragon_mark", "label": "龙噬印记", "kind": "positive",
     "effect": ("buff_on_cost3", 0.30, "attack"),
     "desc": "释放3能耗技能后，获得双攻+30%"},
    {"id": "charge_mark", "label": "蓄电印记", "kind": "positive",
     "effect": ("power_bonus", 10, "attack"),
     "desc": "攻击技能获得迸发：本次威力+10"},
    {"id": "sprout_mark", "label": "萌芽印记", "kind": "positive",
     "effect": ("buff_extra_layer", 1, "buff"),
     "desc": "获得增益时，会额外获得1层"},
    # ---- 负面 ----
    {"id": "poison_mark", "label": "中毒印记", "kind": "negative",
     "effect": ("dot", 0.03, "turn_end"),
     "desc": "回合结束造成3%生命的毒系伤害"},
    {"id": "thorn_mark", "label": "棘刺印记", "kind": "negative",
     "effect": ("switch_in_hp", -0.06, "switch_in"),
     "desc": "场上精灵离场后，更换入场的精灵失去6%生命"},
    {"id": "dark_mark", "label": "暗涌印记", "kind": "negative",
     "effect": ("switch_in_debuff", 5, "switch_in"),
     "desc": "更换入场的精灵获得随机5层属性减益"},
    {"id": "drain_mark", "label": "降灵印记", "kind": "negative",
     "effect": ("switch_in_energy", -1, "switch_in"),
     "desc": "场上精灵离场后，更换入场的精灵失去1能量"},
    {"id": "slow_mark", "label": "减速印记", "kind": "negative",
     "effect": ("speed_delta", -10, "always"),
     "desc": "速度-10"},
]

MARKS_BY_ID = {m["id"]: m for m in MARK_TABLE}


def mark_table():
    return MARK_TABLE


def mark_effect(side_marks, kind=None):
    """取某一侧印记的某个 kind（找不到返回 0）。"""
    for mid, m in MARKS_BY_ID.items():
        if not side_marks.get(mid):
            continue
        mk, val, scope = m["effect"]
        if kind and mk != kind:
            continue
        return val
    return 0


def mark_power_mult(side_marks, is_attack, first=False):
    """印记给的威力倍率（可叠加多个印记，但每类最多一个）。"""
    mul = 1.0
    for mid, m in MARKS_BY_ID.items():
        if not side_marks.get(mid):
            continue
        mk, val, scope = m["effect"]
        if mk == "power_mult":
            if scope == "all":
                mul *= 1.0 + val
            elif scope == "attack" and is_attack:
                mul *= 1.0 + val
            elif scope == "first" and first and is_attack:
                mul *= 1.0 + val
    return mul


def mark_power_bonus(side_marks, is_attack):
    if not is_attack:
        return 0
    return int(mark_effect(side_marks, "power_bonus") or 0)


def mark_cost_delta(side_marks):
    """能耗变化（湿润 -1、蓄势 +1）。"""
    d = 0
    for mid, m in MARKS_BY_ID.items():
        if not side_marks.get(mid):
            continue
        mk, val, scope = m["effect"]
        if mk == "cost_delta":
            d += val
        d += m.get("cost_delta", 0)
    return d


def mark_switch_in(side_marks):
    """换上新精灵时的印记结算：棘刺(-6%) / 降灵(-1 能量) / 暗涌(5 层减益)。"""
    out = {"hp_pct": 0.0, "energy": 0, "debuff": 0}
    for mid, m in MARKS_BY_ID.items():
        if not side_marks.get(mid):
            continue
        mk, val, _scope = m["effect"]
        if mk == "switch_in_hp":
            out["hp_pct"] += -val
        elif mk == "switch_in_energy":
            out["energy"] += val
        elif mk == "switch_in_debuff":
            out["debuff"] += int(val)
    return out


def mark_on_skill(side_marks, cost):
    """用完技能后的印记反馈（龙噬：释放 3 能耗技能后双攻 +30%）。"""
    gain = 0.0
    for mid, m in MARKS_BY_ID.items():
        if not side_marks.get(mid):
            continue
        mk, val, _scope = m["effect"]
        if mk == "buff_on_cost3" and int(cost or 0) == 3:
            gain += val
    return gain


def mark_tick(side_marks):
    """回合末印记结算：光合(+1 能量) / 中毒印记(3% 毒系伤害)。"""
    out = {"energy": 0, "dot": 0.0}
    for mid, m in MARKS_BY_ID.items():
        if not side_marks.get(mid):
            continue
        mk, val, _scope = m["effect"]
        if mk == "energy_tick":
            out["energy"] += int(val)
        elif mk == "dot":
            out["dot"] += val
    return out


def mark_speed_delta(side_marks):
    return int(mark_effect(side_marks, "speed_delta") or 0)


def set_mark(side_marks, mark_id):
    """施加一个印记：同类（正/负面）最多一个，新的顶掉旧的。

    返回 (实际挂上的 id, 被顶掉的 id 或 None)。
    """
    m = MARKS_BY_ID.get(mark_id)
    if not m:
        return None, None
    gone = None
    for mid in list(side_marks):
        other = MARKS_BY_ID.get(mid)
        if not other:
            continue
        if other["kind"] == m["kind"] and mid != mark_id:
            gone = mid
            side_marks.pop(mid, None)
    side_marks[mark_id] = int(side_marks.get(mark_id, 0) or 0) + 1
    return mark_id, gone

