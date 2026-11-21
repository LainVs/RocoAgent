"""
技能数据库 — 从 SQLite 加载技能，效果由 EffectTag 系统驱动

加载优先级（高→低）:
  1. effect_data.SKILL_EFFECTS          (手动精确配置，35 个)
  2. skill_effects_generated.SKILL_EFFECTS_GENERATED  (自动转换，387 个)
  3. 空列表 (无效果，仅数值)
"""
import os
import re
import sqlite3
from typing import Optional, List

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sim.models import Skill, Type, SkillCategory, TYPE_NAME_MAP, CATEGORY_NAME_MAP
from sim.effect_models import SkillEffect

# 魔攻属性类型
SPECIAL_TYPES = {Type.FIRE, Type.WATER, Type.GRASS, Type.ELECTRIC, Type.ICE,
                 Type.PSYCHIC, Type.DRAGON, Type.DARK, Type.FAIRY}

_conn: Optional[sqlite3.Connection] = None
_skill_db: dict = {}
# 技能数据源：NRC_AI 自带的 nrc.db（491 个技能的名字/威力/耗能/描述）。
#
# 注意（2026-11-21 移植时的判断）：
#   · 它的**精灵属性**数据是错的（4/5 与 roco-cal 不一致），所以不用它的 pokemon 表
#   · 但**技能表**是完整的（491 条，全部有效果配置），可以用于
#     "技能名 -> Skill 对象"的构造
#   · 我们自己的技能库（data/roco.sqlite3，1269 条）有脏数据
#     （106 个同名重复、80 条 cost=0 但有威力），不适合直接建 Skill
#
# 路径从 src/sim/ 往上两级 = 仓库根，再进 data/external。
_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "external", "nrc-ai", "data", "nrc.db")

# 属性中文名→Type（含短名）
_TYPE_MAP = {
    "普通": Type.NORMAL, "火": Type.FIRE, "水": Type.WATER, "草": Type.GRASS,
    "电": Type.ELECTRIC, "冰": Type.ICE, "武": Type.FIGHTING, "毒": Type.POISON,
    "地": Type.GROUND, "翼": Type.FLYING, "幻": Type.PSYCHIC, "虫": Type.BUG,
    "幽": Type.GHOST, "龙": Type.DRAGON, "恶": Type.DARK,
    "机械": Type.STEEL, "萌": Type.FAIRY, "光": Type.LIGHT,
    "未知": Type.NORMAL, "—": Type.NORMAL,
}
_TYPE_MAP.update(TYPE_NAME_MAP)

# 分类中文名→SkillCategory
_CAT_MAP = {
    "物理": SkillCategory.PHYSICAL, "魔法": SkillCategory.MAGICAL,
    "防御": SkillCategory.DEFENSE, "状态": SkillCategory.STATUS,
    "物攻": SkillCategory.PHYSICAL, "魔攻": SkillCategory.MAGICAL,
    "变化": SkillCategory.STATUS, "—": SkillCategory.STATUS,
}
_CAT_MAP.update(CATEGORY_NAME_MAP)


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        if not os.path.exists(_DB_PATH):
            raise FileNotFoundError(f"Database not found: {_DB_PATH}")
        _conn = sqlite3.connect(_DB_PATH)
        _conn.row_factory = sqlite3.Row
    return _conn


def _flatten_effects(effects):
    """把双重嵌套的 SkillEffect 摊平。

    为什么需要（2026-11-21 实测）
    ----------------------------
    `skill_effects_generated.py` 里有 8 个技能被写成了
    `SE(timing, [SE(timing, [EffectTag(...)])])` —— 内层多包了一层。

    引擎的 `battle._iter_flat_tags` 只展平一层，遇到内层 SkillEffect
    会直接 AttributeError: 'SkillEffect' object has no attribute 'type'。

    实测受影响的 8 个技能：
        富养化 / 风隐 / 雪球 / 架势 / 休息回复 / 徒长 / 根吸收 / 杠杆置换

    修在加载层而不是改生成文件，原因：
        · 生成文件是上游自动产出的，改它下次重新生成会丢
        · 摊平后所有调用方（battle / pimc / belief）都受益
        · 语义不变：内层 SkillEffect 的 timing 和外层一致

    如果内层 timing 和外层不同，保留内层的（更具体）。
    """
    out = []
    for se in (effects or []):
        if not isinstance(se, SkillEffect):
            out.append(se)
            continue
        inner = getattr(se, "effects", None) or []
        nested = [x for x in inner if isinstance(x, SkillEffect)]
        if not nested:
            out.append(se)
            continue
        # 有嵌套：把内层的 effects 提到外层
        flat = []
        for x in inner:
            if isinstance(x, SkillEffect):
                sub = _flatten_effects([x])
                for y in sub:
                    # 用内层的 timing（更具体），但保留外层的 filter
                    if getattr(y, "timing", None) != getattr(se, "timing", None):
                        y = SkillEffect(timing=y.timing, effects=y.effects,
                                        filter=(y.filter or se.filter))
                    elif not (getattr(y, "filter", None) or {}):
                        y = SkillEffect(timing=y.timing, effects=y.effects,
                                        filter=se.filter)
                    flat.append(y)
            else:
                flat.append(x)
        out.extend(flat)
    return out


