# -*- coding: utf-8 -*-
"""adapter.py —— 把我们库的数据转成 sim 引擎的模型。

为什么要适配层
--------------
sim 引擎（移植自 NRC_AI）期望的是 `sim.models.Pokemon` / `Skill`，
而我们的事实来源是：
    · `data/roco.sqlite3` 的 `my_team` 表（六维实测值 + 装备技能 ID）
    · `species` 表（属性、特性）
    · `features` 表（特性描述）
    · sim 的 `skill_db`（技能名 -> 带效果的 Skill）

三条硬规则（都是实测踩出来的）
------------------------------
1. **六维用 my_team 的实测值，不用公式重算**
   NRC 的 IV 默认是"3 项 60、3 项 0"，我们实盘也是 3 项 60，
   但性格不同（胆小/固执/急躁…）。直接用实测值可以绕开两套公式。

2. **技能按 ID 查，不按名字**
   我们库「蝙蝠」有两条：id=7180090（power=65 正确）、
   id=7200000（power=100 脏数据）。按名字查会随机命中。

3. **属性名要加「系」后缀**
   我们库是 `"火"`，sim 的 TYPE_NAME_MAP 键是 `"火系"`。
   实测加后缀后 18/18 全部命中。

用法
----
    import adapter
    p = adapter.build_pokemon("音速犬")
    print(p.name, p.current_hp, [s.name for s in p.skills])
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import paths  # noqa: E402

from sim.models import Pokemon, Skill, SkillCategory, Type  # noqa: E402
from sim.models import TYPE_NAME_MAP  # noqa: E402
import sim.skill_db as _SD  # noqa: E402

_conn = None


def _db():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(paths.DB))
        _conn.row_factory = sqlite3.Row
    return _conn


def _type_of(type_name):
    """中文属性名 -> Type 枚举。自动补「系」后缀。"""
    if not type_name:
        return Type.NORMAL
    t = TYPE_NAME_MAP.get(type_name)
    if t is None:
        t = TYPE_NAME_MAP.get(type_name + "系")
    if t is None:
        # 有些技能表写的是 "普通" 而 map 里是 "普通系"
        for k, v in TYPE_NAME_MAP.items():
            if k.rstrip("系") == type_name.rstrip("系"):
                return v
        return Type.NORMAL
    return t


_CAT_MAP = {
    "物攻": SkillCategory.PHYSICAL, "物理": SkillCategory.PHYSICAL,
    "魔攻": SkillCategory.MAGICAL, "魔法": SkillCategory.MAGICAL,
    "防御": SkillCategory.DEFENSE, "变化": SkillCategory.STATUS,
    "状态": SkillCategory.STATUS,
}


def _my_team_row(name):
    rows = _db().execute(
        "SELECT t.*, sp.name spname, sp.type_names, sp.is_leader "
        "FROM my_team t JOIN species sp ON sp.id = t.species_id "
        "WHERE sp.name = ?", (name,)).fetchall()
    return rows[0] if rows else None


def _species_row_by_id(species_id):
    return _db().execute(
        "SELECT * FROM species WHERE id=?", (species_id,)).fetchone()


def _trait_of(species_id):
    r = _db().execute(
        "SELECT f.name, f.desc FROM feature_pets fp "
        "JOIN features f ON f.id = fp.feature_id "
        "WHERE fp.species_id=? AND fp.is_primary=1", (species_id,)).fetchone()
    if not r or not r["name"]:
        return ""
    return "%s:%s" % (r["name"], r["desc"] or "")


def _skill_row(skill_id):
    return _db().execute(
        "SELECT id,name,power,cost,type_name FROM skills WHERE id=?",
        (int(skill_id),)).fetchone()


def build_skill(skill_id):
    """按 ID 构造 Skill。

    数值（威力/耗能/属性）**一律用我们库的**，只从 sim 借效果配置。

    为什么（2026-11-21 实测）：
        sim.skill_db（来自 NRC_AI 的 nrc.db）技能数值有错：
            「月蚀」  我们库 power=130 cost=5  vs  NRC power=40 cost=2
            「撞鬼」  我们库 power=120 cost=4  vs  NRC power=40 cost=2
        如果直接用 NRC 的 Skill，这两个技能的威力会被砍到 1/3。

        但 NRC 的**效果配置**（DAMAGE/LIFE_DRAIN/应对…）是它独有的资产，
        所以：数值覆盖 + 效果保留。
    """
    _SD.load_skills()
    row = _skill_row(skill_id)
    if not row:
        return None
    name = row["name"]
    ours_power = int(row["power"] or 0)
    ours_cost = int(row["cost"] or 0)
    ours_type = _type_of(row["type_name"])

    sk = _SD.get_skill(name)
    if sk is not None:
        # 数值以我们库为准
        if sk.power != ours_power or sk.energy_cost != ours_cost:
            sk.power = ours_power
            sk.energy_cost = ours_cost
        sk.skill_type = ours_type
        return sk

    # sim 里没有这个技能：退回裸数据（无效果）
    return Skill(
        name=name,
        skill_type=ours_type,
        category=(SkillCategory.PHYSICAL if ours_power > 0
                  else SkillCategory.STATUS),
        power=ours_power,
        energy_cost=ours_cost,
    )


def build_pokemon(name):
    """从 my_team 构造 Pokemon。

    六维用实测值（my_team 的 hp/atk/spa/def/spd/spe）。
    如果某只不在 my_team 里，退回用 species 的种族值 + 默认 IV 计算。
    """
    row = _my_team_row(name)
    if row is not None:
        sp = _species_row_by_id(row["species_id"])
        stats = {
            "hp": int(row["hp"] or 0),
            "attack": int(row["atk"] or 0),
            "defense": int(row["def"] or 0),
            "sp_attack": int(row["spa"] or 0),
            "sp_defense": int(row["spd"] or 0),
            "speed": int(row["spe"] or 0),
        }
        moves = json.loads(row["moves"] or "[]")
        species_id = row["species_id"]
    else:
        # 不在队伍里：用种族值 + 3 项 60 的默认配置
        sp = _db().execute(
            "SELECT * FROM species WHERE name=? "
            "ORDER BY implemented DESC, total DESC LIMIT 1", (name,)).fetchone()
        if not sp:
            raise ValueError("未知精灵: %s" % name)
        import statmodel as S
        b = [int(sp["hp"] or 0), int(sp["atk"] or 0), int(sp["spa"] or 0),
             int(sp["def"] or 0), int(sp["spd"] or 0), int(sp["spe"] or 0)]
        iv = 60
        stats = {
            "hp": S.calc_real_stat(b[0], iv, 1.0, is_hp=True),
            "attack": S.calc_real_stat(b[1], iv, 1.0),
            "defense": S.calc_real_stat(b[3], iv, 1.0),
            "sp_attack": S.calc_real_stat(b[2], iv, 1.0),
            "sp_defense": S.calc_real_stat(b[4], iv, 1.0),
            "speed": S.calc_real_stat(b[5], iv, 1.0),
        }
        moves = []
        species_id = sp["id"]

    types = json.loads(sp["type_names"] or "[]")
    main_type = _type_of(types[0]) if types else Type.NORMAL

    skills = []
    for sid in moves:
        sk = build_skill(sid)
        if sk is not None:
            skills.append(sk)

    p = Pokemon(
        name=name,
        pokemon_type=main_type,
        ability=_trait_of(species_id),
        skills=skills,
        **stats,
    )
    # 关键：current_hp 默认是 0，必须显式设成 hp，否则回合结算不掉血
    p.current_hp = p.hp
    return p


def our_team():
    """我们队伍 5 只（按 slot 顺序）。"""
    names = []
    for r in _db().execute(
            "SELECT sp.name FROM my_team t JOIN species sp ON sp.id=t.species_id "
            "ORDER BY t.slot"):
        names.append(r["name"])
    return names


def selftest():
    """自检：构造队伍 5 只，打印六维与技能。"""
    print("=== 自检 ===")
    for nm in our_team():
        p = build_pokemon(nm)
        print()
        print("[%s] %s" % (p.name, p.pokemon_type.value))
        print("  hp=%d atk=%d def=%d spa=%d spd=%d spe=%d" % (
            p.hp, p.attack, p.defense, p.sp_attack, p.sp_defense, p.speed))
        print("  特性: %s" % (p.ability or "(无)"))
        for s in p.skills:
            effs = getattr(s, "effects", None) or []
            kinds = []
            for se in effs:
                for t in (getattr(se, "effects", None) or []):
                    kinds.append(getattr(getattr(t, "type", None), "name", "?"))
            print("    %-10s p=%-4s c=%-2s %s" % (
                s.name, s.power, s.energy_cost, kinds))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    selftest()
