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


# species() 的结果缓存。
#
# 为什么：`_publish_state`（每帧发布给前端）会连着调
#     max_hp / types_of x2 / hp_band / species
# 而这几个函数内部各自 `sqlite3.connect()` 一次。实测每帧 **11.5 ms**，
# 全部浪费在重复 open/close 和同一条 SQL 上 —— 运行期数据是不变的。
#
# 调用方全部只读（都是 row.get(...)），所以直接共享同一个 dict 是安全的。
# 谁要改返回值，自己 copy。
_SPECIES_CACHE = {}


def clear_cache():
    """清空缓存（测试 / 热更新数据库后用）。"""
    _SPECIES_CACHE.clear()


def species(name):
    """按名字取 species 行，优先已实装/数据完整的。

    结果按名字缓存。**调用方不得修改返回的 dict**（它会被复用）。
    """
    if not name:
        return None
    if name in _SPECIES_CACHE:
        return _SPECIES_CACHE[name]
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    try:
        r = db.execute(
            "SELECT * FROM species WHERE name=? "
            "ORDER BY implemented DESC, total DESC LIMIT 1", (name,)
        ).fetchone()
        out = dict(r) if r else None
    finally:
        db.close()
    _SPECIES_CACHE[name] = out
    return out


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


def outgoing(my_name, foe_name, skill_name, foe_types=None, my_types=None,
             atk_stat=None, foe_def=None):
    """我方这个技能打对面**当前这只**的伤害（含本系 + 属性克制）。

    这是"自算伤害"的唯一入口，`robot._calc_card_dmg` 只是它的薄包装。

    为什么必须显式支持 foe_types / my_types
    --------------------------------------
    机器人在战斗里**不读属性图标**（那是独立计算器/`foe_ident` 的通道），
    所以 `robot` 传进来的 `self.foe_types` 经常是 None。旧实现直接
    `type_effect(stype, [])` -> 1.0、`stab=1.0`，等于把克制和本系全丢了：
    一张"本系 + 双克制"的卡（卡面 1.25×3=3.75 倍）会被自算低估 3.75 倍，
    再拿去和卡面做 0.35~2.5 的交叉校验，就会把**正确的卡面**判成"离谱"。

    参数
    ----
    foe_types / my_types : 给了就用（HUD 直读的属性优先），否则查库。
    atk_stat / foe_def   : 实测面板优先；不传则按 IV=60、中性性格算。

    返回 int，算不出返回 None。
    """
    import damage as D

    sk = D.skill(skill_name)
    if not sk:
        return None
    power = _num(sk.get("power"))
    atk_type = sk.get("attribute")
    if power <= 0 or not atk_type:
        return None

    me = species(my_name)
    foe = species(foe_name)
    if not me or not foe:
        return None

    my_types = list(my_types) if my_types else types_of(me)
    foe_types = list(foe_types) if foe_types else types_of(foe)

    if atk_stat is None:
        atk_sv = _num(me.get("atk"))
        if atk_sv <= 0:
            return None
        atk_stat = S.calc_real_stat(atk_sv, S.IV_PVP, 1.0, is_hp=False)
    if foe_def is None:
        def_sv = _num(foe.get("def"))
        if def_sv <= 0:
            return None
        foe_def = S.calc_real_stat(def_sv, S.IV_PVP, 1.0, is_hp=False)

    mult = S.type_effect(atk_type, foe_types)
    stab = 1.25 if atk_type in my_types else 1.0
    dmg = DMG.damage(atk_stat, foe_def, power, type_eff=mult, stab=stab)
    return int(dmg) if dmg else None


def lethal(lo, hi, my_hp_max, my_hp_pct):
    """这一击能不能打死我。"""
    cur = (my_hp_max or 0) * (my_hp_pct or 0) / 100.0
    return {"now_lo": lo >= cur, "now_hi": hi >= cur,
            "cur_hp": int(cur), "lo": lo, "hi": hi}


if __name__ == "__main__":
    for args in (("岚鸟", "闪击", "恶魔狼"),
                 ("音速犬", "火焰切割", "卡瓦重")):
        print("%s + %s -> %s : %s" % (args + (incoming(*args),)))
