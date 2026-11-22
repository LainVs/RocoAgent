# -*- coding: utf-8 -*-
"""nrc_fx.py —— nrc-ai 手写效果表（`data/nrc_effects.json`）的运行时。

覆盖范围（2026-11-22）
---------------------
nrc-ai 有 139 个技能 / 169 个特性的**手写语义**，共用到 175 种效果原语。
本模块只实现能映射到现有状态的那一批（状态 = 血量/能量/攻防增益/减伤/
异常状态），其余原语一律**不应用并计数**（`coverage()` 可查），宁可少算。

为什么不和 roco-cal 的特性规则叠加
----------------------------------
`专注力` 两个源里都有（roco-cal 的 `first_turn + stat_buff atk 1.0`，
nrc-ai 的 `ON_BATTLE_START + SELF_BUFF atk 1.0`）。两边都应用就会变成
**+200%**。所以规则是：

    roco-cal 的特性规则能应用 -> 只用 roco-cal 的（它带条件判定）
    否则 -> 用 nrc-ai 的

技能同理：属性增益优先用官方数值（`simulator.skill_buff`，来自
`skill_buff_calc.json`），nrc-ai 只补它没有的部分（回血/上状态/上印记）。
"""
from __future__ import annotations

import io
import json
import os
import re
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "nrc_effects.json")

# 异常状态（2026-11-22 按游戏内原文重写）
#
#   rocokingdomtools.dev / jinidex 引的是**游戏内原文**：
#       中毒  回合结束时造成 3% 生命的毒系伤害（毒系精灵免疫）
#       灼烧  回合结束时造成 2% 生命的火系伤害，并衰减一半层数（火系免疫）
#       冻结  冻结 5% 生命，若当前生命低于冻结比例则力竭（冰系免疫）
#       寄生  回合结束时，从寄生来源吸收 2% 生命（草系免疫）
#
#   BWIKI 的实测补了两条灼烧细节：
#       · 每层灼烧最多 20 点（= 按 1000 血封顶）
#       · 层数回合末衰减一半（向上取整）
#
#   洛克PVP助手里那几个 id 只是"伤害来源"标记，没有百分比公式
#   （真值由服务端下发），所以百分比以上面原文为准。
STATUS_SOURCES = {
    20050010: "寄生",
    20070010: "中毒",
    20070011: "中毒印记",
    20070020: "灼烧",
}

# key -> (每层百分比, 免疫属性, 是否层数减半, 是否把伤害转给对手)
STATUS_RULES = {
    "poison": {"pct": 0.03, "immune": "毒", "halve": False, "leech": False},
    "burn": {"pct": 0.02, "immune": "火", "halve": True, "leech": False,
             "per_stack_cap": 20},
    "freeze": {"pct": 0.05, "immune": "冰", "halve": False, "leech": False,
               "lethal": True},
    "parasite": {"pct": 0.02, "immune": "草", "halve": False, "leech": True},
    # 萌化（第 5 状态，游戏内原文）：退化到上一阶，种族资质相应降低
    # （可退化的精灵才可以获得萌化，下场不会消失）
    # 引擎行为：层数记录；每层 -10% 全属性近似（资质降低的公开量化缺，
    # 但「化茧」的免疫致命、「自由自在」连击+2 可精确判定）
    "moe": {"pct": 0.0, "immune": None, "halve": False, "leech": False,
            "note": "层数作为标记；每层-10%全属性为近似（见 test 断言）"},
    # 引电（authority 权威）：累计2层后，受25%最大生命值的电系伤害
    "yindian": {"pct": 0.25, "immune": "电", "halve": False,
               "leech": False, "at_layers": 2},
    # 禁足（authority 权威）：精灵无法离场，且无法获得新的禁足状态
    "jinzhu": {"pct": 0.0, "immune": None, "halve": False, "leech": False},
}

# 状态中文名（统一播报）
STATUS_CN = {"poison": "中毒", "burn": "灼烧", "freeze": "冻结",
             "parasite": "寄生", "moe": "萌化", "moisture": "湿润",
             "yindian": "引电", "jinzhu": "禁足"}

# 兼容旧名
STATUS_DOT = {k: v["pct"] for k, v in STATUS_RULES.items()}

_CACHE = {"data": None}
_DESC_FX = None
_AUTH_FX = None


def _authority_fx():
    """authority_effects 消费表（data/authority_consumable.json），懒加载。

    180 技的 buff_atk/buff_def/heal_pct/status/mark 等效果，来自
    authority buff_desc 正则抽取。与 desc_fx 互补。
    """
    global _AUTH_FX
    if _AUTH_FX is None:
        try:
            p = os.path.join(ROOT, "data", "authority_consumable.json")
            with io.open(p, encoding="utf-8") as fh:
                _AUTH_FX = json.load(fh).get("skills") or {}
        except Exception:
            _AUTH_FX = {}
    return _AUTH_FX


def _desc_fx():
    """技能描述抽取表（data/skill_effects_desc.json），懒加载。

    202 个技能的 priority/lifesteal/power_bonus/status/mark 直接来自
    游戏内描述原文（scripts/build_desc_effects.py 正则抽取）。
    nrc 效果表只覆盖 139 个热门技能，这张表补长尾。
    """
    global _DESC_FX
    if _DESC_FX is None:
        try:
            p = os.path.join(ROOT, "data", "skill_effects_desc.json")
            with io.open(p, encoding="utf-8") as fh:
                _DESC_FX = json.load(fh).get("skills") or {}
        except Exception:
            _DESC_FX = {}
    return _DESC_FX
