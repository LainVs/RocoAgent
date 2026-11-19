# -*- coding: utf-8 -*-
"""游戏数据访问层：种族值、技能、属性。

数据来自 rocom-helper 导出的 game/*.json（1015 只精灵、1378 个技能）。
路径通过目录联接规避中文路径（PowerShell 写 ASCII 脚本时中文路径会乱码）：
    原始解包目录（ROCO_GAME_DATA）  ->  第三方助手目录（ROCO_HELPER）\\data

重要字段（踩过的坑）：
- skill_map[key]["skill_type_id"] 才是属性 id（0 普通 / 1 火 / 16 恶 ...）
- skill_map[key]["type"] 恒为 1，是分类字段，不是属性
- skill_map[key]["dam_para"][0] 是威力；0 表示变化技
- skill_map[key]["energy_cost"][0] 是耗能
- pet_species[key]["stats"] 是种族值，不是实际属性值
"""
from __future__ import annotations
import sys
import paths

import json
import os
from functools import lru_cache

DATA_DIR = os.environ.get("ROCO_DATA", str(paths.GAME_DATA))

# 属性 id -> 中文名（来自 type_chart.json，固定不变）
TYPE_NAMES = [
    "普通", "火", "水", "草", "电", "冰", "武", "毒", "地",
    "翼", "萌", "幻", "虫", "幽", "机械", "龙", "恶", "光",
]


def _load(name):
    p = os.path.join(DATA_DIR, name)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def species():
    """id(str) -> 种族信息，附带 _sid(int) 方便比较。"""
    raw = _load("pet_species.json")
    out = {}
    for k, v in raw.items():
        sid = int(k)
        v = dict(v)
        v["_sid"] = sid
        out[sid] = v
    return out


@lru_cache(maxsize=1)
def skills():
    raw = _load("skill_map.json")
    return {int(k): v for k, v in raw.items()}


@lru_cache(maxsize=1)
def pet_skills():
    """pet_id(int) -> [skill_id, ...]（该精灵可学技能，按等级顺序）。"""
    raw = _load("pet_skill_map.json")
    out = {}
    for k, v in raw.items():
        ids = []
        for item in v.get("level_skills", []):
            sid = item.get("skill_id")
            if sid and sid not in ids:
                ids.append(sid)
        out[int(k)] = ids
    return out


def type_name(type_id):
    if type_id is None:
        return "?"
    if 0 <= int(type_id) < len(TYPE_NAMES):
        return TYPE_NAMES[int(type_id)]
    return "?"


def skill_type(skill):
    """技能属性 id。"""
    return skill.get("skill_type_id")


def skill_power(skill):
    dp = skill.get("dam_para") or [0]
    try:
        return int(dp[0] or 0)
    except (TypeError, ValueError):
        return 0


def skill_cost(skill):
    ec = skill.get("energy_cost") or [0]
    try:
        return int(ec[0] or 0)
    except (TypeError, ValueError):
        return 0


def skill_priority(skill):
    # 5 是普通优先级；数字越大越先手
    try:
        return int(skill.get("skill_priority") or 5)
    except (TypeError, ValueError):
        return 5


def find_species(name, exact=False):
    """按名字找精灵，返回 [species_dict, ...]。"""
    out = []
    for sp in species().values():
        nm = sp.get("name") or ""
        if (nm == name) if exact else (name in nm):
            out.append(sp)
    return out


def pet_moves(pet_id):
    """返回该精灵可学技能 [(skill_id, skill), ...]，只含可伤害的主动技。"""
    sk = skills()
    out = []
    for sid in pet_skills().get(int(pet_id), []):
        s = sk.get(sid)
        if not s:
            continue
        out.append((sid, s))
    return out


def describe(pet_id):
    sp = species().get(int(pet_id))
    if not sp:
        return "?"
    types = [type_name(t) for t in sp.get("types", [])]
    st = sp.get("stats", {})
    return "%s(%s) hp=%s atk=%s spa=%s spe=%s" % (
        sp.get("name"), "/".join(types), st.get("hp"), st.get("atk"),
        st.get("spa"), st.get("spe"))


if __name__ == "__main__":
    print("species:", len(species()), "skills:", len(skills()),
          "pet_skills:", len(pet_skills()))
    for sp in find_species("恶魔狼", exact=True)[:2]:
        print(describe(sp["_sid"]))
        for sid, s in pet_moves(sp["_sid"])[:6]:
            print("   %-8s %-6s 属性=%-3s 威力=%-4s 耗能=%-3s" % (
                sid, s.get("name"), type_name(skill_type(s)),
                skill_power(s), skill_cost(s)))
