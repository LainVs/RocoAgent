# -*- coding: utf-8 -*-
"""对面能打我们多少：用已知数据算，而不是等它打出来。

公式（已用真实卡面标定，六项零误差）：
    面板  = round( round(种族×1.1 + 个体×0.55 + 10) × 性格 + 50 )
    伤害  = round( round(攻/防 × 威力 × 0.9024) × 本系 × 克制 )

对面哪三项个体满（60）不知道，但有 20 种组合 —— 所以返回**区间**：
    lo  攻击个体 0   → 它最弱时能打多少
    hi  攻击个体 60  → 它最强时能打多少
判断"我会不会被秒"用 lo；判断"安全不安全"用 hi。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "data", "roco.sqlite3")
LEVEL = 60
STAB = 1.25


def level_coef(level=LEVEL):
    return (level * 45.0 / 100.0 + 10.0) / 41.0


def panel(base, iv, nature=1.0, hp=False):
    if hp:
        return int(round(round(base * 1.7 + iv * 0.85 + 70) * nature + 100))
    return int(round(round(base * 1.1 + iv * 0.55 + 10) * nature + 50))


def _db():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db


def species(name):
    db = _db()
    try:
        r = db.execute("SELECT * FROM species WHERE name=? "
                       "ORDER BY provisional ASC, implemented DESC, total DESC LIMIT 1",
                       (name,)).fetchone()
        return dict(r) if r else None
    finally:
        db.close()


def skill(name):
    db = _db()
    try:
        r = db.execute("SELECT * FROM skills WHERE name=? LIMIT 1", (name,)).fetchone()
        return dict(r) if r else None
    finally:
        db.close()


def type_mult(a_types, d_types):
    """a 打 d 的倍率（双属性相乘，按 battle_config 的档位）。"""
    if not a_types or not d_types:
        return 1.0
    db = _db()
    try:
        m = 1.0
        for a in a_types:
            for d in d_types:
                r = db.execute("SELECT mult FROM type_eff WHERE atk=? AND def=?",
                               (a, d)).fetchone()
                m *= (r["mult"] if r else 1.0)
        return m
    finally:
        db.close()


def _types(sp):
    try:
        return json.loads(sp["types"] or "[]")
    finally:
        pass


def incoming(foe_name, skill_name, my_name, my_def_iv=60, my_def_nature=1.0):
    """对面这只用它这个技能打我们，返回 (lo, hi) 伤害区间。

    my_* 是我方对应防御项的个体/性格（默认按满个体、中性，最保守）。
    """
    foe = species(foe_name)
    me = species(my_name)
    sk = skill(skill_name)
    if not foe or not me or not sk:
        return None
    power = int(sk.get("power") or 0)
    if power <= 0:
        return None

    ft = _types(foe)
    mt = _types(me)
    st = sk.get("type_id")
    mult = type_mult([st] if st is not None else ft, mt)
    stab = STAB if st in ft else 1.0
    L = level_coef()

    out = []
    for atk_iv in (0, 60):
        atk = panel(foe["atk"], atk_iv)
        dfn = max(1, panel(me["def"], my_def_iv, my_def_nature))
        base = int(round(atk / float(dfn) * power * L))
        out.append(int(round(base * stab * mult)))
    return tuple(out)


def lethal(lo, hi, my_hp_max, my_hp_pct):
    """这一击能不能打死我。"""
    cur = my_hp_max * (my_hp_pct or 0) / 100.0
    return {"now_lo": lo >= cur, "now_hi": hi >= cur,
            "cur_hp": int(cur), "lo": lo, "hi": hi}


if __name__ == "__main__":
    for args in (("岚鸟", "闪击", "恶魔狼"),
                 ("画间沉铁兽", "铁壁冲撞", "恶魔狼"),
                 ("音速犬", "火焰切割", "卡瓦重")):
        r = incoming(*args)
        print("%s + %s -> %s : %s" % (args[0], args[1], args[2], r))
    # 恶魔狼满血 417
    r = incoming("岚鸟", "闪击", "恶魔狼")
    if r:
        print("致死判断:", lethal(r[0], r[1], 417, 100.0))