_SKIP = Counter()
_APPLIED = Counter()


def _load():
    if _CACHE["data"] is not None:
        return _CACHE["data"]
    try:
        with io.open(PATH, encoding="utf-8") as fh:
            _CACHE["data"] = json.load(fh)
    except Exception:
        _CACHE["data"] = {"traits": {}, "skills": {}}
    return _CACHE["data"]


def trait_rows(name):
    """特性名 -> nrc 效果组；**名字对不上时试形态归一化**。

    库内名和 nrc 名偶有出入（库里「恶魔的晚餐」vs nrc「恶魔的晚宴」），
    直接查不到时用编辑距离 ≈1 的候选兜底（只在 nrc 键总数 169 里找，
    成本可忽略）。
    """
    table = _load().get("traits") or {}
    if name in table:
        return table[name] or []
    if not name:
        return []
    target = re.sub(r"[（(].*?[)）]", "", name).strip()
    for k in table:
        if not isinstance(k, str):
            continue
        k2 = re.sub(r"[（(].*?[)）]", "", k).strip()
        if k2 == target and abs(len(k2) - len(target)) <= 1:
            return table[k] or []
    # 「晚餐/晚宴」「型/行」这类一字之差
    if len(target) >= 3:
        for k in table:
            if isinstance(k, str) and _near(k, target):
                return table[k] or []
    return []


def _near(a, b):
    """一字之差（替换或增删一处）且前两字相同 —— 只在 169 个键里线性扫。"""
    if abs(len(a) - len(b)) > 1:
        return False
    if a[:2] != b[:2]:
        return False
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    ra, rb = a[i:], b[i:]
    if len(ra) == len(rb) == 1:
        return True                      # 替换
    if len(ra) == 0 and len(rb) == 1:
        return True                      # 多一字
    if len(rb) == 0 and len(ra) == 1:
        return True                      # 少一字
    return False


def skill_rows(name):
    return (_load().get("skills") or {}).get(name) or []


def desc_effects(name):
    """技能效果合并查询：desc 抽取表（176）+ authority 消费表（180）。

    返回合并后的效果 dict（authority 优先，desc 补充）。
    两表加起来覆盖约 340 技的效果。
    """
    desc = _desc_fx().get(name) or {}
    auth = _authority_fx().get(name) or {}
    if not auth:
        return desc
    if not desc:
        return auth
    out = dict(desc)
    for k, v in auth.items():
        if k == "source":
            continue
        if k not in out:
            out[k] = v
    return out


def has_trait(name):
    return bool(trait_rows(name))


def coverage():
    d = _load()
    return {"traits": len(d.get("traits") or {}),
            "skills": len(d.get("skills") or {}),
            "applied": dict(_APPLIED), "skipped": dict(_SKIP)}


# ---------------------------------------------------------------- 基础操作
def _norm_stat(params):
    """SELF_BUFF 的键 -> (atk_buff 增量, def_buff 增量)。

    nrc-ai 用 atk/spatk/def/spdef/speed，我们的模型只有攻/防两槽
    （双攻合并、双防合并），速度增益暂时不进减伤/出手判定以外的地方。
    """
    atk = float(params.get("atk") or 0) + float(params.get("spatk") or 0)
    dfn = float(params.get("def") or 0) + float(params.get("spdef") or 0)
    return atk, dfn


def _bump_status(pet, key, stacks):
    if stacks <= 0:
        return None
    st = getattr(pet, "status", None)
    if st is None:
        st = pet.status = {}
    st[key] = st.get(key, 0) + int(stacks)
    status_cn = {"poison": "中毒", "burn": "灼烧", "freeze": "冻结",
                 "parasite": "寄生", "moe": "萌化", "moisture": "湿润"}
    return "%s 获得%s %d 层" % (pet.name, status_cn.get(key, key), stacks)


# ---------------------------------------------------------------- 效果处理
def _h_self_buff(pet, foe, params, ctx):
    atk, dfn = _norm_stat(params)
    if atk > 0 or dfn > 0:
        # 走统一入口：**线性叠加 + 封顶 +100%**（力量增效 10 层 = 翻倍）
        import simulator as _S
        _S.add_buff(pet, atk, dfn)
    bits = []
    if atk:
        bits.append("物攻 +%d%%" % round(100 * atk))
    if dfn:
        bits.append("双防 +%d%%" % round(100 * dfn))
    return "%s 属性提升（%s）" % (pet.name, "、".join(bits)) if bits else None


def _h_enemy_debuff(pet, foe, params, ctx):
    atk, dfn = _norm_stat(params)
    bits = []
    if atk:
        foe.atk_buff = max(0.0, foe.atk_buff + atk)   # atk 负值 = 降攻
        bits.append("对方物攻 %d%%" % round(100 * atk))
    if dfn:
        foe.def_buff = max(0.0, foe.def_buff + dfn)
        bits.append("对方双防 %d%%" % round(100 * dfn))
    return "%s 削弱对手（%s）" % (pet.name, "、".join(bits)) if bits else None


def _h_damage_reduction(pet, foe, params, ctx):
    pct = float(params.get("pct") or 0)
    if pct <= 0:
        return None
    pet.taken_mult = min(pet.taken_mult, 1.0 - pct)
    return "%s 减伤 %d%%" % (pet.name, round(100 * pct))


