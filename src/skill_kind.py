# -*- coding: utf-8 -*-
"""skill_kind.py —— 技能效果分类（单一事实来源）。

为什么需要
----------
2026-11-21 发现一个影响所有对局的 bug：

    perceive 把 power<=0 的技能一律标成 kind="dash"（"变化技能"），
    policy_v2 又把 kind=="dash" 一律当成 buff，前两回合无脑优先。

但真实队伍的 24 个技能位里 **0 个 buff 技能**，所有 slot 4 都是：

    借用 —— 每回合随机变成己方队伍其他精灵的技能
    取念 —— 每回合随机变成敌方任意精灵的技能，能耗 -2

这两个是"复制技能"，打成它只是白白浪费一回合。日志里
`T1 SKILL 4 (status 4: buff first)` 连续出现 7 局，就是这个 bug。

真实数据（roco-cal skills/*.json，607 个技能）里，无伤害技能有 6 类：

    other           114   天气、印记、控制
    reduction        41   减伤 / 护盾（damage_reduction 字段）
    self_buff        41   自身攻防速增益（target=self）
    heal             19   治疗 / 回能
    enemy_debuff     18   给敌方挂减益（target=opponent）
    copy              3   借用 / 取念 / 复写

"无伤害"和"buff"不是一回事。本模块把分类集中到一处，
其他模块不再各自猜测。

数据来源
--------
`data/external/roco-cal/skills/*.json`，字段：

    skill_power        > 0 即伤害技能
    buff_options[].effects[]   {target: self|opponent, field: ..., value: ...}
    buff_effects[]             同上（少数技能用这个）
    damage_reduction   "70%" 这种字符串，或数字
    description        兜底用的中文描述

用法
----
    import skill_kind as SK
    SK.classify("借用")      # -> "copy"
    SK.is_buff("钧势")       # -> True
    SK.is_copy("取念")       # -> True
"""
from __future__ import annotations

import json
import os
import threading

# ---------------------------------------------------------------- 常量

KIND_DAMAGE = "damage"
KIND_SELF_BUFF = "self_buff"
KIND_ENEMY_DEBUFF = "enemy_debuff"
KIND_REDUCTION = "reduction"
KIND_HEAL = "heal"
KIND_COPY = "copy"
KIND_OTHER = "other"
KIND_UNKNOWN = "unknown"

ALL_KINDS = (KIND_DAMAGE, KIND_SELF_BUFF, KIND_ENEMY_DEBUFF, KIND_REDUCTION,
             KIND_HEAL, KIND_COPY, KIND_OTHER, KIND_UNKNOWN)

# 自身增益里，只有影响攻防速的才算"值得优先叠"的 buff。
# power_bonus / combo_plus 等属于"强化这次攻击"，不叠层。
STAT_FIELDS = frozenset((
    "phys_atk_buff", "mag_atk_buff",
    "phys_def_buff", "mag_def_buff",
    "speed_buff",
))

# 复制类技能：名字写死更可靠，描述匹配只作兜底
COPY_NAMES = frozenset(("借用", "取念", "复写"))

# ---------------------------------------------------------------- 索引

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKILL_DIR = os.path.join(_ROOT, "data", "external", "roco-cal", "skills")

_lock = threading.Lock()
_index = None            # name -> dict(power, effects, dr, desc, kind)
_load_error = None


