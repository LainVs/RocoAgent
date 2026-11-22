# -*- coding: utf-8 -*-
"""simulator.py —— 离线对战模拟器（自对弈用）。

为什么需要它
------------
实战一局 2-3 分钟，一晚最多 200 局，样本量根本不够学策略。
模拟器可以**每秒跑几千局**，用同一套伤害模型验证策略改进。

设计原则
--------
1. **复用真实数据**：精灵数值、属性、特性、技能全部来自
   data/roco.sqlite3 + data/external/roco-cal，不自己编。
2. **复用真实伤害模型**：直接调 damage_v2（roco-cal 引擎），
   保证模拟和实战数值一致。
3. **不追求完美还原**：只模拟决策相关的东西（血量、伤害、能量、换人），
   跳过动画/印记/天气等次要机制。
4. **可插拔策略**：Policy 接口，可以对比不同算法。

用法
----
    python src\\simulator.py --selfplay 1000     # 自对弈 1000 局
    python src\\simulator.py --bench             # 跑基准策略对比
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import paths  # noqa: E402

MAX_TURNS = 60          # 单局上限，防死循环
ENERGY_CAP = 10
ENERGY_GAIN = 2         # 聚能一次 +2（实测值）

# 一次 buff 技能的攻击增益。
#
# ★ 2026-11-22 升级：官方精确值优先，中位数兜底（抄作业 S1）
# ----------------------------------------------------------
# 原来用 ATK_BUFF_MEDIAN=0.70（roco-cal 51 条 self 增益的中位数）。
# 现在有官方精确映射 `roco_buff_calc_map.json`：
#
#     「物攻等级提升」-> {"phyAtkPercent": 10}   （+10%）
#     「最好的伙伴」   -> 双攻双防速各 +20%
#
# 全链：技能 skill_result[].effect_id -> buff group(id) -> percent 字段。
# 实测官方 2032 技能里 147 个引用了带百分比的 buff group。
# 精确值查不到时仍回退中位数。
ATK_BUFF_MEDIAN = 0.70

# 官方 buff 计算映射：group_id -> {"name", "phyAtkPercent", ...}
_BUFF_CALC = None


def buff_calc_map():
    global _BUFF_CALC
    if _BUFF_CALC is None:
        p = os.path.join(ROOT, "data", "external", "rkpp-official",
                         "roco_buff_calc_map.json")
        try:
            with io.open(p, encoding="utf-8") as fh:
                _BUFF_CALC = json.load(fh)
        except Exception:
            _BUFF_CALC = {}
    return _BUFF_CALC


# 技能名 -> 官方百分比增益（首次用到时从 authority/skill.json 建）
_SKILL_BUFF_PCT = None


def skill_buff_pct(skill_name):
    """查一个技能给自身的官方增益（0~1 的小数），查不到返回 None。

    链路：authority/skill.json 的 skill_result[].effect_id
          -> roco_buff_calc_map（group_id -> phyAtkPercent 等）
    """
    global _SKILL_BUFF_PCT
    if _SKILL_BUFF_PCT is None:
        out = {}
        try:
            p = os.path.join(
                r"C:/Users/Lenovo/Downloads/洛克PVP助手/洛克PVP助手/app/wwwroot/native-data/authority/skill.json")
            with io.open(p, encoding="utf-8") as fh:
                auth = json.load(fh)
        except Exception:
            auth = {}
        for s in auth.values():
            dp = s.get("dam_para") or [0]
            if (dp[0] or 0) > 0:
                continue          # 有伤害的主卡不在 buff 技能之列
            pct = {}
            for e in (s.get("skill_result") or []):
                g = buff_calc_map().get(str(e.get("effect_id")))
                if not g:
                    continue
                for f in ("phyAtkPercent", "magAtkPercent"):
                    if g.get(f):
                        pct[f] = max(pct.get(f, 0), g[f])
            if pct:
                # 双攻取 max（模拟器只有一个 atk_buff 槽）
                out[s.get("name")] = max(
                    pct.get("phyAtkPercent", 0),
                    pct.get("magAtkPercent", 0)) / 100.0
        _SKILL_BUFF_PCT = out
    return _SKILL_BUFF_PCT.get(skill_name)


# ---------------------------------------------------------------- 数据

class Dex:
    """精灵/技能数据访问（带缓存）。"""

    def __init__(self, db_path=None):
        self.db = sqlite3.connect(db_path or str(paths.DB))
        self.db.row_factory = sqlite3.Row
        self._sp = {}
        self._sk = {}
        self._trait = {}

    def species(self, name):
        if name in self._sp:
            return self._sp[name]
        # 同名多条时选真身（种族值非占位 + implemented + total 大）
        rows = self.db.execute(
            "SELECT id,name,type_names,hp,atk,spa,def,spd,spe,total,"
            "implemented,max_energy FROM species WHERE name=?",
            (name,)).fetchall()
        best = None
        for r in rows:
            vals = [r["hp"], r["atk"], r["spa"], r["def"], r["spd"], r["spe"]]
            try:
                ints = [int(x) for x in vals]
            except (TypeError, ValueError):
                continue
            real = 0 if (any(v <= 0 for v in ints) or len(set(ints)) == 1) else 1
            score = (real, r["implemented"] or 0, r["total"] or 0)
            if best is None or score > best[0]:
                best = (score, r)
        if not best:
            self._sp[name] = None
            return None
        r = best[1]
        types = json.loads(r["type_names"] or "[]")
        self._sp[name] = {
            "id": r["id"], "name": name, "types": types,
            "base": [int(r["hp"]), int(r["atk"]), int(r["spa"]),
                     int(r["def"]), int(r["spd"]), int(r["spe"])],
            "trait": self.trait_of(r["id"]),
        }
        return self._sp[name]

    def trait_of(self, species_id):
        if species_id in self._trait:
            return self._trait[species_id]
        r = self.db.execute(
            "SELECT f.name FROM feature_pets fp JOIN features f "
            "ON f.id=fp.feature_id WHERE fp.species_id=? AND fp.is_primary=1",
            (species_id,)).fetchone()
        t = r[0] if r else None
        self._trait[species_id] = t
        return t

    def moves(self, species_id, limit=8):
        rows = self.db.execute(
            "SELECT s.id,s.name,s.power,s.cost,s.type_name FROM species_skills ss "
            "JOIN skills s ON s.id=ss.skill_id "
            "WHERE ss.species_id=? AND s.power>0 "
            "ORDER BY s.power DESC LIMIT ?", (species_id, limit)).fetchall()
        out = []
        for r in rows:
            out.append({"id": r["id"], "name": r["name"],
                        "power": int(r["power"] or 0),
                        "cost": int(r["cost"] or 0),
                        "type": r["type_name"]})
        return out


# ---------------------------------------------------------------- 战斗单位

class Pet:
    """场上的一只精灵。"""

    def __init__(self, dex, name, iv=60, nature_hp=1.0):
        self.name = name
        self.data = dex.species(name)
        if not self.data:
            raise ValueError("未知精灵: %s" % name)
        import statmodel as S
        b = self.data["base"]
        self.max_hp = S.calc_real_stat(b[0], iv, nature_hp, is_hp=True)
        self.hp = self.max_hp
        self.atk = S.calc_real_stat(b[1], iv, 1.0)
        self.spa = S.calc_real_stat(b[2], iv, 1.0)
        self.dfn = S.calc_real_stat(b[3], iv, 1.0)
        self.spd = S.calc_real_stat(b[4], iv, 1.0)
        self.spe = S.calc_real_stat(b[5], iv, 1.0)
        self.types = self.data["types"]
        self.trait = self.data["trait"]
        self.energy = ENERGY_CAP
        self.moves = dex.moves(self.data["id"])
        # buff 数值来自 roco-cal 的真实技能数据（不是虚构的"每层 +20%"）。
        #
        # 实测（2026-11-21，roco-cal skills/*.json 607 个技能）：
        #   · 没有"叠层"机制：每个 buff 技能一次性给固定数值
        #   · 攻 buff 51 条，中位 +70%，均值 +64%
        #   · 防 buff 20 条，中位 +90%
        #   · 攻/防 buff 是**不同技能**，一个技能不会同时给两者
        # 旧代码假设"每层 +20% 攻防、最多 3 层（+60% 攻防）"，
        # 数值上接近真实单次 buff，但机制是虚构的（同时加攻防 + 可叠层）。
        self.atk_buff = 0.0     # 攻击增益比例，例如 0.70 = +70%
        self.def_buff = 0.0     # 防御增益比例
        self.buffed = False     # 是否已经用过 buff 技能（不再叠）

    @property
    def alive(self):
        return self.hp > 0

    def pct(self):
        return 100.0 * self.hp / self.max_hp if self.max_hp else 0.0


# ---------------------------------------------------------------- 伤害

def calc_damage(attacker, defender, move):
    """用真实的 roco-cal 引擎算伤害。

    增益按 roco-cal 的真实公式处理（damresult.py）：
        buff_numerator = 1 + atk_up + enemy_def_down
        buff_denominator = 1 + atk_down + enemy_def_up
    也就是"攻方攻击提升"和"守方防御下降"在**同一个分子**里相加，
    而不是各自独立相乘。这里只实现攻方增益这一侧（守方减益暂不模拟）。
    """
    import damage_v2 as DMG
    import statmodel as S
    stab = 1.25 if move["type"] in attacker.types else 1.0
    eff = S.type_effect(move["type"], defender.types)
    atk = attacker.atk * (1.0 + attacker.atk_buff)
    dfn = defender.dfn * (1.0 + defender.def_buff)
    dmg = DMG.damage(int(atk), int(dfn), move["power"],
                     type_eff=eff, stab=stab)
    return max(1, int(dmg))


# ---------------------------------------------------------------- 策略

class Policy:
    """策略基类。返回 (kind, payload)。

    kind: "skill"(move_index) / "energy" / "switch"(bench_index)
    """

    name = "base"

    def choose(self, me, foe, bench, turn):
        raise NotImplementedError


class GreedyPolicy(Policy):
    """贪心：选能打出的最高伤害技能；打不动就聚能。"""

    name = "greedy"

    def choose(self, me, foe, bench, turn):
        best, best_dmg = None, -1
        for i, mv in enumerate(me.moves):
            if mv["cost"] > me.energy:
                continue
            d = calc_damage(me, foe, mv)
            if d > best_dmg:
                best, best_dmg = i, d
        if best is None:
            return ("energy", None)
        return ("skill", best)


class SmartPolicy(Policy):
    """考虑击杀/被击杀/换人的策略。

    1. 能一击必杀 -> 打
    2. 我血低且对面能秒我 -> 换人（但刚换过就不再换）
    3. 对面血少且我打得动 -> 打
    4. 打不动（伤害 < 对面血 15%）-> 换人（同上）
    5. 否则选最高伤害

    实测教训（2026-11-21）：不加"刚换过"保护会连续 4 回合换人
    （T8-T11 全是 switch），白送 4 个回合，胜率直接归零。
    """

    name = "smart"

    def __init__(self):
        self._switched_at = -99

    def choose(self, me, foe, bench, turn):
        affordable = [i for i, mv in enumerate(me.moves)
                      if mv["cost"] <= me.energy]
        if not affordable:
            return ("energy", None)

        dmgs = {i: calc_damage(me, foe, me.moves[i]) for i in affordable}
        best_i = max(dmgs, key=dmgs.get)
        best_d = dmgs[best_i]

        # 能杀就打
        if best_d >= foe.hp:
            return ("skill", best_i)

        # 刚换过（2 回合内）不许再换，否则会连换白送回合
        can_switch = bench and (turn - self._switched_at) >= 2

        # 用**当前能量能打出的**技能估伤害。
        # 踩过的坑（2026-11-21）：原来对 foe.moves 全量取 max，不看能量，
        # 导致 incoming 常年 400+ 超过满血，于是"必死"永远成立 -> 每回合都换。
        affordable_foe = [mv for mv in foe.moves if mv["cost"] <= foe.energy]
        incoming = max((calc_damage(foe, me, mv) for mv in affordable_foe),
                       default=0)

        # 这回合必死 -> 打一下再死，绝不换人。
        #
        # 理由（模拟器里能直接算清）：
        #   · 换人 = 少打一次 + 新精灵照样吃这回合的攻击
        #   · 不换 = 打出一次伤害 + 死后替补**自动上场**，不浪费回合
        # 所以"必死时换人"是纯亏。实测：加了这条之前 smart 只有 37.5%，
        # 不加这条反而更差（0%），因为条件恒成立变成了每回合乱换。
        if me.hp <= incoming:
            return ("skill", best_i)

        # 打不动（伤害 < 对面 15% 血）且能换 -> 换最优替补
        if can_switch and best_d < foe.max_hp * 0.15:
            # 选**最优替补**，不是第一个。
            #
            # bench 是队伍里的**索引**列表，Pet 对象要通过 self._team 取。
            # 评分用**当前血量**：用 max_hp 会让残血精灵看起来"受伤占比低"
            # 而排到第一，换上去就送。
            team = getattr(self, "_team", None)
            best_bench = None
            for bi, idx in enumerate(bench):
                cand = team[idx] if team and idx < len(team) else None
                if cand is None:
                    continue
                inc = max((calc_damage(foe, cand, mv) for mv in affordable_foe),
                          default=0)
                d = max((calc_damage(cand, foe, mv) for mv in cand.moves),
                        default=0)
                # 换上就死的候选降级（全队都扛不住时才退而求其次）
                dies = cand.hp <= inc
                score = (d / max(1, foe.hp) - inc / max(1, cand.hp))
                key = (0 if dies else 1, score)
                if best_bench is None or key > best_bench[0]:
                    best_bench = (key, bi)
            if best_bench is not None:
                self._switched_at = turn
                return ("switch", best_bench[1])

        return ("skill", best_i)


class BuffPolicy(Policy):
    """开局先叠一次真实增益，之后一直打最强技能。

    buff 数值来自 roco-cal 真实技能数据（攻 +70% / 防 +90% 中位），
    不是旧代码里虚构的"每层 +20%、最多 3 层"。
    真实机制下 buff 不能叠，用一次就够，所以 `_buffed` 只用一次。
    """

    name = "buff"

    def __init__(self):
        self._buffed = False

    def choose(self, me, foe, bench, turn):
        affordable = [i for i, mv in enumerate(me.moves)
                      if mv["cost"] <= me.energy]
        if not affordable:
            return ("energy", None)
        dmgs = {i: calc_damage(me, foe, me.moves[i]) for i in affordable}
        best_i = max(dmgs, key=dmgs.get)

        if (not self._buffed and not me.buffed and turn <= 2
                and dmgs[best_i] < foe.hp):
            self._buffed = True
            return ("buff", None)
        return ("skill", best_i)


# ---------------------------------------------------------------- 对局

def run_match(dex, team_a, team_b, pol_a, pol_b, seed=None):
    """跑一局。返回 (winner, turns, hp_a, hp_b)。"""
    rnd = random.Random(seed)
    a = [Pet(dex, n) for n in team_a]
    b = [Pet(dex, n) for n in team_b]
    ia = ib = 0

    for turn in range(1, MAX_TURNS + 1):
        # 先把倒下的换掉（双方都要处理，不能只处理一边）
        while ia < len(a) and not a[ia].alive:
            ia += 1
        while ib < len(b) and not b[ib].alive:
            ib += 1
        if ia >= len(a):
            return "b", turn, 0, sum(p.hp for p in b)
        if ib >= len(b):
            return "a", turn, sum(p.hp for p in a), 0

        me, foe = a[ia], b[ib]
        bench_a = [i for i in range(len(a)) if i != ia and a[i].alive]
        bench_b = [i for i in range(len(b)) if i != ib and b[i].alive]
        # 把队伍对象注入策略（SmartPolicy 需要看替补的属性/技能来评分）
        for pol, team in ((pol_a, a), (pol_b, b)):
            try:
                pol._team = team
            except Exception:
                pass

        ka, pa = pol_a.choose(me, foe, bench_a, turn)
        kb, pb = pol_b.choose(foe, me, bench_b, turn)

        # 取出双方本回合选的技能对象（buff 精确值要用它的名字，2026-11-22）
        _skill_a = me.moves[pa] if (ka == "skill" and pa is not None
                                    and pa < len(me.moves)) else None
        _skill_b = foe.moves[pb] if (kb == "skill" and pb is not None
                                     and pb < len(foe.moves)) else None

        # 处理聚能
        if ka == "energy":
            me.energy = min(ENERGY_CAP, me.energy + ENERGY_GAIN)
        if kb == "energy":
            foe.energy = min(ENERGY_CAP, foe.energy + ENERGY_GAIN)

        # 处理换人（换人回合不攻击，换上新精灵）
        switched_a = switched_b = False
        if ka == "switch" and bench_a:
            ia = bench_a[min(pa or 0, len(bench_a) - 1)]
            a[ia].energy = ENERGY_CAP
            me = a[ia]
            switched_a = True
        if kb == "switch" and bench_b:
            ib = bench_b[min(pb or 0, len(bench_b) - 1)]
            b[ib].energy = ENERGY_CAP
            foe = b[ib]
            switched_b = True

        # 处理 buff：官方精确值优先（skill_buff_pct），中位数兜底
        #
        # 旧代码是 me.buffs = min(3, me.buffs + 1)（虚构的叠层），
        # 但 roco-cal 的 607 个技能里**没有叠层机制**，且攻/防 buff
        # 是不同技能，一个技能不会同时加两者。
        if ka == "buff":
            _p = skill_buff_pct((_skill_a or {}).get("name", "")) \
                if _skill_a else None
            me.atk_buff = max(me.atk_buff, _p if _p else ATK_BUFF_MEDIAN)
            me.buffed = True
        if kb == "buff":
            _p = skill_buff_pct((_skill_b or {}).get("name", "")) \
                if _skill_b else None
            foe.atk_buff = max(foe.atk_buff, _p if _p else ATK_BUFF_MEDIAN)
            foe.buffed = True

        # 出手（速度快的先打）。换人方这回合不攻击。
        acts = []
        if (not switched_a and ka == "skill" and pa is not None
                and pa < len(me.moves)):
            mv = me.moves[pa]
            if mv["cost"] <= me.energy:
                me.energy -= mv["cost"]
                acts.append((me.spe, "a", me, foe, mv))
        if (not switched_b and kb == "skill" and pb is not None
                and pb < len(foe.moves)):
            mv = foe.moves[pb]
            if mv["cost"] <= foe.energy:
                foe.energy -= mv["cost"]
                acts.append((foe.spe, "b", foe, me, mv))
        # 出手顺序：速度高的先打；速度相同则随机（不能固定偏向 A 方，
        # 否则同队自对弈会 100% 偏向 A —— 实测就是这样）。
        acts.sort(key=lambda x: (-x[0], rnd.random()))

        for _spe, who, atk, dfn, mv in acts:
            if not atk.alive or not dfn.alive:
                continue
            d = calc_damage(atk, dfn, mv)
            dfn.hp = max(0, dfn.hp - d)

        if not any(p.alive for p in a):
            return "b", turn, 0, sum(p.hp for p in b)
        if not any(p.alive for p in b):
            return "a", turn, sum(p.hp for p in a), 0

    # 超时按剩余血量判
    ha = sum(p.hp for p in a)
    hb = sum(p.hp for p in b)
    return ("a" if ha > hb else "b"), MAX_TURNS, ha, hb


# ---------------------------------------------------------------- 入口

DEFAULT_TEAM_A = ["音速犬", "恶魔狼", "冥古龙", "卡瓦重", "冰钻布鲁斯", "刨雪鹅"]
DEFAULT_TEAM_B = ["白发路路", "寂灭骨龙", "烈火战神", "水灵", "卡瓦重",
                  "炽心勇狮"]


def selfplay(n, team_a=None, team_b=None):
    dex = Dex()
    ta = team_a or DEFAULT_TEAM_A
    tb = team_b or DEFAULT_TEAM_B
    # 过滤掉库里没有的
    ta = [x for x in ta if dex.species(x)]
    tb = [x for x in tb if dex.species(x)]
    print("队伍 A: %s" % ta)
    print("队伍 B: %s" % tb)
    if not ta or not tb:
        print("队伍为空，检查精灵名")
        return 1

    from collections import Counter
    wins = Counter()
    turns = []
    for i in range(n):
        pa, pb = GreedyPolicy(), GreedyPolicy()
        w, t, _ha, _hb = run_match(dex, ta, tb, pa, pb, seed=i)
        wins[w] += 1
        turns.append(t)
    turns.sort()
    print("\n=== 贪心 vs 贪心，%d 局 ===" % n)
    print("  A 胜 %d (%.1f%%)  B 胜 %d (%.1f%%)"
          % (wins["a"], 100.0 * wins["a"] / n,
             wins["b"], 100.0 * wins["b"] / n))
    print("  平均回合 %.1f  中位 %d  最长 %d"
          % (sum(turns) / len(turns), turns[len(turns) // 2], turns[-1]))
    return 0


def bench(n):
    """对比三种策略。"""
    dex = Dex()
    ta = [x for x in DEFAULT_TEAM_A if dex.species(x)]
    tb = [x for x in DEFAULT_TEAM_B if dex.species(x)]

    from collections import Counter
    print("=== 策略对比（各 %d 局，A 用新策略 vs B 用贪心）===" % n)
    print("%-10s %8s %8s %8s" % ("策略", "A胜率", "平均回合", "A胜/B胜"))
    for cls in (GreedyPolicy, SmartPolicy, BuffPolicy):
        wins = Counter()
        turns = []
        for i in range(n):
            w, t, _a, _b = run_match(dex, ta, tb, cls(), GreedyPolicy(),
                                     seed=i)
            wins[w] += 1
            turns.append(t)
        rate = 100.0 * wins["a"] / n
        print("%-10s %7.1f%% %8.1f %5d/%d"
              % (cls.name, rate, sum(turns) / len(turns),
                 wins["a"], wins["b"]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfplay", type=int, default=0)
    ap.add_argument("--bench", type=int, default=0)
    a = ap.parse_args()
    if a.bench:
        return bench(a.bench)
    if a.selfplay:
        return selfplay(a.selfplay)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