def _h_heal_hp(pet, foe, params, ctx):
    pct = float(params.get("pct") or 0)
    if pct <= 0:
        return None
    before = pet.hp
    pet.hp = min(pet.max_hp, pet.hp + max(1, int(pet.max_hp * pct)))
    return ("%s 回复 %d" % (pet.name, pet.hp - before)) if pet.hp > before else None


def _h_heal_energy(pet, foe, params, ctx):
    amt = int(params.get("amount") or 0)
    if amt <= 0:
        return None
    from simulator import ENERGY_CAP
    before = pet.energy
    pet.energy = min(ENERGY_CAP, pet.energy + amt)
    return ("%s 回能 +%d" % (pet.name, pet.energy - before)) if pet.energy > before else None


def _h_status(key):
    """施加异常状态。

    ★ 默认目标是**对手**（2026-11-22 用户冒烟抓到的方向 bug：
      「灵魂灼伤：使敌方获得4层灼烧」此前上到了自己身上）。
      nrc 数据里 target="self" 才给自身；描述抽取的 status 一律给对手。
    """
    def fn(pet, foe, params, ctx):
        from_desc = ctx.get("_from_desc", False)
        if params.get("target") == "self" and not from_desc:
            return _bump_status(pet, key, int(params.get("stacks") or 1))
        stacks = params.get("stacks")
        if stacks is None:
            stacks = 1
        return _bump_status(foe, key, int(stacks))
    return fn


def _h_power_mod(pet, foe, params, ctx):
    """给「本回合的威力」挂一个倍率，由调用方收集。

    `bonus_pct` 是加成比例（0.25 = +25%）；`value`（应对后翻倍的
    COUNTER_SUCCESS_DOUBLE_DAMAGE）默认 1.0 = 再乘 (1+1.0)。
    params 全空时也按 +100% 处理（原文：下次攻击威力翻倍）。
    """
    pct = params.get("bonus_pct")
    if pct is None:
        pct = params.get("value")
    if pct is None:
        pct = 1.0                  # COUNTER_SUCCESS_DOUBLE_DAMAGE 无参 = 翻倍
    pct = float(pct or 0)
    if pct <= 0:
        return None
    ctx["power_mult"] = ctx.get("power_mult", 1.0) * (1.0 + pct)
    return None


def _h_enemy_lose_energy(pet, foe, params, ctx):
    """ENEMY_LOSE_ENERGY：对手（新入场或当前）失去能量。

    S12 飓风的 ON_BE_KILLED 行带 target=self_mp → 扣**自己**魔力
    （4 格生命），委托 _h_enemy_lose_energy_self_mp。
    """
    if params.get("target") == "self_mp":
        return _h_enemy_lose_energy_self_mp(pet, foe, params, ctx)
    amt = int(params.get("amount") or 0)
    if amt <= 0 or not foe.alive:
        return None
    before = foe.energy
    foe.energy = max(0, foe.energy - amt)
    return ("%s 被吸取能量 -%d（剩 %d）" % (foe.name, before - foe.energy,
                                          foe.energy)) if foe.energy < before else None


def _h_fainted_allies_buff(pet, foe, params, ctx):
    """FAINTED_ALLIES_BUFF（悲悯/悼亡）：按己方倒下数获得增益。

    params 形如 {"buff_per": {"atk": 0.3, "spatk": 0.3}, "scope": "allies"}
    scope: allies = 只数己方倒下的；all = 双方都数（悼亡原文如此）。

    ⚠ 只在**入场时**结算一次（nrc 的 timing=ON_ENTER）。若在每回合重复
    触发，恶魔狼（悲悯）会每回合 +30% 无上限叠加 —— 原文是
    「入场时，根据己方被击倒的宠物数量获得双攻提升」，是一次性的。
    """
    per = params.get("buff_per") or params.get("values_per_stack") or {}
    battle = getattr(pet, "_battle", None)
    side = getattr(pet, "_side", "a")
    if battle is None:
        return None
    scope = params.get("scope") or "allies"
    if scope == "all":
        dead = sum(1 for p in battle.a if not p.alive) + \
            sum(1 for p in battle.b if not p.alive)
    else:
        dead = sum(1 for p in battle.a if side == "a" and not p.alive) + \
            sum(1 for p in battle.b if side == "b" and not p.alive)
    if dead <= 0:
        return None
    atk, dfn = _norm_stat(per)
    a, d = atk * dead, dfn * dead
    if a or d:
        import simulator as _S
        _S.add_buff(pet, a, d)
    return "%s 按倒下数获得增益（%d 只，双攻 +%.0f%%）" % (
        pet.name, dead, 100 * a)


def _h_steal_all_energy(pet, foe, params, ctx):
    """STEAL_ALL_ENEMY_ENERGY：回合末偷取敌方能量。

    nrc 数据 params: {"amount": N} —— 每次偷 N 点（毒蘑菇 amount=1，
    1493 行；另一载体 amount=2，1745 行）。偷光敌队能量的语义是
    「amount = 对方能量数」的特例，这里按数据实现。
    """
    from simulator import ENERGY_CAP
    if not foe.alive:
        return None
    amount = int(params.get("amount") or 1)
    stolen = min(amount, foe.energy)
    if stolen <= 0:
        return None
    foe.energy -= stolen
    pet.energy = min(ENERGY_CAP, pet.energy + stolen)
    return "%s 偷取对方能量 %d（当前 %d，对方剩 %d）" % (
        pet.name, stolen, pet.energy, foe.energy)


