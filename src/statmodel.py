# -*- coding: utf-8 -*-
"""statmodel.py —— PvP 数值模型（原样移植自 9178 伤害计算器）

来源：https://asdfghjkl0828.github.io/9178/calc.html
该计算器是 1:1 PvP 调参验证过的，本模块**只做移植，不改公式**。
唯一的本地化是把性格表落到 `config/nature_table.json`（从它的 JS 解析导出）。

四段公式
--------
    面板 HP     = round((sv*1.7 + iv*0.85 + 70) * nature + 100)
    面板 非HP   = round(round(sv*1.1 + iv*0.55 + 10) * nature + 50)
    等级系数    = lv>=0 ? 1 + lv*0.1 : 1 / (1 + |lv|*0.1)
    伤害        = floor(攻/防 × 37/41 × 威力 × 克制 × 本系 × 攻方特性 × 防方特性 × 其他 × 减伤 × 连击)

两个容易漏的细节（都是他源码里的）
----------------------------------
1. `sv == 105 且 iv == 0 且 nature == 1.0` 时，非 HP 的中间值**用 floor 而不是 round**
   —— 这是 UE4 float32 的舍入漂移，实测复现出来的特例。
2. 个体值范围是 **0~100**，界面按钮只有 0 / 60 两档；输入框为空时默认 **50**。

PvP 的实际用法（用户口径）
--------------------------
    5 星基础数值 + 人工选 3 项天赋 +60 + 一个性格（一项 ×1.2、一项 ×0.9）
    所以对面某项的取值只有有限几种 → 求值时应给**区间**，不要拿默认值当确定值。
"""
from __future__ import annotations

import itertools
import json
import math
import os

IV_MIN = 0
IV_MAX = 100
IV_DEFAULT = 50          # 源码 readIV: 空值按 50
IV_PVP = 60              # PvP 点满一档

STATS = ("hp", "patk", "matk", "pdef", "mdef", "spd")
BASE_KEY = {"hp": "hp", "patk": "pa", "matk": "ma",
            "pdef": "pd", "mdef": "md", "spd": "sp"}

_CFG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "config", "nature_table.json")
_NATURE = None


def natures():
    global _NATURE
    if _NATURE is None:
        try:
            with open(_CFG, encoding="utf-8") as fh:
                _NATURE = json.load(fh)
        except OSError:
            _NATURE = {"neutral": {k: 1.0 for k in STATS}}
    return _NATURE


def nature_mult(name):
    """性格 -> 六项倍率。未知名字按 neutral。"""
    t = natures()
    return t.get(name) or t.get("neutral") or {k: 1.0 for k in STATS}


def round_half_up(x):
    """四舍五入（游戏用的），不是 Python round() 的银行家舍入。

    为什么必须换（2026-11-21 实测）
    ------------------------------
    Python 的 `round()` 遇到 .5 会取偶数：
        round(416.5) = 416      round(417.5) = 418
    而游戏是标准四舍五入：
        416.5 -> 417            417.5 -> 418

    对照 my_team 里 5 只精灵的 30 个实测面板值：

        冥古龙 HP   旧模型 416   实测 417   <- 差 1
        卡瓦重 魔攻  旧模型  72   实测  73   <- 差 1
        恶魔狼 HP   旧模型 416   实测 417   <- 差 1

    换成 half-up 后 **30/30 全中**。

    全库影响面（1228 只 × 6 维，iv=60 无性格）：
        646 / 7368 = 8.8% 的数值会变，速度最严重（209 个）
    所以这不是"1 点误差无所谓"——它系统性地压低了伤害预测。
    """
    return (int(math.floor(x + 0.5)) if x >= 0
            else -int(math.floor(-x + 0.5)))


