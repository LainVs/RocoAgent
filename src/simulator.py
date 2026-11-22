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

# S5 全量技能表（roco-cal）。懒加载，一次性读进内存。
_S5 = {"path": os.path.join(ROOT, "data", "s5_skills.json"),
       "pets": None}


def _s5_pets():
    """{"冰钻布鲁斯": {"id","skills":[{"name","unlock"}]}, ...}，读不到就给空 dict。"""
    if _S5["pets"] is None:
        try:
            with io.open(_S5["path"], encoding="utf-8") as fh:
                _S5["pets"] = json.load(fh).get("pets") or {}
        except Exception:
            _S5["pets"] = {}
    return _S5["pets"]


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

    def resolve_skill(self, ref):
        """按 **id 或名字** 取一个技能（含 power=0 的辅助技能）。
        为什么单独要这一步（2026-11-22，接真实玩家阵容时发现）
        ------------------------------------------------------
        `moves(species_id)` 只返回「该精灵的学法表 ∩ power>0」，两个限制
        对真实阵容都不成立：

          · 真实阵容（rocom-data/lineups.json 169 套）的技能是**名字**，
            不是 id；
          · 里面大量是力量增效 / 防御 / 有效预防这类 power=0 的辅助技能。

        原来按学法表过滤，169 套里的技能只有 26% 能解析；改成直接查
        skills 表后是 **4042/4056 = 99.7%**（缺的 14 条是活动技能）。

        返回 {"id","name","power","cost","type"} 或 None。
        """
        if ref is None:
            return None
        row = None
        try:
            sid = int(ref)
        except (TypeError, ValueError):
            sid = None
        if sid is not None:
            row = self.db.execute(
                "SELECT id,name,power,cost,type_name FROM skills WHERE id=?",
                (sid,)).fetchone()
        if row is None:
            row = self.db.execute(
                "SELECT id,name,power,cost,type_name FROM skills "
                "WHERE name=? ORDER BY id LIMIT 1", (str(ref).strip(),)
            ).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "name": row["name"],
                "power": int(row["power"] or 0),
                "cost": int(row["cost"] or 0),
                "type": row["type_name"]}

    def skills_for(self, name, with_meta=False):
        """某只精灵的**完整**可学技能表（S5 优先）。

        为什么不用 moves()（2026-11-22，用户指出两件事）
        ------------------------------------------------
        `moves()` = 「species_skills ∩ power>0 ∩ LIMIT 8」，两个后果：

          · "可选的技能组似乎不太全"：冰钻布鲁斯库里 45 个技能，
            编辑器只给 8 个；
          · 力量增效 / 防御 / 有效预防这些 power=0 的辅助技能，
            **一个都点不到**（被 power>0 过滤掉了）。

        而且 `species_skills` 是旧赛季的：冰钻布鲁斯有「跺地」没有「扬沙」，
        而 S5（roco-cal 权威源，`254冰钻布鲁斯.json`）正好相反 ——
        用户点名了这个错。

        所以这里优先读 `data/s5_skills.json`
        （scripts/build_s5_skills.py 从 roco-cal 生成，659 只 / 29163 条），
        查不到再退回 moves()。返回按威力降序，power=0 的排最后，
        和游戏里的技能面板顺序一致。
        """
        out = []
        seen = set()
        s5 = _s5_pets().get(name)
        if s5:
            for s in s5.get("skills") or []:
                mv = self.resolve_skill(s.get("name"))
                if not mv or mv["id"] in seen:
                    continue
                seen.add(mv["id"])
                if with_meta:
                    mv = dict(mv, unlock=s.get("unlock") or "")
                out.append(mv)
        if not out:
            sp = self.species(name)
            if sp:
                out = self.moves(sp["id"], limit=64)
        out.sort(key=lambda m: (-int(m.get("power") or 0), m["name"]))
        return out


# ---------------------------------------------------------------- 战斗单位

