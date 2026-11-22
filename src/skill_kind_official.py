# -*- coding: utf-8 -*-
"""skill_kind 的官方数据增强：用 rkpp-official 的效果链细分技能类别。

之前 skill_kind.classify() 只按**技能名关键词**分类（copy/heal/buff...），
实测对「岩脉崩毁」「扬沙」这类不带关键词的技能只能归 other。

官方数据里每个技能有 skill_result -> buff_group -> buffbase 链，
buffbase.editor_name 是语义化中文（「物攻等级提升10」「击杀获得6能量」），
据此可以精确分类：

    自身提升（物攻/魔攻/速度/防御 +N）  -> self_buff
    对方降低（-N）                      -> enemy_debuff
    回复/治疗                            -> heal
    能量获得                             -> energy_gain
    净化/解除                            -> cleanse

分类规则（效果链优先，名字兜底）：
    有「提升」类 buff 且 target 是自身 -> self_buff
    有「降低」类 buff                  -> enemy_debuff
    有「回复」                         -> heal
"""
import io
import json
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EFFECTS = None

_PAT_UP = re.compile(r"(物攻|魔攻|物防|魔防|速度|生命|命中|暴击|闪避)等级?提升")
_PAT_DOWN = re.compile(r"(物攻|魔攻|物防|魔防|速度|命中|暴击|闪避)等级?降低")
_PAT_HEAL = re.compile(r"回复|治疗")
_PAT_ENERGY = re.compile(r"能量")


def _load():
    global _EFFECTS
    if _EFFECTS is not None:
        return _EFFECTS
    p = os.path.join(_ROOT, "data", "external", "rkpp-official",
                     "skill_effects.json")
    try:
        with io.open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        # 按**技能名**建索引（skill_effects.json 的 key 是官方 skill_id，
        # 而我们手头只有名字）
        by_name = {}
        for v in d.get("skills", {}).values():
            nm = v.get("name")
            if nm and nm not in by_name:
                by_name[nm] = v
        _EFFECTS = by_name
    except Exception:
        _EFFECTS = {}
    return _EFFECTS


def official_kind(skill_name):
    """按官方效果链分类，返回 'self_buff'/'enemy_debuff'/'heal'/
    'energy_gain'/'cleanse'/None（None=无效果链信息）。"""
    if not skill_name:
        return None
    v = _load().get(skill_name)
    if not v:
        return None
    up = down = heal = energy = False
    for e in v.get("effects") or []:
        for b in e.get("buffs") or []:
            if _PAT_HEAL.search(b):
                heal = True
            elif _PAT_ENERGY.search(b):
                energy = True
            elif _PAT_UP.search(b):
                up = True
            elif _PAT_DOWN.search(b):
                down = True
    if heal:
        return "heal"
    if up:
        return "self_buff"
    if down:
        return "enemy_debuff"
    if energy:
        return "energy_gain"
    # 有效果链但都不是上面几类（如「击杀获得6能量」已被 energy 命中；
    # 剩下的是控制/异常类），标 control
    if v.get("effects"):
        return "control"
    return None


def classify_official_first(skill_name, fallback=None):
    """官方效果链优先的分类入口。

    fallback 是旧的 skill_kind.classify() 结果；
    官方链给出**明确三类**时用官方，否则用旧结果。

    ⚠ 只信三类明确的（self_buff / enemy_debuff / heal）：
      'control' / 'energy_gain' 这类是"有效果链但不是标准增益/减益"，
      例如「岩脉崩毁」的效果链只是"技能位置固定"，
      把纯攻击技标成 control 会误导决策（它明明该按伤害卡评估）。
    """
    k = official_kind(skill_name)
    if k in ("self_buff", "enemy_debuff", "heal"):
        return k
    return fallback