def load_skills(csv_path: str = None) -> dict:
    """从 SQLite 加载技能数据库，效果由 EffectTag 系统注入。"""
    global _skill_db
    if _skill_db:
        return _skill_db

    # 加载效果配置（手动优先，生成文件兜底）
    try:
        from sim.effect_data import SKILL_EFFECTS as MANUAL
    except ImportError:
        MANUAL = {}

    try:
        from sim.skill_effects_generated import SKILL_EFFECTS_GENERATED as GENERATED
    except ImportError:
        GENERATED = {}

    # 合并：手动配置覆盖生成配置，过滤生成文件中的空列表
    all_effects = {k: v for k, v in GENERATED.items() if v}
    all_effects.update(MANUAL)

    conn = _get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM skill")

    for row in c.fetchall():
        name = row["name"]
        element = _TYPE_MAP.get(row["element"], Type.NORMAL)
        category = _CAT_MAP.get(row["category"], SkillCategory.STATUS)
        power = row["power"] or 0
        energy = row["energy_cost"] or 0

        # hit_count 字段（如果DB有）
        try:
            hit_count = int(row["hit_count"] or 1)
        except (IndexError, KeyError):
            hit_count = 1

        # agility 字段（如果DB有）
        try:
            agility = bool(row["agility"])
        except (IndexError, KeyError):
            agility = False

        description = row["description"] or ""
        priority_mod = 0
        match = re.search(r"先手([+-]\d+)", description)
        if match:
            priority_mod = int(match.group(1))

        skill = Skill(
            name=name, skill_type=element, category=category,
            power=power, energy_cost=energy,
            hit_count=hit_count, agility=agility, priority_mod=priority_mod,
        )
        skill._base_energy_cost = energy
        skill.effects = _flatten_effects(all_effects.get(name, []))

        # 迸发标记：description 中包含"迸发"的技能
        skill.burst = "迸发" in description
        # 奉献标记：description 中包含"受奉献影响"的技能
        skill.devotion_affected = "受奉献影响" in description

        _skill_db[name] = skill

    total = len(_skill_db)
    covered = sum(1 for s in _skill_db.values() if s.effects)
    print(f"[OK] Loaded {total} skills ({covered} with effects, "
          f"{len(MANUAL)} manual + {len(GENERATED)} generated)")
    return _skill_db


def get_skill(name: str) -> Skill:
    """获取技能副本"""
    load_skills()
    if name in _skill_db:
        return _skill_db[name].copy()
    return Skill(name=name, skill_type=Type.NORMAL, category=SkillCategory.PHYSICAL,
                 power=40, energy_cost=2)


def get_all_skills() -> dict:
    """获取所有技能"""
    load_skills()
    return dict(_skill_db)


def get_all_skill_names() -> list:
    """获取所有技能名列表"""
    load_skills()
    return list(_skill_db.keys())


def get_skill_learners(skill_name: str) -> List[str]:
    """获取能学习某技能的精灵列表"""
    conn = _get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT p.name FROM pokemon p
        JOIN pokemon_skill ps ON ps.pokemon_id = p.id
        JOIN skill s ON ps.skill_id = s.id
        WHERE s.name = ?
    """, (skill_name,))
    return [r[0] for r in c.fetchall()]


def load_ability_effects(ability_str: str) -> list:
    """
    根据精灵的特性字符串返回 AbilityEffect 列表。
    格式: '特性名:描述' 或 '特性名'
    """
    try:
        from sim.effect_data import ABILITY_EFFECTS
    except ImportError:
        return []

    name = ability_str.split(":")[0].split("：")[0].strip()
    effects = ABILITY_EFFECTS.get(name, [])

    # 检测空占位特性（SELF_BUFF(atk=0) 等静默无效的配置）
    if effects:
        from sim.effect_models import E
        all_noop = all(
            len(ae.effects) == 1
            and ae.effects[0].type == E.SELF_BUFF
            and ae.effects[0].params.get("atk", None) == 0
            and len(ae.effects[0].params) == 1
            for ae in effects
        )
        if all_noop:
            import sys
            print(f"[WARN] 特性 '{name}' 是空占位 (SELF_BUFF atk=0)，对战时无实际效果", file=sys.stderr)

    return effects
