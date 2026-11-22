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
# 聚能一次的回能（2026-11-22 用户实测纠正：游戏里是 **+5**，原来写的 2）
ENERGY_GAIN = 5

# 技能 id -> 描述（懒加载）。技能对象（moves）里没有 desc 字段，
# 但「吸血」「先手」这些关键词在描述里，需要的时候查一下。
_SKILL_DESC = None


def _skill_desc_by_name(name):
    """按技能名查描述（供 nrc_fx 的 desc 回退路径）。"""
    global _SKILL_NAME_DESC
    if _SKILL_NAME_DESC is None:
        try:
            import sqlite3
            db = sqlite3.connect(str(paths.DB))
            try:
                _SKILL_NAME_DESC = {r[0]: (r[1] or "") for r in db.execute(
                    "SELECT name, desc FROM skills")}
            finally:
                db.close()
        except Exception:
            _SKILL_NAME_DESC = {}
    return _SKILL_NAME_DESC.get(name) or ""


_SKILL_NAME_DESC = None


def _skill_desc(skill_id):
    global _SKILL_DESC
    if _SKILL_DESC is None:
        try:
            import sqlite3
            db = sqlite3.connect(str(paths.DB))
            try:
                _SKILL_DESC = {r[0]: (r[1] or "") for r in db.execute(
                    "SELECT id, desc FROM skills")}
            finally:
                db.close()
        except Exception:
            _SKILL_DESC = {}
    return _SKILL_DESC.get(int(skill_id)) or ""

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


# 技能名 -> 官方增益（首次用到时读本地 data/external/.../skill_buff_calc.json）
_SKILL_BUFF = None


def skill_buff(skill_name):
    """变化技能给自身的官方增益：{"atk","def","taken"}（0~1 小数）。

    查不到返回 None。

    ★ 2026-11-22 两处修正
    ---------------------
    1. 原来读的是 `C:/Users/Lenovo/Downloads/.../authority/skill.json`
       这个**绝对路径**。目录一挪/删，所有增益会静默退化成
       ATK_BUFF_MEDIAN 兜底，数值全错还没有任何报错。现在改成读
       `data/external/rkpp-official/skill_buff_calc.json`（入库的固定资产，
       由 scripts/build_skill_buffs.py 生成）。
    2. 原来只取攻击（phyAtkPercent/magAtkPercent），
       **防御增益和减伤直接丢了** —— 于是「防御」这类技能在模拟器里毫无作用。
       现在 atk / def / taken 三样都取。
    """
    global _SKILL_BUFF
    if _SKILL_BUFF is None:
        p = os.path.join(ROOT, "data", "external", "rkpp-official",
                         "skill_buff_calc.json")
        try:
            with io.open(p, encoding="utf-8") as fh:
                _SKILL_BUFF = json.load(fh).get("skills") or {}
        except Exception:
            _SKILL_BUFF = {}
    return _SKILL_BUFF.get(skill_name)


def skill_buff_pct(skill_name):
    """兼容旧调用：只要攻击增益（0~1 小数），查不到返回 None。"""
    v = skill_buff(skill_name)
    return v.get("atk") if v else None


# 增益上限 +100%（2026-11-22 用户实测：力量增效叠 10 层 = 物理攻击翻倍，
# 即每层 +10%、线性叠加）。这是「层数」这个概念的数值边界，
# 不是随手拍的常数 —— 10 × 10% = 100%。
BUFF_CAP = 1.0


def add_buff(pet, atk=0.0, dfn=0.0):
    """给精灵叠增益：**线性叠加**并封顶 BUFF_CAP。

    为什么不是 max()：原来的写法是「同名 buff 不叠」，但游戏里
    `力量增效` 是可以一层层叠到 10 层（= +100%）的。用户实测指出来了。
    """
    a = max(0.0, float(atk or 0.0))
    d = max(0.0, float(dfn or 0.0))
    if a:
        pet.atk_buff = min(BUFF_CAP, pet.atk_buff + a)
    if d:
        pet.def_buff = min(BUFF_CAP, pet.def_buff + d)
    return pet.atk_buff, pet.def_buff


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
        """某只精灵的**完整**可学技能表（S5 优先，形态变体取并集）。

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

        ★ 形态变体兜底（2026-11-22 第二轮，用户指出卡瓦重）
        ---------------------------------------------------
        roco-cal 把「卡瓦重」的四个形态算成同一只（id 046），S5 里只有：

            卡瓦重（沙地附近的样子）   扬沙✗ 跺地✓
            卡瓦重（火山附近的样子）   扬沙✗ 跺地✓
            卡瓦重（草地附近的样子）   扬沙✗ 跺地✓
            卡瓦重（雪山附近的样子）   扬沙✓ 跺地✗     <- 冰系那只是因为 S5 改过

        而图鉴里的**本体名** `卡瓦重` 在 S5 里没有条目，于是退回旧表
        （有跺地、没扬沙），用户看到的还是老技能组。

        这里对没直接命中的名字，取所有 `本体（形态）` 条目的**并集**，
        并给每个技能标上 `variant` —— 只挑一个形态等于凭空删技能；
        标出形态名才能让人自己选对（UI 会显示「扬沙 · 雪山附近的样子」）。
        """
        out = []
        seen = set()
        s5 = _s5_pets()
        if name in s5:
            variants = [(name, s5[name])]
        else:
            variants = [(k, v) for k, v in s5.items()
                        if k.startswith(name) and ("（" in k or "(" in k)]
        for vn, v in variants:
            for s in v.get("skills") or []:
                mv = self.resolve_skill(s.get("name"))
                if not mv or mv["id"] in seen:
                    continue
                seen.add(mv["id"])
                if with_meta:
                    mv = dict(mv, unlock=s.get("unlock") or "")
                    if len(variants) > 1:
                        mv["variant"] = vn
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
        self.taken_mult = 1.0   # 受到的伤害倍率（减伤技能/特性，<1 表示减伤）
        self.buffed = False     # 是否已经用过 buff 技能（不再叠）
        # ---- 特性（2026-11-22）
        #
        # 规则来自 data/traits.json（roco-cal 的结构化特性 + 3 条文本推导）。
        # 只应用能判定的条件，见 src/trait_lib.py 的 SUPPORTED 白名单。
        #
        # 实测两个用户点名的例子：
        #   · `专注力`（音速犬）: 入场首回合物攻 +100%  -> trait_atk_buff=1.0
        #   · `渴求`（蝙蝠类）  : 入场获得 30% 吸血      -> 命中后回血
        import trait_lib
        self.trait_spec = trait_lib.spec(self.trait)
        # 特性增益分两类：
        #   trait_atk_buff/def_buff  常驻（「携带的技能都<4耗能」这类）
        #   trait_tmp_atk/tmp_def    只在**本回合**有效（「入场首回合」这类）
        self.trait_atk_buff = 0.0
        self.trait_def_buff = 0.0
        self.trait_tmp_atk = 0.0
        self.trait_tmp_def = 0.0
        a, d = trait_lib.carrier_buffs(self)
        self.trait_atk_buff += a
        self.trait_def_buff += d
        # 按当前倒下数动态计算的增益（悲悯/悼亡）走 recalc_dead_buffs()，
        # 每次出手前重算 —— 原文 timing=current，是持续状态。
        # _dead_trait 标记这条特性属于 timing=current:pets_dead_count，
        # 战斗中每回合重算 trait_atk_buff = base + dead_buffs()
        self._dead_trait = any(
            r.get("mode") == "pets_dead_count"
            for r in trait_lib.supported_rules(self.trait or ""))
        self._dead_base_atk = self.trait_atk_buff
        self._dead_base_def = self.trait_def_buff
        # 特性速度加成（烈冰钻/变形活画），begin_turn 重算
        self._trait_speed = 0
        # 全神贯注（入场+100%，每行动-20%）：剩余层数
        self._focus_layers = 0
        self._trait_first_done = False
        # nrc-ai 手写效果表用到的状态（异常状态 + 本回合威力修正）
        self.status = {}
        self.nrc_power_mult = 1.0
        self.nrc_power_bonus = 0
        self._nrc_entered = False
        # S6.7：蓄力 / 迸发 / 打断
        self.charging = 0          # >0 = 蓄力中（下回合释放所选技能）
        self.charge_skill = None   # 蓄力的技能名
        self._burst_used = False   # 入场后首行动（迸发）已消耗
        # S6.11：传动槽位（机械系技能会移动位置）——索引指向 moves
        self.skill_slots = list(range(len(self.moves)))
        # 传动叠加层数（回合开始时按「传动X」移动）
        self._transmission = 0

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