def _parse_dr(value):
    """damage_reduction 可能是 "70%" / 70 / None。返回 0..1 的比例。"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 100.0 if v > 1.0 else v
    s = str(value).strip()
    if s.endswith("%"):
        s = s[:-1]
    try:
        v = float(s)
    except (TypeError, ValueError):
        return 0.0
    return v / 100.0 if v > 1.0 else v


def _effects_of(raw):
    """把 buff_options / buff_effects 摊平成 effects 列表。"""
    out = []
    for opt in (raw.get("buff_options") or []):
        if isinstance(opt, dict):
            out.extend(opt.get("effects") or [])
    out.extend(raw.get("buff_effects") or [])
    return [e for e in out if isinstance(e, dict)]


def _classify_raw(raw):
    """给一条原始技能数据判定分类。"""
    power = raw.get("skill_power")
    try:
        power = int(power or 0)
    except (TypeError, ValueError):
        power = 0

    name = raw.get("name") or ""
    desc = raw.get("description") or ""
    effects = _effects_of(raw)
    dr = _parse_dr(raw.get("damage_reduction"))

    # 有伤害就是伤害技能，即使附带 debuff（例如寒风吹：魔伤 + 魔防-50%）
    if power > 0:
        return KIND_DAMAGE

    # 复制技能优先判，避免被描述里的"技能"字样误伤
    if name in COPY_NAMES:
        return KIND_COPY
    if "变成" in desc and "技能" in desc:
        return KIND_COPY

    # 自身攻防速增益
    has_self_stat = any(
        e.get("target") == "self" and e.get("field") in STAT_FIELDS
        for e in effects)
    has_opp = any(e.get("target") == "opponent" for e in effects)

    # 既减伤又有增益的技能（不动如山：减伤90% + 物攻+50%）
    # 归类为 self_buff —— 因为它的收益主要来自增益。
    if has_self_stat:
        return KIND_SELF_BUFF
    if has_opp:
        return KIND_ENEMY_DEBUFF
    if dr > 0:
        return KIND_REDUCTION
    if any(w in desc for w in ("回复", "恢复", "治疗")):
        return KIND_HEAL
    return KIND_OTHER


def _build_index():
    """扫描 roco-cal 的 skills/*.json 建索引（只做一次）。"""
    global _index, _load_error
    idx = {}
    try:
        if not os.path.isdir(_SKILL_DIR):
            _load_error = "目录不存在: %s" % _SKILL_DIR
            return idx
        for fn in os.listdir(_SKILL_DIR):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(_SKILL_DIR, fn)
            try:
                data = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            for raw in (data if isinstance(data, list) else [data]):
                if not isinstance(raw, dict):
                    continue
                name = raw.get("name")
                if not name:
                    continue
                power = raw.get("skill_power")
                try:
                    power = int(power or 0)
                except (TypeError, ValueError):
                    power = 0
                idx[name] = {
                    "power": power,
                    "kind": _classify_raw(raw),
                    "dr": _parse_dr(raw.get("damage_reduction")),
                    "desc": raw.get("description") or "",
                    "element": raw.get("element"),
                }
    except Exception as e:                      # 宁可降级，不要炸调用方
        _load_error = "%s: %s" % (type(e).__name__, e)
    return idx


def index():
    """返回 name -> info 的字典（懒加载，线程安全）。"""
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                _index = _build_index()
    return _index


def load_error():
    return _load_error


def info(skill_name):
    """返回技能信息字典，查不到返回 None。"""
    if not skill_name:
        return None
    return index().get(skill_name)


def classify(skill_name):
    """技能分类。查不到返回 KIND_UNKNOWN。"""
    i = info(skill_name)
    return i["kind"] if i else KIND_UNKNOWN


def is_buff(skill_name):
    """是否是"值得优先叠"的自身攻防速增益。"""
    return classify(skill_name) == KIND_SELF_BUFF


def is_copy(skill_name):
    """是否是复制类技能（借用/取念/复写）—— 绝不该当 buff 用。"""
    return classify(skill_name) == KIND_COPY


def damage_reduction(skill_name):
    """减伤比例 0..1，非减伤技能返回 0。"""
    i = info(skill_name)
    return i["dr"] if i else 0.0


def stats():
    """统计各类数量，用于自检。"""
    out = {}
    for i in index().values():
        out[i["kind"]] = out.get(i["kind"], 0) + 1
    return out


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("技能目录: %s" % _SKILL_DIR)
    if load_error():
        print("!! 加载失败: %s" % load_error())
    print("技能总数: %d" % len(index()))
    print()
    print("=== 分类统计 ===")
    for k, n in sorted(stats().items(), key=lambda x: -x[1]):
        print("  %-16s %d" % (k, n))
    print()
    print("=== 抽查 ===")
    for nm in ("借用", "取念", "复写", "钧势", "分光", "不动如山",
               "霜冻", "雪球", "冰墙", "镜像反射", "休息回复",
               "升龙咆哮", "坟场搏击", "放晴", "不存在的技能名"):
        i = info(nm)
        print("  %-10s %-14s %s" % (
            nm, classify(nm),
            ("power=%s dr=%.2f" % (i["power"], i["dr"])) if i else ""))
