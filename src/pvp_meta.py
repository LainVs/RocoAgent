"""PvP 大数据：从真实天梯阵容估计对面「性格 / 血量 / 防御 / 技能携带」。

## 为什么需要这个模块

计算器现在只能给一个**区间**（`foe_hp.hp_band` -> 中性~+HP性格），
但排位里对面的配法是**有分布的**：

    · 固执 22.8% / 开朗 19.2% / 胆小 14.0% / 平和 12.5% / 沉默 12.3% ...
    · 不同性格会改变 HP（1.2 / 1.0 / 0.9）和物防（1.2 / 1.0 / 0.9）

所以「这一下打掉对面多少血」本身就是一个**概率分布**，不是单点。
用户要的就是这个：*给出我们技能打对面百分之多少血的概率*。

## 数据源（都是固定资产，见各自 VERSION.md）

| 文件 | 内容 | 规模 |
|---|---|---|
| `data/external/rocom-data/data/lineups.json` | 真实 PvP 阵容（性格/个体/四技能） | 169 套 / 1014 只 |
| `data/external/rocoarena/teams.json` | 天梯样本（含推荐性格/投资/技能池） | 15 套 |
| `data/roco.sqlite3` `species` | 种族值 | 1228 只 |
| `config/nature_table.json` | 性格 -> 六项倍率 | 31 种 |

技能名与数据库的匹配率实测 **99.7%**（373 个里只有 8 个不在库），
所以技能携带率可以直接按名字统计。

## 个体值（IV）的处理

`statmodel.IV_PVP = 60`，PvP 是**点满一档**的，六项都用 60。
`my_team` 里 5 只精灵的 30 个实测面板值已经验证过这个模型（30/30）。
所以这里**只让性格产生分布**，不把 `talents` 当成个体值来源
（`talents` 是"推荐投资哪三项"，不是实际 IV）。

## 性格表的缺口

`config/nature_table.json` 缺 10 个在真实阵容里出现过的性格：

    保守 / 紧张 / 孤僻 / 认真 / 浮躁 / 淘气 / 狂妄 / 沉着 / 马虎 / 理智

其中影响 HP 的一个都没有，所以 **HP 分布不受影响**。
影响物防的只有 孤僻 / 淘气 / 紧张（合计约 5%），
在补全之前一律按 neutral 处理，宁可区间偏宽也不编造倍率。
`unknown_nature_share()` 会把缺口比例暴露出来，方便以后补。

## 用法

```python
import pvp_meta as M
M.carry_rates("音速犬")          # [("闪焰冲锋", 0.42), ...]
M.hp_dist("音速犬")             # [(366, 0.75), (439, 0.25)]
M.pct_dist("音速犬", 175)       # {"lo":..,"mid":..,"hi":..,"buckets":[..]}
```

命令行自检：

```
python src/pvp_meta.py --species 音速犬 --dmg 175
python src/pvp_meta.py --stats
```
"""

from __future__ import annotations

import io
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import foe_hp                      # noqa: E402
import statmodel as S              # noqa: E402

LINEUPS = os.path.join(_ROOT, "data", "external", "rocom-data", "data",
                       "lineups.json")
ARENA = os.path.join(_ROOT, "data", "external", "rocoarena", "teams.json")

# 阵容里的"生命/物防"等中文 -> 数据库列名
TALENT_KEY = {
    "生命": "hp", "物攻": "atk", "魔攻": "spa",
    "物防": "def", "魔防": "spd", "速度": "spe",
}

_PAREN = re.compile(r"[（(][^）)]*[）)]")


def _norm(name):
    """去掉「（首领形态）」这类后缀，只留种族名。"""
    if not name:
        return ""
    return _PAREN.sub("", str(name)).strip()


# ---------------------------------------------------------------- 载入
# species -> {"n": int, "natures": {name: count}, "skills": {name: count}}
_META = None