def _h_entry_per_skill(pet, foe, params, ctx):
    """入场时按「携带的技能里满足条件的个数」叠加。"""
    per = params.get("per_count") or {}


# ---------------------------------------------------------------- S11 原语

def _h_perm_mod(pet, foe, params, ctx):
    """PERMANENT_MOD（向心力）：永久威力修正。

    params: {"target": "power", "delta": 30} → 挂 pet._perm_power_delta，
    calc_damage 出手时加到威力上。
    （身经百练的 per_counter 变体需要计数器系统，暂缓——见 PLAN_S11）
    """
    if params.get("target") != "power":
        return None
    delta = int(params.get("delta") or 0)
    if not delta:
        return None
    pet._perm_power_delta = delta
    return "%s 永久威力 +%d（向心力）" % (pet.name, delta)


def _h_mark_power_per_meteor(pet, foe, params, ctx):
    """MARK_POWER_PER_METEOR（坠星/观星）：每层星陨印记威力+15%。

    挂 pet._meteor_pct，calc_damage 按守方（我方放上）星陨层数乘算。
    """
    pct = float(params.get("bonus_pct_per_mark") or 0)
    if not pct:
        return None
    pet._meteor_pct = pct
    return "%s 每层星陨印记威力 +%d%%" % (pet.name, round(100 * pct))


def _h_extra_freeze(pet, foe, params, ctx):
    """EXTRA_FREEZE_ON_FREEZE（加个雪球/捉迷藏）：施加冻结时额外 +N 层。"""
    extra = int(params.get("extra") or 0)
    if not extra:
        return None
    pet._freeze_extra = extra
    return "%s 冻结施加时额外 +%d 层" % (pet.name, extra)


def _h_elem_cost_reduce(pet, foe, params, ctx):
    """ON_SKILL_ELEMENT_COST_REDUCE（浸润/浪潮）：特定系别技能能耗 -N。

    params: {"element": "水", "reduce": 1} → pet._elem_cost_reduce 映射，
    Battle.eff_cost 消费。幂等（重复触发不叠加）。
    """
    elem = params.get("element") or ""
    reduce_n = int(params.get("reduce") or 0)
    if not elem or not reduce_n:
        return None
    m = getattr(pet, "_elem_cost_reduce", None) or {}
    if m.get(elem) == reduce_n:
        return None
    m[elem] = reduce_n
    pet._elem_cost_reduce = m
    return "%s %s系技能能耗 -%d" % (pet.name, elem, reduce_n)


def _h_burn_no_decay(pet, foe, params, ctx):
    """BURN_NO_DECAY（煤渣）：自身灼烧层数不衰减。"""
    pet._burn_no_decay = True
    return "%s 灼烧层数不衰减" % pet.name


def _h_convert_poison(pet, foe, params, ctx):
    """CONVERT_POISON_TO_MARK（蚀刻）：回合末敌方 2 层中毒 buff
    转化为 1 层中毒印记（印记换人保留、不被 buff 衰减清掉）。"""
    ratio = int(params.get("ratio") or 2)
    st = getattr(foe, "status", None) or {}
    n = int(st.get("poison", 0) or 0)
    k = n // ratio
    if k <= 0:
        return None
    st["poison"] = n - k * ratio
    battle = getattr(pet, "_battle", None)
    side = getattr(foe, "_side", None)
    if battle is not None and side:
        marks = battle.marks.setdefault(side, {})
        marks["poison_mark"] = int(marks.get("poison_mark") or 0) + k
    return "%s 蚀刻：敌方 %d 层中毒转化为 %d 层中毒印记" % (
        pet.name, k * ratio, k)


def _h_enemy_lose_energy_self_mp(pet, foe, params, ctx):
    """ENEMY_LOSE_ENERGY target=self_mp（飓风被击败时机）：自己魔力 -1。"""
    battle = getattr(pet, "_battle", None)
    side = getattr(pet, "_side", None)
    amt = int(params.get("amount") or 0)
    if battle is None or not side or amt <= 0:
        return None
    battle.magic[side] = max(0, battle.magic.get(side, 0) - amt)
    return "%s 被击败：魔力 -%d（剩 %d 格）" % (
        pet.name, amt, battle.magic[side])


def _h_kill_mp_penalty(pet, foe, params, ctx):
    """KILL_MP_PENALTY（付给恶魔的赎价）：
    ON_KILL 时机（pet=击杀者，foe 已倒下）→ 敌方魔力 -1；
    ON_FAINT 时机（pet=被击败者，foe 存活）→ 自己魔力 -1。
    按存活状态区分时机（handler 不接收 timing）。"""
    battle = getattr(pet, "_battle", None)
    if battle is None:
        return None
    if not foe.alive:                      # ON_KILL：敌方 -1
        eside = getattr(foe, "_side", None)
        if not eside:
            return None
        battle.magic[eside] = max(0, battle.magic.get(eside, 0) - 1)
        return "%s 击败 %s：敌方魔力 -1（剩 %d 格）" % (
            pet.name, foe.name, battle.magic[eside])
    if not pet.alive:                      # ON_FAINT：自己 -1
        side = getattr(pet, "_side", None)
        if not side:
            return None
        battle.magic[side] = max(0, battle.magic.get(side, 0) - 1)
        return "%s 被击败：魔力 -1（剩 %d 格）" % (
            pet.name, battle.magic[side])
    return None


