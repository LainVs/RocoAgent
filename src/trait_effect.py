# -*- coding: utf-8 -*-
"""trait_effect.py —— 特性对伤害的修正（移植自 roco-cal 的 trait_runtime）。

数据来源
--------
`data/external/roco-cal/traits/` 下 262 个 JSON，每个形如：

    {"特性": {
        "名称": "三鼓作气",
        "效果": "使用能耗为3的技能后，获得双攻和双防永久+20%。",
        "battle_effect": {
            "affects_battle": true,
            "effect_groups": [{
                "mode": "skill_cost_3",              ← 触发条件
                "stack": {"permanent": true},
                "effects": [{"kind": "stat_buff",
                             "values_per_stack": {"atk": 0.2, "def": 0.2}}]
            }]
        }
    }}

统计（262 个特性）
------------------
    affects_battle=True   104   ← 会影响伤害
    affects_battle=False  146   ← 只影响非伤害（如共鸣只加虫鸣威力）
    effect kind:
        stat_buff           54   攻防加成
        power_multiplier    26   威力倍率
        power_bonus         11   威力固定加成
        combo_plus           5   连击数
        damage_multiplier    5   伤害倍率
        damage_reduction     4   减伤
        level                3   等级修正

设计原则
--------
只做"能确定算的"，算不了的返回空修正（不猜）。
上层用 `modifiers()` 拿修正字典，喂给伤害引擎。
"""
from __future__ import annotations

import io
import json
import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAIT_DIR = os.path.join(ROOT, "data", "external", "roco-cal", "traits")

_LOCK = threading.Lock()
_CACHE = {}          # {trait_name: definition or None}
_INDEX = None        # {trait_name: filepath}


def _load_json(path):
    try:
        return json.load(io.open(path, encoding="utf-8-sig"))
    except Exception:
        return None


def _build_index():
    global _INDEX
    with _LOCK:
        if _INDEX is not None:
            return _INDEX
        idx = {}
        for root, _dirs, files in os.walk(TRAIT_DIR):
            # _battle_effects 是天气/印记，不是特性
            if os.path.basename(root) == "_battle_effects":
                continue
            for f in files:
                if not f.endswith(".json"):
                    continue
                name = f[:-5]
                idx[name] = os.path.join(root, f)
        _INDEX = idx
        return idx


def available():
    return os.path.isdir(TRAIT_DIR)


def names():
    return sorted(_build_index().keys())


def get(trait_name):
    """取某个特性的定义（原始 JSON 的 '特性' 段），没有返回 None。"""
    if not trait_name:
        return None
    with _LOCK:
        if trait_name in _CACHE:
            return _CACHE[trait_name]
    path = _build_index().get(trait_name)
    if not path:
        with _LOCK:
            _CACHE[trait_name] = None
        return None
    j = _load_json(path)
    d = (j or {}).get("特性")
    with _LOCK:
        _CACHE[trait_name] = d
    return d


def affects_battle(trait_name):
    d = get(trait_name)
    if not d:
        return False
    return bool((d.get("battle_effect") or {}).get("affects_battle"))


def describe(trait_name):
    """给日志/UI 用的简短描述。"""
    d = get(trait_name)
    if not d:
        return {"name": trait_name, "known": False}
    be = d.get("battle_effect") or {}
    kinds = []
    for g in be.get("effect_groups") or []:
        for e in g.get("effects") or []:
            k = e.get("kind")
            if k and k not in kinds:
                kinds.append(k)
    return {"name": trait_name, "known": True,
            "affects": bool(be.get("affects_battle")),
            "kinds": kinds,
            "text": d.get("效果") or ""}


# ---------------------------------------------------------------- 修正计算

def _empty():
    return {"atk_buff": 0.0, "def_buff": 0.0,
            "power_bonus": 0, "power_multipliers": [],
            "damage_multipliers": [], "damage_reductions": [],
            "combo_plus": 0, "level_set": None,
            "applied": []}