def calc_real_stat(sv, iv=0, nature=1.0, is_hp=False):
    """单项面板值（移植 calcRealStat，舍入改成游戏的 half-up）。"""
    iv = max(IV_MIN, min(IV_MAX, int(iv)))
    if is_hp:
        return round_half_up((sv * 1.7 + iv * 0.85 + 70) * nature + 100)
    raw = sv * 1.1 + iv * 0.55 + 10
    # 他的特例：种族值 105、无个体、无性格时用 floor
    inner = math.floor(raw) if (sv == 105 and iv == 0 and nature == 1.0) \
        else round_half_up(raw)
    return round_half_up(inner * nature + 50)


def level_mult(lv):
    """能力等级 -> 倍率（**官方公式**）。

    ★ 2026-11-22 修正：原来写的是 `1 + 0.1*lv`（+1 级 = 1.1 倍），是错的。
      官方是 `(2+n)/2`：+1 = **1.5**、+2 = **2.0**、-1 = 2/3。

    两处独立证据：
      · 洛克PVP助手 `producer-session-runtime.js` 里的 `dl()`：
            function dl(a){return a>=0?(2+a)/2:2/(2+Math.abs(a))}
        它的伤害函数 `Sr()` 就是这么用的：
            atk = round(baseAtk * dl(atkStage) * (1 + pct/100) * (1 - 退化层*0.08))
      · 官方论坛/百科：「物攻强化等级为 1 级，能力值变为 (1+2)/2 = 1.5 倍」

    注意：本模拟器目前用扁平的 atk_buff/def_buff 表达增益，还没接「强化等级」，
    所以这个函数暂时没有调用方 —— 但它之前是错的，留着迟早会坑到人。
    """
    lv = int(lv)
    return (2 + lv) / 2.0 if lv >= 0 else 2.0 / (2 + abs(lv))


def panel(base, ivs=None, nature="neutral"):
    """整只精灵面板。

    base   : {"hp":120,"pa":80,"ma":80,"pd":105,"md":105,"sp":92}
    ivs    : {"hp":60,...}，缺省 0
    nature : 性格名（见 config/nature_table.json），或 None/"neutral"
    """
    ivs = ivs or {}
    nat = nature_mult(nature) if nature else {k: 1.0 for k in STATS}
    out = {}
    for k in STATS:
        sv = base.get(BASE_KEY[k]) or 0
        out[k] = calc_real_stat(sv, ivs.get(k, 0), nat.get(k, 1.0),
                                is_hp=(k == "hp"))
    return out


def damage(atk, dfn, power, type_eff=1.0, stab=1.0, trait=1.0,
           extra=1.0, reduce=1.0, hits=1):
    """一次攻击的伤害（原样移植：全程保留小数，最后 floor 一次）。"""
    if power <= 0 or dfn <= 0:
        return 0
    single = (atk / dfn) * (37.0 / 41.0) * power * type_eff * stab \
        * trait * extra * reduce
    return int(math.floor(single * max(1, hits)))


def star_power(stacks):
    """星陨印记威力 = x² + 24x − 24（幻系，不吃本系）。"""
    x = max(0, int(stacks))
    return x * x + 24 * x - 24


# ---------------------------------------------------------------- 属性克制
#
# 合成规则（三份独立实现一致：9178 getTypeEff、roco-cal ele_advantage.py、
# loyshowdown 实机验证）：
#     克制相加：被 1 个属性克 ×2，被 2 个 ×3（不是 ×4）
#     抵抗相乘：被 1 个抵抗 ×0.5，被 2 个 ×0.25
#     一克一抗 ×1
#
# 注意：dmg_calc.py 曾按 battle_config 的字段名猜成 (0.5, 0.75, 1.0, 2.0)，
# 那是错的 —— 那 4 个字段不是这个用途。foe_damage.py 则把双属性相乘
# （2×2=4），对双弱点高估 33%。两者都已删除，这里是唯一实现。

TYPE_NAMES = ("普通", "火", "水", "草", "电", "冰", "武", "毒", "地", "翼",
              "萌", "幻", "虫", "幽", "机械", "龙", "恶", "光")

_EFF = None