def _h_ability_compute(pet, foe, params, ctx):
    """ABILITY_COMPUTE（S12）：开场技能改写引擎。

    六种 action（原文已核对，见 docs/PLAN_S12_COMPUTE.md）：
      count_poison_skills      溶解扩散：毒系技能数 → 水系技中毒层数
      modify_matching_skills   不移/快锤/暴食/冻土：威力% / 迅捷谓词
      shared_wing_skills       飓风：全场翼系精灵同名技能 → 迅捷
    本 handler 只在 pet 上挂标记，消费点在 Battle（calc_damage /
    入场迅捷 / 状态施加）。标记幂等，重复触发不叠加。
    """
    action = params.get("action") or ""
    # 幂等：同一动作指纹只生效一次（begin_turn 会重触发
    # ON_BATTLE_START/ON_ENTER/PASSIVE 三个时机）
    done = getattr(pet, "_ability_done", None)
    if done is None:
        done = set()
        pet._ability_done = done
    fp = (action, json.dumps(params, ensure_ascii=False, sort_keys=True))
    if fp in done:
        return None
    done.add(fp)
    if action == "count_poison_skills":
        n = sum(1 for m in getattr(pet, "moves", [])
                if (m.get("type") or "") == "\u6bd2")
        if n:
            pet._poison_skill_count = n
            return "%s 携带 %d 个毒系技能（水系技附加中毒）" % (pet.name, n)
        return None
    if action == "modify_matching_skills":
        mods = getattr(pet, "_skill_power_mods", None) or {}
        swifts = getattr(pet, "_swift_pred_state", None) or []
        moves = getattr(pet, "moves", [])
        if params.get("count_source") == "self_element":
            # 冻土：数 count_element 技能数 → element 技能威力 +N*pct
            cnt = sum(1 for m in moves
                      if (m.get("type") or "")
                      in (params.get("count_element") or []))
            if not cnt:
                return None
            for m in moves:
                if (m.get("type") or "") in (params.get("element") or []) \
                        and (not params.get("attack_only")
                             or int(m.get("power") or 0) > 0):
                    mods[m["name"]] = mods.get(m["name"], 0) + \
                        cnt * float(params.get("power_pct") or 0)
            pet._skill_power_mods = mods
            return "%s 冰系技能 x%d → 地系技能威力 +%.0f%%" % (
                pet.name, cnt, 100 * cnt * float(params.get("power_pct") or 0))
        if params.get("grant_agility"):
            # 快锤（能耗<N）/ 暴食（系别）
            lt = params.get("energy_cost_lt")
            elems = params.get("element") or []
            for m in moves:
                if lt is not None and int(m.get("cost") or 0) < int(lt):
                    if m["name"] not in swifts:
                        swifts.append(m["name"])
                elif elems and (m.get("type") or "") in elems:
                    if m["name"] not in swifts:
                        swifts.append(m["name"])
            pet._swift_pred_state = swifts
            return "%s %d 个技能获得迅捷" % (pet.name, len(swifts))
        # 不移：pure_attack → 威力 +pct
        pct = float(params.get("power_pct") or 0)
        if pct and params.get("pure_attack"):
            n = 0
            for m in moves:
                if int(m.get("power") or 0) <= 0:
                    continue
                # 纯攻击：无任何附加效果（desc_effects 全空）
                if desc_effects(m.get("name") or ""):
                    continue
                mods[m["name"]] = mods.get(m["name"], 0) + pct
                n += 1
            pet._skill_power_mods = mods
            if n:
                return "%s %d 个纯攻击技能威力 +%.0f%%" % (
                    pet.name, n, 100 * pct)
        return None
    if action == "shared_wing_skills":
        # 飓风：全场翼系精灵携带同名技能 → 迅捷（battle 上下文）
        battle = getattr(pet, "_battle", None) or (ctx or {}).get("battle")
        if battle is None:
            return None
        wing = "\u7ffc"
        shared = set()
        for team in (battle.a, battle.b):
            for other in team:
                if other is pet or not getattr(other, "alive", True):
                    continue
                if wing not in (other.types or []):
                    continue
                for m in getattr(other, "moves", []):
                    shared.add(m.get("name") or "")
        swifts = getattr(pet, "_swift_pred_state", None) or []
        n0 = len(swifts)
        for m in getattr(pet, "moves", []):
            if (m.get("name") or "") in shared and \
                    m["name"] not in swifts:
                swifts.append(m["name"])
        pet._swift_pred_state = swifts
        if len(swifts) > n0:
            return "%s %d 个共享翼系技能获得迅捷" % (
                pet.name, len(swifts) - n0)
        return None
    return None
    elem = params.get("count_key")
    n = 0
    for m in getattr(pet, "moves", []):
        if elem and (m.get("type") or "") != elem:
            continue
        n += 1
    if not n:
        return None
    bonus = int(per.get("power_bonus") or 0) * n
    if bonus:
        pet.trait_atk_buff += 0.0      # 威力加成走 ctx
        ctx["power_bonus"] = ctx.get("power_bonus", 0) + bonus
    return None


def _h_on_kill_buff(pet, foe, params, ctx):
    """ON_KILL_BUFF（恶魔的晚餐）：击杀后获得双攻增益。"""
    buff = params.get("buff") or {}
    atk, dfn = _norm_stat(buff)
    if not (atk or dfn):
        return None
    import simulator as _S
    _S.add_buff(pet, atk, dfn)
    return "%s 击杀获得双攻 +%.0f%%" % (pet.name, 100 * atk)