def modifiers(trait_name, ctx=None):
    """算出这个特性在当前情境下给伤害的修正。

    ctx 可含（都是可选，缺省表示"条件未满足，不加成"）：
        stacks      : dict，{mode: 层数}。如 {"skill_cost_3": 2}
        skill_element / skill_name / skill_cost : 当前技能信息
        advantage   : 属性克制倍率
        own_level   : 自身等级

    返回
    ----
    {
      "atk_buff": 0.2,              # 攻防加成（直接给伤害引擎的 atk_buff）
      "def_buff": 0.0,
      "power_bonus": 20,            # 威力固定加成
      "power_multipliers": [0.2],   # 威力倍率（相加后 1+sum）
      "damage_multipliers": [],
      "damage_reductions": [],
      "combo_plus": 1,
      "level_set": None,
      "applied": ["skill_cost_3"]   # 实际生效的 mode，便于日志
    }
    """
    out = _empty()
    d = get(trait_name)
    if not d:
        return out
    be = d.get("battle_effect") or {}
    if not be.get("affects_battle"):
        return out
    ctx = ctx or {}
    stacks = ctx.get("stacks") or {}

    for g in be.get("effect_groups") or []:
        mode = g.get("mode")
        # 需要层数的条件：没给层数就不加成
        st = g.get("stack") or {}
        n = stacks.get(mode)
        if st.get("enabled") and st.get("input"):
            if not n:
                continue
            n = max(1, int(n))
        else:
            n = 1
        # 技能相关条件：给了技能信息才判断
        se = g.get("skill_elements") or []
        if se and ctx.get("skill_element") not in se:
            continue
        sn = g.get("skill_names") or []
        if sn and ctx.get("skill_name") not in sn:
            continue

        for e in g.get("effects") or []:
            kind = e.get("kind")
            if kind == "stat_buff":
                v = e.get("values_per_stack") or {}
                atk = float(v.get("atk") or v.get("patk") or 0)
                mag = float(v.get("mag") or v.get("matk") or 0)
                out["atk_buff"] += max(atk, mag) * n
            elif kind == "power_multiplier":
                v = e.get("values_per_stack") or e.get("value")
                if isinstance(v, dict):
                    v = v.get("power") or 0
                out["power_multipliers"].append(float(v or 0) * n)
            elif kind == "power_bonus":
                v = e.get("values_per_stack") or e.get("value")
                if isinstance(v, dict):
                    v = v.get("power") or 0
                out["power_bonus"] += int(v or 0) * n
            elif kind == "damage_multiplier":
                v = e.get("values_per_stack") or e.get("value")
                if isinstance(v, dict):
                    v = v.get("damage") or 0
                out["damage_multipliers"].append(float(v or 0) * n)
            elif kind == "damage_reduction":
                v = e.get("values_per_stack") or e.get("value")
                if isinstance(v, dict):
                    v = v.get("damage") or 0
                out["damage_reductions"].append(float(v or 0) * n)
            elif kind == "combo_plus":
                out["combo_plus"] += int(e.get("value") or 0) * n
            elif kind == "level":
                if e.get("operation") == "set":
                    out["level_set"] = int(e.get("value") or 0)
        if out["applied"] == [] or out["applied"][-1] != mode:
            out["applied"].append(mode)
    return out


# ---------------------------------------------------------------- 自检

def _selftest():
    if not available():
        print("特性目录不存在:", TRAIT_DIR)
        return 1
    ns = names()
    print("特性总数: %d" % len(ns))
    battle = [n for n in ns if affects_battle(n)]
    print("影响战斗: %d   不影响: %d" % (len(battle), len(ns) - len(battle)))

    print("\n=== 抽样 ===")
    for n in ("三鼓作气", "共鸣", "溶解腐蚀", "悲悯"):
        d = describe(n)
        print("  %-10s affects=%-5s kinds=%s"
              % (n, d.get("affects"), d.get("kinds") or "-"))
        print("      %s" % (d.get("text") or "")[:56])

    print("\n=== 修正计算 ===")
    m = modifiers("三鼓作气", {"stacks": {"skill_cost_3": 2}})
    print("  三鼓作气 x2 层 -> atk_buff=%.2f applied=%s"
          % (m["atk_buff"], m["applied"]))
    m2 = modifiers("三鼓作气", {})
    print("  三鼓作气 无层数 -> atk_buff=%.2f (应为 0)" % m2["atk_buff"])
    m3 = modifiers("共鸣", {})
    print("  共鸣 -> atk_buff=%.2f (应为 0，不影响战斗)" % m3["atk_buff"])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