class Pet:
    """场上的一只精灵。"""

    def __init__(self, dex, name, iv=60, nature_hp=1.0, nature=None,
                 invest=None):
        """iv/nature/invest：PvP 真实建模（2026-11-22 修正，用户指出）。

        ★ 个体值正确建模：**选三项投 60，其余 0**
        ------------------------------------------
        之前六项都填 60 是错的。用 my_team 5 只精灵的实测面板反推：

            音速犬   hp/atk/speed=60, spa/def/spd=0, 开朗 -> 6/6 面板命中
            冰钻布鲁斯 hp/atk/speed=60, spa/def/spd=0, 固执 -> 6/6 命中
            恶魔狼   hp/atk/speed=60, spa/def/spd=0, 开朗 -> 6/6 命中

        官方 800 只使用率数据同样印证：每只精灵只有 2~3 项有投资
        （音速犬：物攻 82.8% / 速度 81.7% / 生命 62.5%，后三项都 <32%）。
        ⚠ 那份官方文件把这一项写成「精力」，但它是 HP 属性；
        游戏内天分面板和阵容数据（rocom-data 1014 只）都叫「生命」，
        所以界面显示统一用生命，解析两种写法都认。

        invest：[项名...] 要投 60 的项（"hp"/"atk"/"spa"/"def"/"spd"/"spe"），
                缺省 ["hp","atk","spe"]（天梯最常见的攻速体三投资）。
        nature：完整性格名（31 种官方表），决定六项倍率。
        """
        self.name = name
        self.data = dex.species(name)
        if not self.data:
            raise ValueError("未知精灵: %s" % name)
        import statmodel as S
        b = self.data["base"]
        inv = set(invest or ["hp", "atk", "spe"])
        mult = S.nature_mult(nature) if nature else {
            "hp": nature_hp, "patk": 1.0, "matk": 1.0,
            "pdef": 1.0, "mdef": 1.0, "spd": 1.0}
        self.nature = nature
        self.iv = iv
        self.invest = sorted(inv)
        self.max_hp = S.calc_real_stat(b[0], iv if "hp" in inv else 0,
                                       mult["hp"], is_hp=True)
        self.hp = self.max_hp
        self.atk = S.calc_real_stat(b[1], iv if "atk" in inv else 0,
                                    mult["patk"])
        self.spa = S.calc_real_stat(b[2], iv if "spa" in inv else 0,
                                    mult["matk"])
        self.dfn = S.calc_real_stat(b[3], iv if "def" in inv else 0,
                                    mult["pdef"])
        self.spd = S.calc_real_stat(b[4], iv if "spd" in inv else 0,
                                    mult["mdef"])
        self.spe = S.calc_real_stat(b[5], iv if "spe" in inv else 0,
                                    mult["spd"])
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

    def panel(self):
        """六项面板（UI 展示用）。"""
        return {"hp": self.max_hp, "atk": self.atk, "spa": self.spa,
                "def": self.dfn, "spd": self.spd, "spe": self.spe}

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