def calc_damage(attacker, defender, move, power_mult=None, power_bonus=None,
                first=False):
    """用真实的 roco-cal 引擎算伤害（含特性/印记修正）。

    增益按 roco-cal 的真实公式处理（damresult.py）：
        buff_numerator = 1 + atk_up + enemy_def_down
        buff_denominator = 1 + atk_down + enemy_def_up
    也就是"攻方攻击提升"和"守方防御下降"在**同一个分子**里相加，
    而不是各自独立相乘。这里只实现攻方增益这一侧（守方减益暂不模拟）。

    特性修正（2026-11-22）：
      · 威力倍率/加成：trait_lib.attack_mods（carried/usage/action 等 mode）
      · 受击减免    ：trait_lib.incoming_mult
      · 首回合增益  ：attacker.trait_atk_buff（Battle 在回合开始时挂上）
    策略也直接调这个函数，所以**决策和结算用的是同一套修正** ——
    否则策略会按"无特性"估伤害，选出来的招和实际对不上。
    """
    import damage_v2 as DMG
    import statmodel as S
    import trait_lib
    stab = 1.25 if move["type"] in attacker.types else 1.0
    eff = S.type_effect(move["type"], defender.types)
    if power_mult is None or power_bonus is None:
        try:
            foe_cost = sum(int(m.get("cost") or 0) for m in defender.moves)
        except Exception:
            foe_cost = 0
        mods = trait_lib.attack_mods(attacker, move,
                                     foe_total_cost=foe_cost)
        power_mult = mods["power_mult"]
        power_bonus = mods["power_bonus"]
        self_damage_pct = float(mods.get("self_damage_pct") or 0)
    else:
        self_damage_pct = 0.0
    power = max(0, int(int(move["power"]) * float(power_mult)
                       + int(power_bonus)))
    # 天气 + 印记（2026-11-22）：两者都作用在**威力**上，和 roco-cal 的
    # effective_power 一致。天气/印记挂在 Battle 上，通过 pet._battle 取；
    # 拿不到就当没有（策略单独调用 calc_damage 时也是这个行为）。
    import battle_fx
    battle = getattr(attacker, "_battle", None)
    if battle is not None:
        elem = move.get("type")
        wm = battle_fx.weather_power_mult(getattr(battle, "weather", "none"),
                                          elem)
        side = getattr(attacker, "_side", None)
        mm = battle_fx.mark_mods(
            (getattr(battle, "marks", {}) or {}).get(side or "a") or {},
            move.get("name") or "", elem, first_attack=first,
            is_attack=int(move.get("power") or 0) > 0)
        power = max(0, int(power * wm * mm["power_mult"] + mm["power_bonus"]))
    # nrc-ai 手写特性的常驻威力修正（`目空` / `破空` 这类 PASSIVE）
    power = max(0, int(power * getattr(attacker, "nrc_power_mult", 1.0)
                       + getattr(attacker, "nrc_power_bonus", 0)))
    # 迸发（入场后首次行动）：描述里的加成。_burst_used 由 Battle 标记。
    burst_applied = False
    if not getattr(attacker, "_burst_used", True):
        import cond_fx as _cf
        desc = (_skill_desc(move.get("id")) or "") if move.get("id") else (
            move.get("desc") or "")
        bb = _cf.burst_bonus(desc)
        if bb:
            if "power_mult" in bb:
                power = int(power * bb["power_mult"])
            if "power_bonus" in bb:
                power += bb["power_bonus"]
            burst_applied = True
    atk = attacker.atk * (1.0 + attacker.atk_buff
                          + getattr(attacker, "trait_atk_buff", 0.0)
                          + getattr(attacker, "trait_tmp_atk", 0.0))
    dfn = defender.dfn * (1.0 + defender.def_buff
                          + getattr(defender, "trait_def_buff", 0.0)
                          + getattr(defender, "trait_tmp_def", 0.0))
    dmg = DMG.damage(int(atk), int(dfn), power,
                     type_eff=eff, stab=stab)
    dmg *= trait_lib.incoming_mult(defender, move)
    dmg *= getattr(defender, "taken_mult", 1.0)
    calc_damage.last_self_damage_pct = self_damage_pct
    calc_damage._last_burst = burst_applied
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
                 seed=None, magic_a=None, magic_b=None):
        self.dex = dex
        self.rnd = random.Random(seed)
        self.a = self._build(team_a, cfg_a)
        self.b = self._build(team_b, cfg_b)
        self.ia = self.ib = 0
        self.turn = 0
        self.done = None            # None / "a" / "b"
        self.hp_a = self.hp_b = 0
        # 魔力（4 格生命制）：每倒下一只 -1，归 0 判负
        import battle_fx as _bf
        self.magic = {"a": int(magic_a if magic_a is not None
                               else _bf.MAGIC_LIVES),
                      "b": int(magic_b if magic_b is not None
                               else _bf.MAGIC_LIVES)}
        # 天气 / 印记（2026-11-22，数据来自 roco-cal battle_effects）
        import battle_fx
        self.weather = "none"
        self.marks = {"a": {}, "b": {}}
        self.pending = []          # begin_turn 里产生的待播报事件
        self._nrc_entered = set()  # (side, id(pet)) 已触发过入场效果的
        # 奉献（S6.13）：己方队伍计数器，影响啃咬/虫群系技能
        self.devotion = {"a": 0, "b": 0}
        self._first_turn_done = set()  # 首回合增益已触发（可轮转重置）
        for side, team in (("a", self.a), ("b", self.b)):
            for p in team:
                p._battle = self
                p._side = side

    def set_weather(self, weather_id):
        import battle_fx
        if weather_id in battle_fx.weather_ids() or weather_id == "none":
            self.weather = weather_id
        return self.weather

    def set_marks(self, side, field, stacks):
        """旧接口（手填层数）-> 新印记 id。保留兼容。"""
        import battle_fx
        mid = field[:-7] if str(field).endswith("_stacks") else field
        if int(stacks or 0) > 0:
            battle_fx.set_mark(self.marks.setdefault(side, {}), mid)
        else:
            self.marks.setdefault(side, {}).pop(mid, None)
        return self.marks[side]

    def add_mark(self, side, mark_id):
        """施加印记（自动顶掉同类），返回被顶掉的 id 或 None。"""
        import battle_fx
        _added, gone = battle_fx.set_mark(self.marks.setdefault(side, {}),
                                          mark_id)
        return gone

    def eff_cost(self, side, move):
        """技能的实际能耗（湿润 -1 / 蓄势 +1 / 骑士敕令能耗操纵，最低 0）。"""
        import battle_fx
        base = int((move or {}).get("cost") or 0)
        d = battle_fx.mark_cost_delta(self.marks.get(side) or {})
        # 骑士敕令/瞬间零度的能耗操纵（跨回合，_cost_up_next 由应对挂上）
        cost_up = 0
        cu = getattr(self, "_cost_up_next", None)
        if cu:
            cost_up += cu.get(side, 0)
        return max(0, base + d + cost_up)

    def eff_spe(self, side, pet):
        """实际速度（减速印记 -10 / 特性 speed_buff）。"""
        import battle_fx
        sp = pet.spe + battle_fx.mark_speed_delta(self.marks.get(side) or {})
        # 特性给的速度加成（烈冰钻：敌方总能耗>25 时 +75；变形活画：
        # 敌方增益层 x5）。挂在 pet._trait_speed，由 begin_turn 重算。
        sp += int(getattr(pet, "_trait_speed", 0) or 0)
        return sp

    def mark_state(self):
        return {"weather": self.weather,
                "weather_label": __import__("battle_fx").weather_label(
                    self.weather),
                "marks": self.marks}

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
                # ★ resolve 失败时用 desc_fx/authority_fx 的效果构造
                #   虚拟技能条目（power=0），确保 heal/buff 等不丢——
                #   氧循环 heal 回复 0 就是这条路径漏了。
                picked = []
                import nrc_fx as _nfx
                for x in c["moves"]:
                    mv = self.dex.resolve_skill(x)
                    if mv:
                        picked.append(mv)
                    else:
                        fx = _nfx.desc_effects(str(x))
                        if fx:
                            picked.append({"id": 0, "name": str(x),
                                           "power": 0, "cost": 0,
                                           "type": None})
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
        # 特性：入场首回合的临时增益（`专注力` 这类）+ 倒下数动态增益
        # （悲悯/悼亡，timing=current，每次出手前重算）
        import trait_lib
        import battle_fx as _bf
        for p in (self.me, self.foe):
            if p is None:
                continue
            trait = p.trait or ""
            # 全神贯注（风暴战犬）：入场 +100%，每次行动 -20%
            if trait == "全神贯注":
                if not getattr(p, "_focus_init", False):
                    p._focus_layers = 5          # 100% / 20% = 5 层
                    p._focus_init = True
                p.trait_tmp_atk = 0.2 * p._focus_layers
            # 入场首回合增益（专注力/全神贯注这类）：**每次上场**都触发。
            # 用户实测纠正（2026-11-22）：换下再换上，第一回合攻击照样翻倍 ——
            # 「入场」的定义是可轮转的。换人时 clear_buffs 重置
            # _trait_first_done，所以这里直接检查该标记。
            if not getattr(p, "_trait_first_done", True):
                a, d = trait_lib.first_turn_buffs(p)
                p.trait_tmp_atk += a
                p.trait_tmp_def += d
                p._trait_first_done = True
            # 倒下数/持续状态增益：每回合开始时动态重算（timing=current）
            if p._dead_trait:
                da, dd = trait_lib.dynamic_buffs(p)
                p.trait_atk_buff = p._dead_base_atk + da
                p.trait_def_buff = p._dead_base_def + dd
            # 特性速度加成：变形活画（敌方增益层 x5）、
            # 烈冰钻（敌方总能耗>25 时 +75）
            sp = 0
            enemy = self.foe if p is self.me else self.me
            if trait == "变形活画" and enemy is not None:
                layers = int(round(enemy.atk_buff / 0.1)) + \
                    int(round(enemy.def_buff / 0.1))
                sp += 5 * layers
            if trait in ("烈冰钻", "烈冰钻钻") and enemy is not None:
                foe_cost = sum(int(m.get("cost") or 0)
                               for m in enemy.moves)
                if foe_cost > 25:
                    sp += 75
            p._trait_speed = sp
        # nrc-ai 手写效果（ON_ENTER / ON_BATTLE_START / PASSIVE）只在
        # **开局**或**换人入场**时触发一次；站场期间不重复。
        # 触发记录挂在 Battle 上（per side + pet id），换人入场时那只
        # 精灵是新出现的 id，天然会再触发一次 —— 这正是原文语义
        # 「入场时……」。
        import nrc_fx
        for p, other in ((self.me, self.foe), (self.foe, self.me)):
            if p is None:
                continue
            if trait_lib.supported_rules(getattr(p, "trait", None)):
                continue
            key = (getattr(p, "_side", "?"), id(p))
            if key in self._nrc_entered:
                continue
            # roco-cal 已覆盖的（含悲悯/悼亡这类 current 特性）不重复应用
            if trait_lib.supported_rules(getattr(p, "trait", None)):
                self._nrc_entered.add(key)      # 标记过，但不应用 nrc
                continue
            self._nrc_entered.add(key)
            ctx = {}
            evs = (nrc_fx.trait_fire(p, other, "ON_ENTER", ctx)
                   + nrc_fx.trait_fire(p, other, "ON_BATTLE_START", ctx)
                   + nrc_fx.trait_fire(p, other, "PASSIVE", ctx))
            p.nrc_power_mult *= ctx.get("power_mult", 1.0)
            p.nrc_power_bonus += ctx.get("power_bonus", 0)
            for e in evs:
                self.pending.append("T%d %s" % (self.turn, e))
        return True

    def step(self, ka, pa, kb, pb):
        """执行一个回合两边的动作，返回本回合的事件列表（给 UI 展示）。

        动作格式同 Policy.choose 的返回值 (kind, payload)，
        kind ∈ {"skill","energy","switch","buff"}。

        结算用到的 battle_fx（场地/魔力）在方法里引入，别名 _bf。
        """
        import battle_fx as _bf
        events = []
        import battle_fx
        import trait_lib
        import nrc_fx
        me, foe = self.me, self.foe
        if me is None or foe is None:
            return events
        if self.pending:
            events.extend(self.pending)
            self.pending = []
        # 把「本回合生效的特性增益」写进战报：`专注力` 这类只在入场首回合
        # 有效的东西，如果界面上只有一个忽高忽低的伤害数字，很容易被
        # 误认为"一直有加倍 buff"（2026-11-22 用户就是这样报的）。
        for who, p in (("我方", me), ("对方", foe)):
            ta = getattr(p, "trait_tmp_atk", 0.0)
            td = getattr(p, "trait_tmp_def", 0.0)
            if ta or td:
                events.append("%s 特性生效：物攻 +%.0f%% 双防 +%.0f%%（仅本回合）"
                              % (who, 100 * ta, 100 * td))
        bench_a, bench_b = self.bench("a"), self.bench("b")

        # 打断标记（应对判定在下面，这里先声明）
        interrupted = {"a": False, "b": False}
        # 能耗操纵（骑士敕令/瞬间零度：应对成功时挂上，**本回合内**的
        # 消费走局部；跨回合的部分挂 self._cost_up_next）
        enemy_cost_up = {"a": 0, "b": 0}

        # 取出双方本回合选的技能对象（buff 精确值要用它的名字）
        skill_a = me.moves[pa] if (ka == "skill" and pa is not None
                                   and pa < len(me.moves)) else None
        skill_b = foe.moves[pb] if (kb == "skill" and pb is not None
                                    and pb < len(foe.moves)) else None

        # nrc-ai 手写的**技能效果**（139 个技能）与**特性 ON_USE_SKILL**。
        # 属性增益优先走官方数值（skill_buff），nrc 只补它没有的东西：
        # 回血 / 回能 / 异常状态 / 印记 / 减伤。
        #
        # ⚠ 这里**不**处理「本手威力倍率」类原语（POWER_DYNAMIC 要 应对判定、
        #   ENTRY_BUFF_PER_SKILL_COUNT 要携带技能统计），它们只在专门的
        #   时机算得准，硬塞进这一手只会算错。
        for owner, other, mv in ((me, foe, skill_a), (foe, me, skill_b)):
            if mv is None:
                continue
            ctx = {"_from_desc": False}
            if not trait_lib.supported_rules(getattr(owner, "trait", None)):
                events.extend(nrc_fx.trait_fire(owner, other, "ON_USE_SKILL",
                                                ctx))
            events.extend(nrc_fx.skill_fire(owner, other, mv.get("name") or "",
                                            "PRE_USE", ctx))
            events.extend(nrc_fx.skill_fire(owner, other, mv.get("name") or "",
                                            "ON_USE", ctx))
            import cond_fx
            # 脱离（S6.10）：power=0 且在 leave_skills 集合 → 走换人路径
            # （自己离场，替补入场，本回合入场的精灵免疫离场效果——
            # mark_switch_in 由 step("switch") 处理）
            # ⚠ tag 必须在 owner 替换之前记（脱离后 owner 指向新精灵）
            # 返场（S6.12）：「使敌方精灵返场」→ 敌方增益清空 + 重置入场
            # 标记（与换人同一份原文：增益/减益状态在更换精灵之后会被清除）
            import battle_fx as _bfx
            _target_of = foe if owner is me else me
            if "使敌方精灵返场" in (mv.get("desc") or
                                   _skill_desc(mv.get("id")) or ""):
                _tag_r = "我方" if owner is me else "对方"
                nrc_fx.clear_buffs(_target_of)
                events.append("%s 使用 %s：敌方返场（增益/减益清除）"
                              % (_tag_r, mv["name"]))
                continue
            if mv.get("name") in _bfx.leave_skills(self.dex):
                _tag = "我方" if owner is me else "对方"
                side_lv = "a" if owner is me else "b"
                bench_lv = self.bench(side_lv)
                if bench_lv:
                    old_lv = owner.name
                    if owner is me:
                        nrc_fx.clear_buffs(me)
                        self.ia = bench_lv[0]
                        self.a[self.ia].energy = ENERGY_CAP
                        me = self.a[self.ia]
                        switched_a = True
                    else:
                        nrc_fx.clear_buffs(foe)
                        self.ib = bench_lv[0]
                        self.b[self.ib].energy = ENERGY_CAP
                        foe = self.b[self.ib]
                        switched_b = True
                    events.append("%s 使用 %s 脱离，%s 换上 %s"
                                  % (_tag, mv["name"], old_lv, owner.name))
                    continue
                else:
                    events.append("%s 没有可换的替补，%s 脱离失败"
                                  % (_tag, mv["name"]))
                    continue
            target_of = foe if owner is me else me

            dv = nrc_fx.desc_effects(mv.get("name") or "")
            # authority heal_pct/buff_atk/buff_def（S7.3）：
            # power>0 的攻击技也可能带这些效果（氧循环=魔伤+回复10%）
            # 放在蓄力检查之前，所有技能统一消费
            _heal = dv.get("heal_pct")
            if _heal and owner.alive:
                _before = owner.hp
                owner.hp = min(owner.max_hp,
                               owner.hp + max(1, int(owner.max_hp * _heal)))
                if owner.hp > _before:
                    events.append("%s 回复 %d（剩 %d/%d）"
                                  % ("我方" if owner is me else "对方",
                                     owner.hp - _before,
                                     owner.hp, owner.max_hp))
            _ba = dv.get("buff_atk")
            _bd = dv.get("buff_def")
            if _ba or _bd:
                add_buff(owner, _ba or 0, _bd or 0)
                owner.buffed = True
                _bits = []
                if _ba:
                    _bits.append("攻击 +%.0f%%" % (100 * _ba))
                if _bd:
                    _bits.append("防御 +%.0f%%" % (100 * _bd))
                events.append("%s %s 属性提升（%s）"
                              % ("我方" if owner is me else "对方",
                                 mv["name"], "、".join(_bits)))
            # 蓄力：选了蓄力技 → 本回合蓄力（不攻击），下回合才能释放
            is_charge = mv.get("name") in cond_fx.charge_skills(self.dex)
            if is_charge and getattr(owner, "charging", 0) == 0:
                owner.charging = 1
                owner.charge_skill = mv["name"]
                events.append("%s 开始蓄力（%s，下回合释放）"
                              % ("我方" if owner is me else "对方",
                                 mv["name"]))
                continue            # 蓄力回合不攻击

            # 条件型状态施加（灵魂灼伤/溶解扩散/溶解腐蚀）：
            # 按本次技能系别选状态，优先于无条件 status
            import cond_fx
            cond = cond_fx.cond_status_apply(mv.get("name") or "",
                                             mv.get("type") or "")
            st = dict(dv.get("status") or {})
            if cond:
                st = {k: n for k, n in cond}     # 覆盖无条件抽取
            for key, stacks in st.items():
                ctx["_from_desc"] = True
                ev2 = nrc_fx._bump_status(target_of, key, stacks)
                if ev2:
                    events.append("%s（%s 描述）" % (ev2, mv["name"]))
            mk = dv.get("mark") or {}
            side_of = "a" if owner is me else "b"
            for mid, stacks in mk.items():
                _added, gone = battle_fx.set_mark(
                    self.marks.setdefault(side_of, {}), mid)
                suffix = "（顶掉 {}）".format(gone) if gone else ""
                events.append("{} 施加印记 {}{}".format(mv["name"], mid,
                                                        suffix))
            # 奉献（S6.13）：desc 含「获得N次奉献」→ 队伍计数+N
            import re as _re_d
            _dev_txt = (dv.get("desc") or "") or                 _skill_desc(mv.get("id")) or ""
            m_dev = _re_d.search(r"获得\s*(\d+)\s*次奉献", _dev_txt)
            if m_dev:
                n_dev = int(m_dev.group(1))
                side_d = "a" if owner is me else "b"
                self.devotion[side_d] = self.devotion.get(side_d, 0) + n_dev
                events.append("%s 获得奉献 %d 次（累计 %d）"
                              % ("我方" if owner is me else "对方",
                                 n_dev, self.devotion[side_d]))
            eg = dv.get("energy_gain")
            if eg and owner.alive:
                before = owner.energy
                owner.energy = min(ENERGY_CAP, owner.energy + int(eg))
                if owner.energy > before:
                    events.append("%s 回能 +%d（%d）"
                                  % (mv["name"], owner.energy - before,
                                     owner.energy))
            ed = dv.get("energy_drain")
            if ed and target_of.alive:
                before = target_of.energy
                target_of.energy = max(0, target_of.energy - int(ed))
                if target_of.energy < before:
                    events.append("%s 吸走对方能量 -%d（剩 %d）"
                                  % (mv["name"], before - target_of.energy,
                                     target_of.energy))
        # 应对（counter）：两边技能都定了才判得出。谁应对成功谁吃收益。
        # 游戏内原文：**应对成功时无视速度差，必定先手**。所以这里记一个
        # 标记，后面排出手顺序时优先。另外「先手 +N」的技能也提优先级。
        #
        # 打断（S6.8）：应对成功时，若我方技能带「打断」→ 敌方本回合
        # 技能作废（含 buff/状态效果）。
        counter_first = {"a": False, "b": False}
        for owner, other, mv, omv, side in (
                (me, foe, skill_a, skill_b, "a"),
                (foe, me, skill_b, skill_a, "b")):
            if mv is None or omv is None:
                continue
            _ctx = {}
            evs, hit = nrc_fx.counter_fire(owner, other,
                                           mv.get("name") or "", omv,
                                           _ctx)
            for e in evs:
                events.append(e)
            # 能耗操纵（骑士敕令/瞬间零度）：应对成功 → 对方**下一回合**
            # 技能能耗+N（挂 self._cost_up_next，begin_turn 时转入本回合）
            if hit and _ctx.get("enemy_cost_up"):
                if not hasattr(self, "_cost_up_next"):
                    self._cost_up_next = {"a": 0, "b": 0}
                self._cost_up_next["b" if side == "a" else "a"] = \
                    _ctx["enemy_cost_up"]
            if hit:
                counter_first[side] = True
                # 打断：我方技能描述含「打断」→ 对方本回合效果作废
                _desc_txt = (_skill_desc(mv.get("id"))
                             if mv.get("id") else "") or ""
                # 能耗操纵：骑士敕令/瞬间零度（敌方本回合技能能耗+3）
                import re as _re
                m_up = _re.search(r"能耗\s*\+\s*(\d+)", _desc_txt)
                if m_up:
                    enemy_cost_up["b" if side == "a" else "a"] = \
                        int(m_up.group(1))
                import cond_fx as _cf
                if (mv.get("name") in _cf.interrupt_skills(self.dex)
                        or "打断" in _desc_txt):
                    interrupted["b" if side == "a" else "a"] = True
                    events.append("%s 打断了对方的 %s！"
                                  % ("我方" if side == "a" else "对方",
                                     omv.get("name", "技能")))
                if not trait_lib.supported_rules(
                        getattr(owner, "trait", None)):
                    for e in nrc_fx.trait_fire(owner, other,
                                               "ON_COUNTER_SUCCESS"):
                        events.append(e)
        # 处理聚能
        if ka == "energy":
            me.energy = min(ENERGY_CAP, me.energy + ENERGY_GAIN)
            events.append("我方聚能（能量 %d）" % me.energy)
        if kb == "energy":
            foe.energy = min(ENERGY_CAP, foe.energy + ENERGY_GAIN)
            events.append("对方聚能（能量 %d）" % foe.energy)

        # 处理换人（换人回合不攻击，换上新精灵）
        # 迅捷（S6.9）：主动换人入场时，使用第一个能量满足且带迅捷的技能
        # —— 追加一次出手（原文：入场时使用迅捷技）
        switched_a = switched_b = False
        swift_acts = []
        if ka == "switch" and bench_a:
            old = me.name
            nrc_fx.clear_buffs(me)          # 换人清增益/减益，印记保留
            self.ia = bench_a[min(pa or 0, len(bench_a) - 1)]
            self.a[self.ia].energy = ENERGY_CAP
            me = self.a[self.ia]
            switched_a = True
            events.append("我方换下 %s（增益/减益已清除），换上 %s"
                          % (old, me.name))
            sw = battle_fx.mark_switch_in(self.marks.get("a") or {})
            if sw["hp_pct"]:
                d = max(1, int(me.max_hp * sw["hp_pct"]))
                me.hp = max(0, me.hp - d)
                events.append("棘刺印记：%s 失去 %d（剩 %d/%d）"
                              % (me.name, d, me.hp, me.max_hp))
            if sw["energy"]:
                me.energy = max(0, me.energy + sw["energy"])
                events.append("降灵印记：%s 能量 %d" % (me.name, me.energy))
            if sw["debuff"]:
                me.atk_buff = max(0.0, me.atk_buff - 0.1 * sw["debuff"])
                me.def_buff = max(0.0, me.def_buff - 0.1 * sw["debuff"])
                events.append("暗涌印记：%s 获得 %d 层属性减益"
                              % (me.name, sw["debuff"]))
            # 迅捷：换人入场 → 自动出第一个能量满足且带迅捷的技能
            import cond_fx as _cf
            for m in me.moves:
                if m["name"] in _cf.swift_skills(self.dex) and m["cost"] <= me.energy:
                    swift_acts.append((me.spe + 999, "a", me, foe, m))
                    events.append("迅捷：%s 入场立即使用 %s"
                                  % (me.name, m["name"]))
                    break
        if kb == "switch" and bench_b:
            old = foe.name
            nrc_fx.clear_buffs(foe)
            self.ib = bench_b[min(pb or 0, len(bench_b) - 1)]
            self.b[self.ib].energy = ENERGY_CAP
            foe = self.b[self.ib]
            switched_b = True
            events.append("对方换下 %s（增益/减益已清除），换上 %s"
                          % (old, foe.name))
            sw = battle_fx.mark_switch_in(self.marks.get("b") or {})
            if sw["hp_pct"]:
                d = max(1, int(foe.max_hp * sw["hp_pct"]))
                foe.hp = max(0, foe.hp - d)
                events.append("棘刺印记：%s 失去 %d（剩 %d/%d）"
                              % (foe.name, d, foe.hp, foe.max_hp))
            if sw["energy"]:
                foe.energy = max(0, foe.energy + sw["energy"])
                events.append("降灵印记：%s 能量 %d" % (foe.name, foe.energy))
            if sw["debuff"]:
                foe.atk_buff = max(0.0, foe.atk_buff - 0.1 * sw["debuff"])
                foe.def_buff = max(0.0, foe.def_buff - 0.1 * sw["debuff"])
                events.append("暗涌印记：%s 获得 %d 层属性减益"
                              % (foe.name, sw["debuff"]))
            # 迅捷（对方换人入场）
            for m in foe.moves:
                if m["name"] in _cf.swift_skills(self.dex) and m["cost"] <= foe.energy:
                    swift_acts.append((foe.spe + 999, "b", foe, me, m))
                    events.append("迅捷：%s 入场立即使用 %s"
                                  % (foe.name, m["name"]))
                    break

        # 处理 buff：官方精确值优先（skill_buff_pct），中位数兜底
        #
        # 旧代码是 me.buffs = min(3, me.buffs + 1)（虚构的叠层），
        # 但 roco-cal 的 607 个技能里**没有叠层机制**，且攻/防 buff
        # 是不同技能，一个技能不会同时加两者。
        if ka == "buff":
            _v = skill_buff((skill_a or {}).get("name", "")) if skill_a else None
            if _v:
                if _v.get("atk") or _v.get("def"):
                    add_buff(me, _v.get("atk"), _v.get("def"))
                if _v.get("taken"):
                    me.taken_mult = min(me.taken_mult, 1.0 - _v["taken"])
            else:
                # 查不到才用中位数兜底（原来 0.0 也会掉进兜底，是错的：
                # 「防御」这种纯防御技能会被当成 +70% 攻击）
                add_buff(me, ATK_BUFF_MEDIAN)
            me.buffed = True
            events.append("我方使用 %s（攻击 +%.0f%% 防御 +%.0f%%）"
                          % ((skill_a or {}).get("name", "蓄力"),
                             100 * me.atk_buff, 100 * me.def_buff))
        if kb == "buff":
            _v = skill_buff((skill_b or {}).get("name", "")) if skill_b else None
            if _v:
                if _v.get("atk") or _v.get("def"):
                    add_buff(foe, _v.get("atk"), _v.get("def"))
                if _v.get("taken"):
                    foe.taken_mult = min(foe.taken_mult, 1.0 - _v["taken"])
            else:
                add_buff(foe, ATK_BUFF_MEDIAN)
            foe.buffed = True
            events.append("对方使用 %s（攻击 +%.0f%% 防御 +%.0f%%）"
                          % ((skill_b or {}).get("name", "蓄力"),
                             100 * foe.atk_buff, 100 * foe.def_buff))

        # 变化技能（power=0）：批量策略不会选（伤害最低 1 点），        # 但**手操窗口玩家会点**，所以必须正确处理。
        #
        # 真实游戏里 power=0 是「变化技能」：加攻/加防/回血/上印记。
        # 本模拟器只建模了**攻击增益**（skill_buff_pct 的官方精确值）；
        # 加防/回血/印记没建模，就按「无伤害」处理并明确说明，
        # 而不是像原来那样让 calc_damage 的 max(1, ...) 假装打 1 点。
        # ★ 打断（S6.8）：被打断的技能在这里 continue（效果作废）
        skip_act = {"a": False, "b": False}
        for who, mv, owner, kind in (("a", skill_a, me, ka),
                                     ("b", skill_b, foe, kb)):
            if kind != "skill" or mv is None or mv["power"] > 0:
                continue
            side_key = "a" if who == "a" else "b"
            if interrupted.get(side_key):
                events.append("%s 的 %s 被打断，效果无效"
                              % ("我方" if who == "a" else "对方",
                                 mv["name"]))
                continue
            skip_act[who] = True
            if mv["cost"] <= owner.energy:
                owner.energy -= mv["cost"]
            v = skill_buff(mv["name"])
            if v and (v.get("atk") or v.get("def") or v.get("taken")):
                bits = []
                if v.get("atk") or v.get("def"):
                    a, d = add_buff(owner, v.get("atk"), v.get("def"))
                    if v.get("atk"):
                        bits.append("攻击 +%d%%（累计 %d%%）"
                                    % (round(100 * v["atk"]), round(100 * a)))
                    if v.get("def"):
                        bits.append("防御 +%d%%（累计 %d%%）"
                                    % (round(100 * v["def"]), round(100 * d)))
                if v.get("taken"):
                    owner.taken_mult = min(owner.taken_mult,
                                           1.0 - v["taken"])
                    bits.append("受到伤害 -%d%%" % round(100 * v["taken"]))
                owner.buffed = True
                events.append("%s 使用 %s（%s）"
                              % ("我方" if who == "a" else "对方",
                                 mv["name"], "、".join(bits)))
            else:
                events.append("%s 使用 %s（变化技能，本模拟器未建模）"
                              % ("我方" if who == "a" else "对方",
                                 mv["name"]))

        # 出手（速度快的先打）。换人方这回合不攻击。
        acts = []
        pri_a = battle_fx.parse_priority(
            (skill_a or {}).get("desc") or "") if skill_a else 0
        pri_b = battle_fx.parse_priority(
            (skill_b or {}).get("desc") or "") if skill_b else 0
        # 描述抽取表里的先手度（与 parse_priority 同源）
        if skill_a is not None and not pri_a:
            pri_a = (nrc_fx.desc_effects(skill_a.get("name") or {})
                     .get("priority") or 0)
        if skill_b is not None and not pri_b:
            pri_b = (nrc_fx.desc_effects(skill_b.get("name") or {})
                     .get("priority") or 0)
        # 蓄力完成的技能本回合释放（charging 归 0）
        charge_release = {"a": False, "b": False}
        if getattr(me, "charging", 0) == 1 and ka == "skill":
            mv_a = me.moves[pa] if pa is not None and pa < len(me.moves) else None
            if mv_a and mv_a.get("name") == getattr(me, "charge_skill", None):
                me.charging = 0
                charge_release["a"] = True
                events.append("我方 %s 蓄力完成，释放！" % mv_a["name"])
        if getattr(foe, "charging", 0) == 1 and kb == "skill":
            mv_b = foe.moves[pb] if pb is not None and pb < len(foe.moves) else None
            if mv_b and mv_b.get("name") == getattr(foe, "charge_skill", None):
                foe.charging = 0
                charge_release["b"] = True
                events.append("对方 %s 蓄力完成，释放！" % mv_b["name"])
        if (not switched_a and not skip_act["a"] and ka == "skill"
                and pa is not None and pa < len(me.moves)):
            # 蓄力中的回合：只有正在释放蓄力完成技的才出手，其他技不上
            if getattr(me, "charging", 0) == 1 and not charge_release["a"]:
                events.append("我方蓄力中（%s），本回合无法行动"
                              % getattr(me, "charge_skill", "?"))
            else:
                mv = me.moves[pa]
                cost = self.eff_cost("a", mv) + enemy_cost_up.get("a", 0)
                if cost <= me.energy:
                    me.energy -= cost
                    acts.append((self.eff_spe("a", me), "a", me, foe, mv))
                else:
                    events.append("我方能量不足（含能耗操纵 +%.0f），%s 没放出来"
                                  % (enemy_cost_up.get("a", 0), mv["name"]))
        if (not switched_b and not skip_act["b"] and kb == "skill"
                and pb is not None and pb < len(foe.moves)):
            if getattr(foe, "charging", 0) == 1 and not charge_release["b"]:
                events.append("对方蓄力中（%s），本回合无法行动"
                              % getattr(foe, "charge_skill", "?"))
            else:
                mv = foe.moves[pb]
                cost = self.eff_cost("b", mv) + enemy_cost_up.get("b", 0)
                if cost <= foe.energy:
                    foe.energy -= cost
                    acts.append((self.eff_spe("b", foe), "b", foe, me, mv))
                else:
                    events.append("对方能量不足（含能耗操纵 +%.0f），%s 没放出来"
                                  % (enemy_cost_up.get("b", 0), mv["name"]))
        # 听桥威力镜像（S6.13）：应对攻击成功 → 威力=对方技能威力
        for _side, _my, _foe in (("a", me, foe), ("b", foe, me)):
            if not counter_first.get(_side):
                continue
            _mv = skill_a if _side == "a" else skill_b
            if _mv and _mv.get("name") == "听桥":
                _omv = skill_b if _side == "a" else skill_a
                if _omv and (_omv.get("power") or 0) > 0:
                    _mv["power"] = _omv["power"]     # 威力镜像
                    _mv["type"] = _omv.get("type") or _mv.get("type")
                    events.append("%s 听桥镜像 %s（威力 %d）"
                                  % ("我方" if _side == "a" else "对方",
                                     _omv["name"], _omv["power"]))
        # 迅捷技追加到 acts（优先于所有普通出手 —— 原文：入场立即使用）
        acts.extend(swift_acts)
        # 出手顺序：**应对成功方必定先手**（无视速度），其次看技能先手度
        # （原文：先手度更高的技能忽略速度差异优先释放，可叠加），
        # 再按速度；速度相同则随机（不能固定偏向 A，否则自对弈 100% 偏 A）。
        #
        # ⚠ rnd.random() 的调用次数（= acts 的元素个数）是整个引擎唯一的
        # 随机源，改排序写法会改变 data/fixtures/sim_baseline.json 的基线。
        acts.sort(key=lambda x: (-(1 if counter_first.get(x[1]) else 0),
                                 -(pri_a if x[1] == "a" else pri_b),
                                 -x[0], self.rnd.random()))

        for idx, (_spe, who, atk, dfn, mv) in enumerate(acts):
            if not atk.alive or not dfn.alive:
                continue
            d = calc_damage(atk, dfn, mv, first=(idx == 0))
            dfn.hp = max(0, dfn.hp - d)
            events.append("%s %s → %s -%d（剩 %d/%d）"
                          % ("我方" if who == "a" else "对方",
                             mv["name"], dfn.name, d, dfn.hp, dfn.max_hp))
            # 印记反馈：龙噬（释放 3 能耗技能后双攻 +30%）
            gain = battle_fx.mark_on_skill(
                self.marks.get(who) or {}, self.eff_cost(who, mv))
            if gain > 0 and atk.alive:
                add_buff(atk, gain, gain)
                events.append("%s 龙噬印记生效（双攻 +%d%%）"
                              % ("我方" if who == "a" else "对方",
                                 round(100 * gain)))
            # 特性 ON_KILL（恶魔的晚餐：击杀后双攻 +50%）+
            # ON_FAINT（诈死等，被击杀方的入场时机）
            if not dfn.alive:
                other = me if who == "a" else foe
                if not trait_lib.supported_rules(
                        getattr(atk, "trait", None)):
                    for e in nrc_fx.trait_fire(atk, other, "ON_KILL"):
                        events.append(e)
            # 印记反馈：龙噬（释放 3 能耗技能后双攻 +30%）
            gain = battle_fx.mark_on_skill(
                self.marks.get(who) or {}, self.eff_cost(who, mv))
            if gain > 0 and atk.alive:
                add_buff(atk, gain, gain)
                events.append("%s 龙噬印记生效（双攻 +%d%%）"
                              % ("我方" if who == "a" else "对方",
                                 round(100 * gain)))
            # 光度换算：使用火系技能后失去 15% 生命（原文），打完再扣自己
            import trait_lib as _tl
            self_dmg = 0.0
            mods_now = _tl.attack_mods(
                atk, mv, first=(idx == 0),
                foe_total_cost=sum(int(m.get("cost") or 0)
                                   for m in dfn.moves))
            self_dmg = float(mods_now.get("self_damage_pct") or 0)
            if self_dmg > 0 and atk.alive:
                sd = max(1, int(atk.max_hp * self_dmg))
                atk.hp = max(0, atk.hp - sd)
                events.append("%s %s 的代价：失去 %d 生命（剩 %d/%d）"
                              % ("我方" if who == "a" else "对方",
                                 mv["name"], sd, atk.hp, atk.max_hp))
                if not atk.alive:
                    events.append("%s 倒下" % atk.name)
            # 吸血：先按特性规则（渴求/先机虹吸），再按**技能描述里的吸血**。
            # 原文：「造成伤害时，根据吸血比例和造成伤害值，回复生命」。
            ls = trait_lib.attack_mods(
                atk, mv, first=(idx == 0),
                foe_total_cost=sum(int(m.get("cost") or 0)
                                   for m in dfn.moves))["lifesteal"]
            sk_ls = battle_fx.parse_lifesteal(
                (mv.get("desc") or "") or _skill_desc(mv.get("id")))
            # 描述抽取表（与 parse_lifesteal 同源，但以 JSON 为准）
            dv_ls = (nrc_fx.desc_effects(mv.get("name") or {})
                     .get("lifesteal") or 0)
            if dv_ls:
                ls += dv_ls
            if ls > 0 and d > 0 and atk.alive:
                before = atk.hp
                atk.hp = min(atk.max_hp, atk.hp + max(1, int(d * ls)))
                gained = atk.hp - before
                if gained > 0:
                    events.append("%s 吸血 +%d（剩 %d/%d）"
                                  % ("我方" if who == "a" else "对方",
                                     gained, atk.hp, atk.max_hp))
            if not dfn.alive:
                events.append("%s 倒下" % dfn.name)

        # 首回合临时增益只在那一回合有效，回合结束就清掉；
        # 全神贯注的 -20%/行动 在行动结算后单独衰减（不是清零）
        for p in list(self.a) + list(self.b):
            if (p.trait or "") == "全神贯注":
                p._focus_layers = max(0, getattr(p, "_focus_layers", 5) - 1)
                p.trait_tmp_atk = 0.2 * p._focus_layers
            else:
                p.trait_tmp_atk = 0.0
            p.trait_tmp_def = 0.0

        # 回合末：异常状态（buff，含衰减）+ 印记（毒伤 3%/层，不衰减）
        # + 特性 ON_TURN_END（回血/回能）。
        # ★ 中毒 buff 与中毒印记**数值相同但生命周期不同**：buff 换人清空、
        #   层数减半由 status_tick 处理；印记换人保留、由 mark_tick 结算。
        #   两者叠加时总毒伤 = 3%×(buff层数 + 印记层数)。
        for p, other, side in ((me, foe, "a"), (foe, me, "b")):
            if p is None:
                continue
            # buff 部分（status_tick 内部做衰减）
            for e in nrc_fx.status_tick(p, other):
                events.append(e)
            # 印记部分
            tick = battle_fx.mark_tick(self.marks.get(side) or {})
            if tick["energy"] and p.alive:
                before = p.energy
                p.energy = min(ENERGY_CAP, p.energy + tick["energy"])
                if p.energy > before:
                    events.append("%s 光合印记回能 +%d（%d）"
                                  % ("我方" if side == "a" else "对方",
                                     p.energy - before, p.energy))
            if tick["dot"] and p.alive:
                d2 = max(1, int(p.max_hp * tick["dot"]))
                p.hp = max(0, p.hp - d2)
                events.append("%s 中毒印记 -%d（剩 %d/%d）"
                              % ("我方" if side == "a" else "对方",
                                 d2, p.hp, p.max_hp))
            # S6.16：traits_text_derived 的规则在 trait_lib 层消费
            # （turn_end/heal_pct 等新 kind 不在 nrc HANDLERS 里）
            for r in trait_lib.supported_rules(getattr(p, "trait", None)):
                if r.get("kind") == "heal_pct" and p.alive:
                    _hp = max(1, int(p.max_hp * float(r.get("value") or 0)))
                    _b = p.hp
                    p.hp = min(p.max_hp, p.hp + _hp)
                    if p.hp > _b:
                        events.append("%s %s（剩 %d/%d）"
                                      % ("我方" if side == "a" else "对方",
                                         r.get("kind", ""), p.hp, p.max_hp))
            if not trait_lib.supported_rules(getattr(p, "trait", None)):
                for e in nrc_fx.trait_fire(p, other, "ON_TURN_END"):
                    events.append(e)

        if not any(p.alive for p in self.a):
            self.magic["a"] = max(0, self.magic.get("a", _bf.MAGIC_LIVES) - 1)
            events.append("魔力 -1（我方剩 %d 格）" % self.magic["a"])
            self._finish("b", self.turn, 0, sum(p.hp for p in self.b))
        elif not any(p.alive for p in self.b):
            self.magic["b"] = max(0, self.magic.get("b", _bf.MAGIC_LIVES) - 1)
            events.append("魔力 -1（对方剩 %d 格）" % self.magic["b"])
            self._finish("a", self.turn, sum(p.hp for p in self.a), 0)
        # 魔力归 0 判负（原文：魔力降至零即战败）
        if self.magic["a"] <= 0:
            self._finish("b", self.turn, 0, sum(p.hp for p in self.b))
        elif self.magic["b"] <= 0:
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
              cfg_a=None, cfg_b=None, weather=None):
    """跑一局。返回 (winner, turns, hp_a, hp_b)。

    cfg_a/cfg_b（2026-11-22，UI 模拟器）：
        [{"name":..., "iv":60, "nature":"固执", "invest":["hp","atk","spe"],
          "moves":[技能 id 或名字...], "max_hp":417(可选覆盖)}, ...]
        与 team_a/team_b 按位置对应；None 的字段用默认值。

    实现：两边都交给策略，循环推进 `Battle`。手操窗口走同一个 Battle。
    """
    bt = Battle(dex, team_a, team_b, cfg_a=cfg_a, cfg_b=cfg_b, seed=seed)
    if weather:
        bt.set_weather(weather)
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
