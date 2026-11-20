# -*- coding: utf-8 -*-
"""foe_hp.py —— 对手血量/承伤查询（唯一入口）。

原来这段逻辑散在三个模块里：
    foe_damage.py  species()/panel()/incoming()  <- 克制规则是错的（双属性相乘）
    pvp_engine.py  max_hp()/worst_incoming()     <- 依赖 damage.py 的旧公式
    robot.py       各处直接 F.panel(...)

现在统一到这里，数值一律走 statmodel（唯一验证过的模型）。

本模块只做"查数据 + 套 statmodel"，不实现任何新公式。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import statmodel as S
import damage_v2 as DMG

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "data", "roco.sqlite3")


def _num(v):
    """DB 里的种族值可能是 '--' 占位（未实装宠）。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def species(name):
    """按名字取 species 行，优先已实装/数据完整的。"""
    if not name:
        return None
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    try:
        r = db.execute(
            "SELECT * FROM species WHERE name=? "
            "ORDER BY implemented DESC, total DESC LIMIT 1", (name,)
        ).fetchone()
        return dict(r) if r else None
    finally:
        db.close()


def types_of(row_or_name):
    """species 行或名字 -> 属性名列表。"""
    row = species(row_or_name) if isinstance(row_or_name, str) else row_or_name
    if not row:
        return []
    try:
        return json.loads(row.get("type_names") or "[]")
    except ValueError:
        return []


def max_hp(name):
    """最大血量（PvP 点满 HP）。未知返回 None。

    注意：性格可能 +HP 20%，所以真实值可能是 max_hp 或 max_hp*1.2。
    要判断"会不会被秒"应该用区间，见 hp_band()。
    """
    row = species(name)
    if not row:
        return None
    sv = _num(row.get("hp"))
    if sv <= 0:
        return None
    return S.calc_real_stat(sv, S.IV_PVP, 1.0, is_hp=True)


def hp_band(name):
    """(中性, +HP性格) 的血量区间；对面性格未知时用它。"""
    row = species(name)
    if not row:
        return None
    sv = _num(row.get("hp"))
    if sv <= 0:
        return None
    return (S.calc_real_stat(sv, S.IV_PVP, 1.0, is_hp=True),
            S.calc_real_stat(sv, S.IV_PVP, 1.2, is_hp=True))


def incoming(foe_name, skill_name, my_name, my_def_iv=S.IV_PVP):
    """对面用这个技能打我们，返回 (lo, hi) 伤害区间。

    lo = 对面攻击个体 0，hi = 对面攻击个体点满。
    判断"我会不会被秒"用 lo（保守），判断"安不安全"用 hi。
    """
    import damage as D

    foe = species(foe_name)
    me = species(my_name)
    sk = D.skill(skill_name)
    if not foe or not me or not sk:
        return None

    power = _num(sk.get("power"))
    if power <= 0:
        return None

    foe_types = types_of(foe)
    my_types = types_of(me)
    atk_type = sk.get("attribute")
    mult = S.type_effect(atk_type, my_types)
    stab = 1.25 if atk_type in foe_types else 1.0

    my_def = _num(me.get("def"))
    if my_def <= 0:
        return None
    dfn = S.calc_real_stat(my_def, my_def_iv, 1.0)

    out = []
    for atk_iv in (0, S.IV_PVP):
        atk = S.calc_real_stat(_num(foe.get("atk")), atk_iv, 1.0)
        out.append(DMG.damage(atk, dfn, power, type_eff=mult, stab=stab))
    return tuple(out)


def lethal(lo, hi, my_hp_max, my_hp_pct):
    """这一击能不能打死我。"""
    cur = (my_hp_max or 0) * (my_hp_pct or 0) / 100.0
    return {"now_lo": lo >= cur, "now_hi": hi >= cur,
            "cur_hp": int(cur), "lo": lo, "hi": hi}


if __name__ == "__main__":
    for args in (("岚鸟", "闪击", "恶魔狼"),
                 ("音速犬", "火焰切割", "卡瓦重")):
        print("%s + %s -> %s : %s" % (args + (incoming(*args),)))