class Battle:
    """一场对局的**可单步**状态机。

    为什么要从 run_match() 抽出来（2026-11-22，手操对战窗口）
    ------------------------------------------------------
    手操窗口必须和批量模拟共用同一套战斗循环。各写一套的话，同一个
    局面手操和批量会给出不同结果，这个窗口就完全没有参考价值了。

    所以 `run_match()` 现在只是「两边都交给策略」的薄壳，
    手操窗口用 `begin_turn()` + `step(我方动作, 敌方动作)` 单步推进。

    语义逐条对齐原来的 run_match（改动会立刻被
    `data/fixtures/sim_baseline.json` + `src/test_sim_engine.py` 抓到）：

      · 每回合先跳过倒下的精灵；一方全灭即判负
      · 聚能 +2 能量（上限 ENERGY_CAP）
      · 换人回合不攻击，换上的精灵能量补满
      · buff 数值优先官方精确值（skill_buff_pct），中位数兜底
      · 出手顺序按速度降序，速度相同用**回合内随机数**打散
        （不能固定偏向 A，否则同队自对弈 100% 偏向 A）
      · MAX_TURNS 回合上限，超时按剩余总血量判
    """

    def __init__(self, dex, team_a, team_b, cfg_a=None, cfg_b=None,
                 seed=None):
        self.dex = dex
        self.rnd = random.Random(seed)
        self.a = self._build(team_a, cfg_a)
        self.b = self._build(team_b, cfg_b)
        self.ia = self.ib = 0
        self.turn = 0
        self.done = None            # None / "a" / "b"
        self.hp_a = self.hp_b = 0

    # ---------------------------------------------------------- 构建
    def _build(self, team, cfg):
        # cfg 可能比 team 短（UI 只给已填的槽位，手写测试也可能只给前几只），
        # 必须补齐，否则 `(cfg or ...)[i]` 直接 IndexError。
        # 实测 2026-11-22：/api/sim/run 传 3 只精灵 + 1 条 cfg 就崩在
        # `c = (cfg or [None] * len(team))[i] or {}`。
        cfg = list(cfg or [])
        if len(cfg) < len(team):
            cfg += [None] * (len(team) - len(cfg))
        pets = []
        for i, n in enumerate(team):
            c = cfg[i] or {}
            p = Pet(self.dex, n, iv=c.get("iv", 60),
                    nature=c.get("nature"),
                    invest=c.get("invest"))
            if c.get("moves"):
                # 技能：接受**id 或名字**，顺序 = 卡槽顺序。
                # 真实玩家阵容（lineup_lib）给的是名字，UI 给的是 id，
                # 统一走 Dex.resolve_skill（含 power=0 的辅助技能）。
                picked = [self.dex.resolve_skill(x) for x in c["moves"]]
                picked = [m for m in picked if m]
                if picked:
                    p.moves = picked[:8]
            if c.get("max_hp"):
                p.max_hp = int(c["max_hp"])
                p.hp = p.max_hp
            pets.append(p)
        return pets

    # ---------------------------------------------------------- 查询
    @property
    def me(self):
        return self.a[self.ia] if self.ia < len(self.a) else None

    @property
    def foe(self):
        return self.b[self.ib] if self.ib < len(self.b) else None

    def bench(self, side="a"):
        team = self.a if side == "a" else self.b
        cur = self.ia if side == "a" else self.ib
        return [i for i in range(len(team)) if i != cur and team[i].alive]

    def result(self):
        return (self.done, self.turn, self.hp_a, self.hp_b)

    def _finish(self, winner, turn, hp_a, hp_b):
        self.done = winner
        self.turn = turn
        self.hp_a = hp_a
        self.hp_b = hp_b

    # ---------------------------------------------------------- 单步
    def begin_turn(self):
        """进入下一回合。返回 False 表示对局已经结束。

        顺序和原来的 `for turn in range(1, MAX_TURNS+1)` 完全一致：
        先判「60 回合用完了」，再判「谁被打没了」——
        反过来的话，第 60 回合刚好全灭时会多算一回合。
        """
        if self.done:
            return False
        if self.turn >= MAX_TURNS:
            ha = sum(p.hp for p in self.a)
            hb = sum(p.hp for p in self.b)
            self._finish("a" if ha > hb else "b", MAX_TURNS, ha, hb)
            return False
        self.turn += 1
        # 先把倒下的换掉（双方都要处理，不能只处理一边）
        while self.ia < len(self.a) and not self.a[self.ia].alive:
            self.ia += 1
        while self.ib < len(self.b) and not self.b[self.ib].alive:
            self.ib += 1
        if self.ia >= len(self.a):
            self._finish("b", self.turn, 0, sum(p.hp for p in self.b))
            return False
        if self.ib >= len(self.b):
            self._finish("a", self.turn, sum(p.hp for p in self.a), 0)
            return False
        return True

    def step(self, ka, pa, kb, pb):
        """执行一个回合两边的动作，返回本回合的事件列表（给 UI 展示）。

        动作格式同 Policy.choose 的返回值 (kind, payload)，
        kind ∈ {"skill","energy","switch","buff"}。
        """
        events = []
        me, foe = self.me, self.foe
        if me is None or foe is None:
            return events
        bench_a, bench_b = self.bench("a"), self.bench("b")

        # 取出双方本回合选的技能对象（buff 精确值要用它的名字）
        skill_a = me.moves[pa] if (ka == "skill" and pa is not None
                                   and pa < len(me.moves)) else None
        skill_b = foe.moves[pb] if (kb == "skill" and pb is not None
                                    and pb < len(foe.moves)) else None

        # 处理聚能
        if ka == "energy":
            me.energy = min(ENERGY_CAP, me.energy + ENERGY_GAIN)
            events.append("我方聚能（能量 %d）" % me.energy)
        if kb == "energy":
            foe.energy = min(ENERGY_CAP, foe.energy + ENERGY_GAIN)
            events.append("对方聚能（能量 %d）" % foe.energy)

        # 处理换人（换人回合不攻击，换上新精灵）
        switched_a = switched_b = False
        if ka == "switch" and bench_a:
            old = me.name
            self.ia = bench_a[min(pa or 0, len(bench_a) - 1)]
            self.a[self.ia].energy = ENERGY_CAP
            me = self.a[self.ia]
            switched_a = True
            events.append("我方换下 %s，换上 %s" % (old, me.name))
        if kb == "switch" and bench_b:
            old = foe.name
            self.ib = bench_b[min(pb or 0, len(bench_b) - 1)]
            self.b[self.ib].energy = ENERGY_CAP
            foe = self.b[self.ib]
            switched_b = True
            events.append("对方换下 %s，换上 %s" % (old, foe.name))

        # 处理 buff：官方精确值优先（skill_buff_pct），中位数兜底
        #
        # 旧代码是 me.buffs = min(3, me.buffs + 1)（虚构的叠层），
        # 但 roco-cal 的 607 个技能里**没有叠层机制**，且攻/防 buff
        # 是不同技能，一个技能不会同时加两者。
        if ka == "buff":
            _p = skill_buff_pct((skill_a or {}).get("name", "")) \
                if skill_a else None
            me.atk_buff = max(me.atk_buff, _p if _p else ATK_BUFF_MEDIAN)
            me.buffed = True
            events.append("我方使用 %s（攻击 +%.0f%%）"
                          % ((skill_a or {}).get("name", "蓄力"),
                             100 * me.atk_buff))
        if kb == "buff":
            _p = skill_buff_pct((skill_b or {}).get("name", "")) \
                if skill_b else None
            foe.atk_buff = max(foe.atk_buff, _p if _p else ATK_BUFF_MEDIAN)
            foe.buffed = True
            events.append("对方使用 %s（攻击 +%.0f%%）"
                          % ((skill_b or {}).get("name", "蓄力"),
                             100 * foe.atk_buff))

        # 变化技能（power=0）：批量策略不会选（伤害最低 1 点），
        # 但**手操窗口玩家会点**，所以必须正确处理。
        #
        # 真实游戏里 power=0 是「变化技能」：加攻/加防/回血/上印记。
        # 本模拟器只建模了**攻击增益**（skill_buff_pct 的官方精确值）；
        # 加防/回血/印记没建模，就按「无伤害」处理并明确说明，
        # 而不是像原来那样让 calc_damage 的 max(1, ...) 假装打 1 点。
        skip_act = {"a": False, "b": False}
        for who, mv, owner, kind in (("a", skill_a, me, ka),
                                     ("b", skill_b, foe, kb)):
            if kind != "skill" or mv is None or mv["power"] > 0:
                continue
            skip_act[who] = True
            if mv["cost"] <= owner.energy:
                owner.energy -= mv["cost"]
            pct = skill_buff_pct(mv["name"])
            if pct:
                owner.atk_buff = max(owner.atk_buff, pct)
                owner.buffed = True
                events.append("%s 使用 %s（攻击 +%.0f%%）"
                              % ("我方" if who == "a" else "对方",
                                 mv["name"], 100 * pct))
            else:
                events.append("%s 使用 %s（变化技能，本模拟器未建模）"
                              % ("我方" if who == "a" else "对方",
                                 mv["name"]))

        # 出手（速度快的先打）。换人方这回合不攻击。
        acts = []
        if (not switched_a and not skip_act["a"] and ka == "skill"
                and pa is not None and pa < len(me.moves)):
            mv = me.moves[pa]
            if mv["cost"] <= me.energy:
                me.energy -= mv["cost"]
                acts.append((me.spe, "a", me, foe, mv))
            else:
                events.append("我方能量不足，%s 没放出来" % mv["name"])
        if (not switched_b and not skip_act["b"] and kb == "skill"
                and pb is not None and pb < len(foe.moves)):
            mv = foe.moves[pb]
            if mv["cost"] <= foe.energy:
                foe.energy -= mv["cost"]
                acts.append((foe.spe, "b", foe, me, mv))
            else:
                events.append("对方能量不足，%s 没放出来" % mv["name"])
        # 出手顺序：速度高的先打；速度相同则随机（不能固定偏向 A 方，
        # 否则同队自对弈会 100% 偏向 A —— 实测就是这样）。
        #
        # ⚠ 这里的 rnd.random() 调用次数（= acts 的元素个数）是整个引擎
        # 唯一的随机源。改排序写法会改变 data/fixtures/sim_baseline.json
        # 的基线，动之前先想清楚。
        acts.sort(key=lambda x: (-x[0], self.rnd.random()))

        for _spe, who, atk, dfn, mv in acts:
            if not atk.alive or not dfn.alive:
                continue
            d = calc_damage(atk, dfn, mv)
            dfn.hp = max(0, dfn.hp - d)
            events.append("%s %s → %s -%d（剩 %d/%d）"
                          % ("我方" if who == "a" else "对方",
                             mv["name"], dfn.name, d, dfn.hp, dfn.max_hp))
            if not dfn.alive:
                events.append("%s 倒下" % dfn.name)

        if not any(p.alive for p in self.a):
            self._finish("b", self.turn, 0, sum(p.hp for p in self.b))
        elif not any(p.alive for p in self.b):
            self._finish("a", self.turn, sum(p.hp for p in self.a), 0)
        return events

    def auto_step(self, pol_a, pol_b):
        """两边都交给策略（批量模拟用）。"""
        me, foe = self.me, self.foe
        if me is None or foe is None:
            return []
        bench_a, bench_b = self.bench("a"), self.bench("b")
        # 把队伍对象注入策略（SmartPolicy 需要看替补的属性/技能来评分）
        for pol, team in ((pol_a, self.a), (pol_b, self.b)):
            try:
                pol._team = team
            except Exception:
                pass
        ka, pa = pol_a.choose(me, foe, bench_a, self.turn)
        kb, pb = pol_b.choose(foe, me, bench_b, self.turn)
        return self.step(ka, pa, kb, pb)


def run_match(dex, team_a, team_b, pol_a, pol_b, seed=None,
              cfg_a=None, cfg_b=None):
    """跑一局。返回 (winner, turns, hp_a, hp_b)。

    cfg_a/cfg_b（2026-11-22，UI 模拟器）：
        [{"name":..., "iv":60, "nature":"固执", "invest":["hp","atk","spe"],
          "moves":[技能 id 或名字...], "max_hp":417(可选覆盖)}, ...]
        与 team_a/team_b 按位置对应；None 的字段用默认值。

    实现：两边都交给策略，循环推进 `Battle`。手操窗口走同一个 Battle。
    """
    bt = Battle(dex, team_a, team_b, cfg_a=cfg_a, cfg_b=cfg_b, seed=seed)
    while bt.begin_turn():
        bt.auto_step(pol_a, pol_b)
    return bt.result()

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