def _type_eff_table():
    """{atk_name: {def_name: mult}}，从 DB 的 type_eff 读一次。"""
    global _EFF
    if _EFF is not None:
        return _EFF
    import sqlite3
    import os
    db_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "roco.sqlite3")
    table = {}
    try:
        db = sqlite3.connect(db_path)
        try:
            rows = db.execute(
                "SELECT a.name, d.name, t.mult FROM type_eff t "
                "JOIN types a ON a.id=t.atk JOIN types d ON d.id=t.def"
            ).fetchall()
            for atk, dfn, mult in rows:
                table.setdefault(atk, {})[dfn] = float(mult)
        finally:
            db.close()
    except Exception:
        table = {}
    _EFF = table
    return table


def type_effect(atk_type, def_types):
    """攻击属性打防守方属性组合的最终倍率（加法规则）。"""
    if not atk_type or not def_types:
        return 1.0
    table = _type_eff_table().get(atk_type) or {}
    mults = [table.get(d, 1.0) for d in def_types]
    if any(m == 0 for m in mults):
        return 0.0
    if len(mults) == 1:
        return mults[0]
    eff = sum(1 for m in mults if m > 1)
    res = sum(1 for m in mults if m < 1)
    if eff == 2:
        return 3.0
    if res == 2:
        return 0.25
    if eff == 1 and res == 1:
        return 1.0
    if eff == 1:
        return 2.0
    if res == 1:
        return 0.5
    return 1.0


def defense_profile(def_types):
    """防守方对 18 属性的承伤分组（等价于 pokemon.json 的 defense_profile）。

    实测校验：水灵 ["水"] -> 2x 草/电、0.5x 火/机械，与 json 完全一致。
    """
    out = {"takes_3x_from": [], "takes_2x_from": [],
           "takes_half_from": [], "takes_one_third_from": []}
    for atk in TYPE_NAMES:
        m = type_effect(atk, def_types)
        if m >= 3.0:
            out["takes_3x_from"].append(atk)
        elif m >= 2.0:
            out["takes_2x_from"].append(atk)
        elif m <= 0.34:
            out["takes_one_third_from"].append(atk)
        elif m <= 0.5:
            out["takes_half_from"].append(atk)
    return out


# ---------------------------------------------------------------- 区间

def invested_sets(n=3):
    """PvP 里"哪 n 项点满 60"的所有组合。"""
    return [set(c) for c in itertools.combinations(STATS, n)]


def hp_range(base, key="hp"):
    """血量区间：iv ∈ {0,60} × 性格 ∈ {0.9,1.0,1.2}。"""
    sv = base.get(BASE_KEY[key] if key in BASE_KEY else key) or 0
    vals = [calc_real_stat(sv, iv, nat, is_hp=True)
            for iv in (0, IV_PVP) for nat in (0.9, 1.0, 1.2)]
    return min(vals), max(vals)


def def_range(base, key="pdef", n=3):
    """防御区间：(是否点满该防) × (性格是否针对该防) 的四种组合。"""
    sv = base.get(BASE_KEY.get(key, key)) or 0
    vals = []
    for iv in (0, IV_PVP):
        for nat in (0.9, 1.0, 1.2):
            vals.append(calc_real_stat(sv, iv, nat, is_hp=False))
    return min(vals), max(vals)


if __name__ == "__main__":
    print("性格表 %d 项" % len(natures()))
    demo = {"音速犬": 85, "冰钻布鲁斯": 73, "寂灭骨龙": 120, "迪莫": 120}
    print("%-12s %6s %8s %8s %8s" % ("species", "sv", "HP(0)", "HP(60)", "区间"))
    for n, sv in demo.items():
        lo, hi = hp_range({"hp": sv})
        print("%-12s %6d %8d %8d %8s"
              % (n, sv, calc_real_stat(sv, 0, 1.0, True),
                 calc_real_stat(sv, IV_PVP, 1.0, True), "%d~%d" % (lo, hi)))
    print("\n实测对照：音速犬 366 / 冰钻布鲁斯 345（= 点满 HP、无性格修正）")
    print("性格 固执(+物攻-魔攻) 对 物攻 的影响：", nature_mult("固执"))