def _h_on_skill_element_buff(pet, foe, params, ctx):
    """ON_SKILL_ELEMENT_BUFF（助燃/爆燃）：使用特定系别技能后获得属性增益。

    params: {"element": "火", "buff": {"atk": 0.2, "spatk": 0.3}}
    ctx["mv_type"] = 当前使用的技能系别（由 Battle.step 传入）。
    """
    want = params.get("element") or ""
    mv_type = (ctx or {}).get("mv_type") or ""
    if not mv_type or mv_type != want:
        return None
    return _apply_buff_pair(
        pet, params.get("buff") or {},
        "%s " + want + "\u7cfb\u6280\u80fd\u540e\u83b7\u5f97\u53cc\u653b +%d%%")


def _h_energy_cost_condition_buff(pet, foe, params, ctx):
    """ENERGY_COST_CONDITION_BUFF（鼓气/三鼓作气）：使用能耗 N 的技能后获得增益。

    params: {"cost": 3, "buff": {"atk": 0.2, "def": 0.2}}
    三鼓作气 permanent=True → 永久（模拟器 buff 本就不衰减，无需区分）。
    ctx["mv_cost"] = 当前技能能耗（由 Battle.step 传入）。
    """
    want = int(params.get("cost") or 0)
    mv_cost = int((ctx or {}).get("mv_cost") or 0)
    if want and mv_cost != want:
        return None
    return _apply_buff_pair(
        pet, params.get("buff") or {},
        "%s \u80fd\u8017" + str(want) + "\u6280\u80fd\u540e\u83b7\u5f97\u53cc\u653b\u53cc\u9632 +%d%%")


def _apply_buff_pair(pet, buff, label):
    """把 {"atk": x, "spatk": y, "def": z} 加到 pet，返回事件文本或 None。

    ⚠ 模拟器的 atk_buff 是**单槽**，物/魔两条伤害路径共用：
    「物攻+30% 魔攻+30%」→ atk_buff=0.3（两条路径各得 30%）。
    相加成 0.6 会让物攻路径吃到 60%（S10 实测抓到，已修正）。
    """
    atk = max(float(buff.get("atk") or 0), float(buff.get("spatk") or 0))
    dfn = float(buff.get("def") or 0)
    if not (atk or dfn):
        return None
    import simulator as _S
    _S.add_buff(pet, atk, dfn)
    return label % (pet.name, round(100 * max(atk, dfn)))


HANDLERS = {
    "SELF_BUFF": _h_self_buff,
    "ENEMY_DEBUFF": _h_enemy_debuff,
    "DAMAGE_REDUCTION": _h_damage_reduction,
    "HEAL_HP": _h_heal_hp,
    "HEAL_ENERGY": _h_heal_energy,
    "POISON": _h_status("poison"),
    "BURN": _h_status("burn"),
    "MOISTURE_MARK": _h_status("moisture"),
    "FREEZE": _h_status("freeze"),
    "PARASITE": _h_status("parasite"),
    "DAMAGE_MOD_NON_LIGHT": _h_power_mod,
    "FIRST_STRIKE_POWER_BONUS": _h_power_mod,
    "ENTRY_BUFF_PER_SKILL_COUNT": _h_entry_per_skill,
    # `DAMAGE` 在原表里是「这招会打伤害」的标记（params 全空），
    # 实际伤害由技能自身的威力算，所以这里是个显式的空操作，
    # 不能算「未实现」（否则 58 条永远顶着 skipped）。
    "DAMAGE": lambda pet, foe, params, ctx: None,
    # `ON_KILL_BUFF`：击杀后获得增益（恶魔的晚餐：双攻 +50%）。
    # 由 Battle.step 在对手倒下时触发。
    "ON_KILL_BUFF": _h_on_kill_buff,
    # `ON_SKILL_ELEMENT_BUFF`（助燃/爆燃）：使用特定系别技能后获得属性增益
    # params: {"element": "火", "buff": {"atk": 0.2, "spatk": 0.2}}
    # ctx["mv_type"] 必须由 Battle.step 传入，否则不触发（避免误加 buff）
    "ON_SKILL_ELEMENT_BUFF": _h_on_skill_element_buff,
    # `ENERGY_COST_CONDITION_BUFF`（三鼓作气/鼓气）：使用能耗3的技能后加增益
    # params: {"cost": 3, "buff": {"atk": 0.2, "def": 0.2}, "permanent": true}
    # ctx["mv_cost"] 必须由 Battle.step 传入
    "ENERGY_COST_CONDITION_BUFF": _h_energy_cost_condition_buff,
    # `STEAL_ALL_ENEMY_ENERGY`（毒蘑菇/大捞一把）：偷取对方全部能量
    "STEAL_ALL_ENEMY_ENERGY": _h_steal_all_energy,
    # `ENEMY_LOSE_ENERGY`（飓风/小偷小摸/做噩梦）：对手换上时被吸能量。
    # 由 Battle 换人分支触发。
    "ENEMY_LOSE_ENERGY": _h_enemy_lose_energy,
    # `KILL_MP_PENALTY`（付给恶魔的赎价）：击败敌方 → 敌方魔力 -1；
    # 被击败 → 自己魔力 -1。ON_KILL/ON_FAINT 两时机都注册同一 handler。
    "KILL_MP_PENALTY": _h_kill_mp_penalty,
    # ---- S11 低难度原语 ----
    "PERMANENT_MOD": _h_perm_mod,
    "MARK_POWER_PER_METEOR": _h_mark_power_per_meteor,
    "EXTRA_FREEZE_ON_FREEZE": _h_extra_freeze,
    "ON_SKILL_ELEMENT_COST_REDUCE": _h_elem_cost_reduce,
    "BURN_NO_DECAY": _h_burn_no_decay,
    "CONVERT_POISON_TO_MARK": _h_convert_poison,
    "FAINTED_ALLIES_BUFF": _h_fainted_allies_buff,
    # `FREEZE` / `PARASITE`（灵魂灼伤等）：给对手上异常状态
    "FREEZE": _h_status("freeze"),
    "PARASITE": _h_status("parasite"),
    # `COUNTER_SUCCESS_DOUBLE_DAMAGE`（圣火骑士）：应对成功后下次攻击威力翻倍
    "COUNTER_SUCCESS_DOUBLE_DAMAGE": _h_power_mod,
    # `THREAT_SPEED_BUFF`（哨兵/预警）：速度 +50%（进 eff_spe 路径）
    "THREAT_SPEED_BUFF": _h_self_buff,
    # `CONDITIONAL_ENTRY_BUFF_MP`（图书守卫者）：按血脉条件入场加成 ——
    # 血脉没建模，参数 mp_value 判不了，占位并如实 skip
    "CONDITIONAL_ENTRY_BUFF_MP": lambda pet, foe, params, ctx: None,
    # `KILL_MP_PENALTY`（付给恶魔的赎价）：击败敌方 → 敌方魔力 -1；
    # 被击败 → 自己魔力 -1。ON_KILL/ON_FAINT 两时机共用。
    "KILL_MP_PENALTY": _h_kill_mp_penalty,
    # ---- S11 低难度原语 ----
    "PERMANENT_MOD": _h_perm_mod,
    "MARK_POWER_PER_METEOR": _h_mark_power_per_meteor,
    "EXTRA_FREEZE_ON_FREEZE": _h_extra_freeze,
    "ON_SKILL_ELEMENT_COST_REDUCE": _h_elem_cost_reduce,
    "BURN_NO_DECAY": _h_burn_no_decay,
    "CONVERT_POISON_TO_MARK": _h_convert_poison,
    # S12 ABILITY_COMPUTE：开场技能改写引擎（溶解扩散/不移/快锤/暴食/
    # 冻土/飓风），消费点在 Battle
    "ABILITY_COMPUTE": _h_ability_compute,
    # S12 飓风的被击败时机：ENEMY_LOSE_ENERGY target=self_mp →
    # 自己魔力 -1（与 KILL_MP_PENALTY 的 ON_FAINT 同语义）
}