def _add(bucket, species, nature, skills):
    sp = _norm(species)
    if not sp:
        return
    m = bucket.setdefault(sp, {"n": 0, "natures": {}, "skills": {}})
    m["n"] += 1
    if nature:
        m["natures"][nature] = m["natures"].get(nature, 0) + 1
    for sk in skills or []:
        sk = str(sk).strip()
        if sk:
            m["skills"][sk] = m["skills"].get(sk, 0) + 1


def _load():
    global _META
    if _META is not None:
        return _META
    bucket = {}

    # ---- 1. rocom-data 的 169 套真实阵容（主力样本）
    try:
        with io.open(LINEUPS, encoding="utf-8") as fh:
            for team in json.load(fh):
                for m in team.get("members") or []:
                    _add(bucket, m.get("pokemon"), m.get("nature"),
                         m.get("skills"))
    except (OSError, ValueError) as e:
        print("pvp_meta: lineups.json load failed: %s" % e,
              file=sys.stderr)

    # ---- 2. rocoarena 的天梯样本（补充，字段名不同）
    try:
        with io.open(ARENA, encoding="utf-8") as fh:
            data = json.load(fh)
        for team in data.get("teams") or []:
            for m in team.get("members") or []:
                skills = [s.get("name") for s in (m.get("skills") or [])]
                _add(bucket, m.get("name"), m.get("nature"), skills)
    except (OSError, ValueError) as e:
        print("pvp_meta: teams.json load failed: %s" % e, file=sys.stderr)

    _META = bucket
    return _META


def reload():
    """测试 / 数据更新后用。"""
    global _META
    _META = None


def species_meta(name):
    """原始统计（含样本数），没有样本返回 None。"""
    sp = _norm(name)
    return _load().get(sp)


def sample_size(name):
    m = species_meta(name)
    return int(m["n"]) if m else 0


# ---------------------------------------------------------------- 性格
def nature_dist(name):
    """[(性格, 概率)]，按概率降序。没有样本返回 []。"""
    m = species_meta(name)
    if not m or not m["n"]:
        return []
    n = float(m["n"])
    return sorted(((k, v / n) for k, v in m["natures"].items()),
                  key=lambda x: -x[1])


def unknown_nature_share(name):
    """这个物种的样本里，性格表覆盖不到的比例（0~1）。"""
    m = species_meta(name)
    if not m or not m["n"]:
        return 0.0
    table = S.natures()
    miss = sum(v for k, v in m["natures"].items() if k not in table)
    return miss / float(m["n"])


# ---------------------------------------------------------------- 技能
def carry_rates(name, top=None):
    """[(技能名, 携带率)]。携带率 = 该物种样本里带这个技能的比例。

    注意分母是**这只精灵的出场次数**，不是全样本 —— 所以四项技能
    的携带率加起来大约 4.0（每只带 4 个技能）。
    """
    m = species_meta(name)
    if not m or not m["n"]:
        return []
    n = float(m["n"])
    out = sorted(((k, v / n) for k, v in m["skills"].items()),
                 key=lambda x: -x[1])
    return out[:top] if top else out


# ---------------------------------------------------------------- 数值分布
# 数据库列 -> 性格表里的键
STAT_KEY = {"hp": "hp", "atk": "patk", "spa": "matk",
            "def": "pdef", "spd": "mdef", "spe": "spd"}


def _stat_dist(name, col, is_hp):
    """按性格分布算某个面板值的概率分布。

    返回 [(值, 概率)]，按值升序，同值合并。
    """
    row = foe_hp.species(name)
    m = species_meta(name)
    if not row or not m or not m["n"]:
        return []
    sv = foe_hp._num(row.get(col))
    if sv <= 0:
        return []
    table = S.natures()
    key = STAT_KEY.get(col, "hp")
    n = float(m["n"])
    acc = {}
    for nat, cnt in m["natures"].items():
        mult = (table.get(nat) or table.get("neutral")).get(key, 1.0)
        val = S.calc_real_stat(sv, S.IV_PVP, mult, is_hp=is_hp)
        acc[val] = acc.get(val, 0.0) + cnt / n
    return sorted(acc.items())