# ---------------------------------------------------------------- 应对（counter）
def skill_category(move):
    """把技能归到 应对 的三个类别：attack / defense / status。

    判定依据（本作没有官方字段，只能按数据特征分）：
      威力 > 0                        -> attack
      威力 = 0 且给自身加**防御**     -> defense（防御 / 有效预防 这类）
      其余威力 = 0                    -> status（力量增幅 / 上状态 / 上印记）

    `防御` 的判定复用官方数值表（`simulator.skill_buff`），不是拍脑袋。
    """
    if not move:
        return None
    if int(move.get("power") or 0) > 0:
        return "attack"
    try:
        import simulator as _S
        v = _S.skill_buff(move.get("name") or "")
    except Exception:
        v = None
    if v and (v.get("def") or 0) > 0:
        return "defense"
    # 有些防御技能在官方数值表里没有条目（例如「有效预防」），
    # 但 nrc-ai 的效果链里有「减伤」—— 据此判成 defense。
    for r in skill_rows(move.get("name") or ""):
        for e in r.get("effects") or []:
            if e.get("type") == "DAMAGE_REDUCTION":
                return "defense"
    return "status"


def counter_fire(pet, foe, skill_name, foe_move, ctx=None):
    """我方这一招**应对**对方的 `foe_move`：命中类别就触发。

    nrc-ai 的 ON_COUNTER 组带 `filters={"category": ...}`，正是被应对方
    的技能类别。返回 (事件列表, 是否应对成功)。
    """
    ctx = ctx if ctx is not None else {}
    cat = skill_category(foe_move)
    if not cat:
        return [], False
    hit = False
    out = []
    rows = skill_rows(skill_name)
    # desc 回退（S6.11）：骑士敕令/瞬间零度等能耗操纵技不在 nrc 表，
    # 应对类别和「能耗+N」全在 desc 里 —— 从 desc 解析。
    if not rows:
        import simulator as _S
        desc = _S._skill_desc_by_name(skill_name) or ""
        want = None
        for kw, cc in (("应对攻击", "attack"), ("应对防御", "defense"),
                       ("应对状态", "status")):
            if kw in desc:
                want = cc
                break
        if want and want == cat:
            hit = True
            out.insert(0, "%s 应对成功（对方是%s技能）"
                       % (pet.name, {"attack": "攻击", "defense": "防御",
                                     "status": "状态"}[cat]))
            import re as _re
            m = _re.search(r"能耗\s*\+\s*(\d+)", desc)
            if m:
                ctx["enemy_cost_up"] = int(m.group(1))
        return out, hit
    for r in rows:
        if r.get("timing") != "ON_COUNTER":
            continue
        want = (r.get("filters") or {}).get("category")
        if want and want != cat:
            continue
        hit = True
        for e in r.get("effects") or []:
            fn = HANDLERS.get(e["type"])
            if fn is None:
                _SKIP[e["type"]] += 1
                continue
            _APPLIED[e["type"]] += 1
            msg = fn(pet, foe, e.get("params") or {}, ctx)
            if msg:
                out.append(msg)
    if hit:
        out.insert(0, "%s 应对成功（对方是%s技能）"
                   % (pet.name, {"attack": "攻击", "defense": "防御",
                                 "status": "状态"}[cat]))
    return out, hit