def hp_dist(name):
    """最大血量的概率分布 [(hp, 概率)]。"""
    return _stat_dist(name, "hp", True)


def def_dist(name):
    """物防的概率分布 [(物防, 概率)]。"""
    return _stat_dist(name, "def", False)


def mdef_dist(name):
    """魔防的概率分布 [(魔防, 概率)]。"""
    return _stat_dist(name, "spd", False)


def _pctile(buckets, q):
    """buckets = [(value, prob)] 已按 value 升序；返回 q 分位数。"""
    acc = 0.0
    for v, p in buckets:
        acc += p
        if acc >= q:
            return v
    return buckets[-1][0] if buckets else None


def pct_dist(name, dmg):
    """技能伤害 dmg 打掉对面**最大血量**的百分比分布。

    返回:
        {"lo": p10, "mid": p50, "hi": p90,
         "buckets": [[pct, prob], ...], "n": 样本数, "hp_dist": [...]}

    为什么分母用最大血量：卡面伤害是固定值，血量越高占比越低。
    分布完全来自对面 HP 的不确定性（性格）。
    """
    if not dmg or dmg <= 0:
        return None
    hd = hp_dist(name)
    if not hd:
        return None
    acc = {}
    for hp, p in hd:
        pct = round(100.0 * dmg / hp, 1)
        acc[pct] = acc.get(pct, 0.0) + p
    buckets = sorted(acc.items())
    return {
        "lo": _pctile(buckets, 0.10),
        "mid": _pctile(buckets, 0.50),
        "hi": _pctile(buckets, 0.90),
        "buckets": [[v, round(p, 4)] for v, p in buckets],
        "n": sample_size(name),
        "hp_dist": [[int(v), round(p, 4)] for v, p in hd],
    }


def pct_at_least(name, dmg, pct):
    """打掉 >= pct% 的概率（用于"这一下够不够斩杀"）。"""
    d = pct_dist(name, dmg)
    if not d:
        return None
    return round(sum(p for v, p in d["buckets"] if v >= pct), 4)


# ---------------------------------------------------------------- CLI
def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="PvP 大数据分布自检")
    ap.add_argument("--species", help="精灵名")
    ap.add_argument("--dmg", type=float, help="技能伤害（卡面读数）")
    ap.add_argument("--stats", action="store_true", help="打印数据源规模")
    args = ap.parse_args(argv)

    if args.stats or not args.species:
        meta = _load()
        members = sum(m["n"] for m in meta.values())
        skills = sum(len(m["skills"]) for m in meta.values())
        print("物种 %d 只 / 出场 %d 次 / 技能条目 %d"
              % (len(meta), members, skills))
        top = sorted(meta.items(), key=lambda kv: -kv[1]["n"])[:10]
        for sp, m in top:
            print("  %-14s %4d 次  性格 %d 种  技能 %d 种"
                  % (sp, m["n"], len(m["natures"]), len(m["skills"])))
        if not args.species:
            return 0

    sp = args.species
    print("== %s ==" % sp)
    print("样本 %d 次" % sample_size(sp))
    print("性格:", ["%s %.0f%%" % (k, 100 * p) for k, p in nature_dist(sp)[:6]])
    print("未知性格占比: %.1f%%" % (100 * unknown_nature_share(sp)))
    print("技能携带:", ["%s %.0f%%" % (k, 100 * p)
                     for k, p in carry_rates(sp, top=8)])
    print("HP 分布:", [(int(v), round(p, 3)) for v, p in hp_dist(sp)])
    print("物防分布:", [(int(v), round(p, 3)) for v, p in def_dist(sp)])
    if args.dmg:
        d = pct_dist(sp, args.dmg)
        if d:
            print("伤害 %g -> 占比 p10=%.1f%% 中位=%.1f%% p90=%.1f%%"
                  % (args.dmg, d["lo"], d["mid"], d["hi"]))
            print("  明细:", ["%.1f%%:%.0f%%" % (v, 100 * p)
                            for v, p in d["buckets"]])
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