# 允许的技能/特性时机
TRAIT_TIMINGS = {"ON_ENTER", "ON_BATTLE_START", "ON_TURN_START",
                 "ON_USE_SKILL", "ON_TURN_END", "PASSIVE", "ON_TAKE_HIT",
                 "ON_COUNTER_SUCCESS", "ON_KILL", "ON_FAINT", "ON_BE_KILLED",
                 "ON_ENEMY_SWITCH"}
SKILL_TIMINGS = {"ON_USE", "PRE_USE", "POST_USE"}


def _run(pet, foe, rows, timings, ctx):
    out = []
    for r in rows:
        if r.get("timing") not in timings:
            # 时机没建模：把这个组里的原语记成 skipped，
            # 否则「没应用」会被误读成「没有效果」
            for e in r.get("effects") or []:
                _SKIP["timing:" + str(r.get("timing"))] += 1
            continue
        for e in r.get("effects") or []:
            fn = HANDLERS.get(e["type"])
            if fn is None:
                _SKIP[e["type"]] += 1
                continue
            _APPLIED[e["type"]] += 1
            msg = fn(pet, foe, e.get("params") or {}, ctx)
            if msg:
                out.append(msg)
    return out


def trait_fire(pet, foe, timing, ctx=None):
    """触发特性的某个时机，返回事件文本列表。"""
    ctx = ctx if ctx is not None else {}
    return _run(pet, foe, trait_rows(getattr(pet, "trait", None)),
                {timing}, ctx)


def skill_fire(pet, foe, skill_name, timing, ctx=None):
    ctx = ctx if ctx is not None else {}
    return _run(pet, foe, skill_rows(skill_name), {timing}, ctx)


def status_tick(pet, foe=None):
    """回合末结算异常状态，返回事件文本。

    规则见 STATUS_RULES（游戏内原文）。要点：
      · 中毒 3% / 灼烧 2%（层数减半，每层最多 20 点）/ 冻结 5%（血量不足则力竭）
        / 寄生 2% 且**把伤害回给对手**
      · 免疫：毒系免中毒、火系免灼烧、冰系免冻结、草系免寄生
    """
    st = getattr(pet, "status", None)
    if not st:
        return []
    out = []
    types = set(pet.types or [])
    for key, rule in STATUS_RULES.items():
        n = int(st.get(key, 0) or 0)
        if n <= 0 or not pet.alive:
            continue
        if rule["immune"] in types:
            out.append("%s 是%s系，免疫%s" % (pet.name, rule["immune"], key))
            st[key] = 0
            continue
        pct = rule["pct"] * min(n, 5)
        dmg = max(1, int(pet.max_hp * pct))
        cap = rule.get("per_stack_cap")
        if cap:
            dmg = min(dmg, cap * min(n, 5))
        pet.hp = max(0, pet.hp - dmg)
        status_cn = {"poison": "中毒", "burn": "灼烧", "freeze": "冻结",
                     "parasite": "寄生", "moe": "萌化"}
        out.append("%s 受到%s伤害 -%d（剩 %d/%d）"
                   % (pet.name, status_cn.get(key, key), dmg,
                      pet.hp, pet.max_hp))
        if rule["leech"] and foe is not None and foe.alive:
            before = foe.hp
            foe.hp = min(foe.max_hp, foe.hp + dmg)
            if foe.hp > before:
                out.append("%s 从寄生吸取 +%d（剩 %d/%d）"
                           % (foe.name, foe.hp - before, foe.hp, foe.max_hp))
        if rule.get("halve") and n > 1:
            # S11 煤渣（BURN_NO_DECAY）：灼烧层数不衰减
            if key == "burn" and getattr(pet, "_burn_no_decay", False):
                st[key] = n
            else:
                st[key] = (n + 1) // 2          # 向上取整
        if not pet.alive:
            out.append("%s 倒下" % pet.name)
            break
    return out


def clear_buffs(pet):
    """换人时清掉增益/减益（印记不清）。

    游戏内原文：「增益和减益状态在更换精灵之后会被清除」「精灵下场后
    印记不会消失」。异常状态按同一份原文属于「减益」，一并清掉。

    ★ 同时清掉入场标记（2026-11-22 用户实测纠正）：专注力这类
      「入场首回合」特性的「入场」是**可轮转的** —— 换下再换上，
      第一回合攻击照样翻倍。所以换下时重置 _trait_first_done，
      换上时重新触发。
    """
    pet.atk_buff = 0.0
    pet.def_buff = 0.0
    pet.trait_tmp_atk = 0.0
    pet.trait_tmp_def = 0.0
    pet.taken_mult = 1.0
    pet.status = {}
    pet.buffed = False
    pet._trait_first_done = False       # 入场标记重置（可轮转）
