# -*- coding: utf-8 -*-
"""cond_fx —— 条件型状态施加 + 萌化 + 蓄力/迸发/打断。

数据/语义全部来自游戏内原文（rocokingdomtools/jinidex 术语表 + 技能描述）。

【条件型状态施加】原文例：
    灵魂灼伤（尖嘴狐仙特性）：冰系技能使敌方获得4层灼烧，
                            火系技能使敌方获得2层冻结
    溶解扩散：每携带1个毒系技能进入战斗，水系技能使敌方获得1层中毒
    溶解腐蚀：水系技能使敌方获得2层中毒

【萌化】第 5 状态：退化到上一阶，种族资质相应降低（可退化的精灵才可以
  获得萌化，下场不会消失）。化茧=受致命伤+1层并免疫；无忧无虑=层数不限；
  自由自在=每层连击+2。每层 -10% 全属性为社区口径近似（官方未量化）。

【蓄力】先蓄力 1 回合，下回合才能释放；蓄力期间免疫所有离场效果。
【迸发】入场后的首次行动，会获得额外效果（威力+30 / 变为1.5倍 / 能耗-1）。
【打断】使敌方本次无法释放对应技能。
"""
import re
import sys

sys.path.insert(0, "src")
import battle_fx  # noqa: E402

# ------------------------------------------------- 条件型状态施加
# 从技能描述原文人工核对（probe_cond2 扫描 4 技）
COND_STATUS = {
    "灵魂灼伤": [("冰", "burn", 4), ("火", "freeze", 2)],
    "溶解扩散": [("水", "poison", 1)],
    "溶解腐蚀": [("水", "poison", 2)],
}


def cond_status_apply(skill_name, move_element):
    """条件型施加：返回 [(state_key, stacks)]，条件不匹配返回空。

    move_element=None（type_name 为 NULL 的特殊技能如灵魂灼伤）时
    默认取第一个分支（冰系）；手操窗口后续加「选择系别」UI。
    """
    branches = COND_STATUS.get(skill_name, [])
    if not branches:
        return []
    if not move_element:
        return [(k, n) for _e, k, n in branches[:1]]
    for elem, key, n in branches:
        if elem == move_element:
            return [(key, n)]
    return []


# ------------------------------------------------- 萌化（第 5 状态）
def moe_on_lethal(pet):
    """化茧：受到致命伤害时 +1 层萌化并免疫该次伤害。"""
    if getattr(pet, "trait", "") != "化茧":
        return False
    st = getattr(pet, "status", None) or {}
    st["moe"] = st.get("moe", 0) + 1
    pet.status = st
    return True


def moe_stat_mult(pet):
    """萌化属性乘数：每层 -10% 全属性（社区口径近似，测试锁定该假设）。"""
    st = getattr(pet, "status", None) or {}
    n = min(int(st.get("moe", 0) or 0), 6)
    return max(0.4, 1.0 - 0.1 * n)


def moe_combo_bonus(pet):
    """自由自在：每层萌化连击 +2。"""
    if getattr(pet, "trait", "") != "自由自在":
        return 0
    st = getattr(pet, "status", None) or {}
    return int(st.get("moe", 0)) * 2


# ------------------------------------------------- 蓄力 / 迸发 / 打断
def is_charge_skill(desc_or_name):
    return "蓄力" in (desc_or_name or "")


_CHARGE_SET = None


def charge_skills(dex):
    """蓄力技能名集合（desc 含「蓄力」，懒加载；24 个）。"""
    global _CHARGE_SET
    if _CHARGE_SET is None:
        rows = dex.db.execute(
            "SELECT name FROM skills WHERE desc LIKE '%蓄力%'").fetchall()
        _CHARGE_SET = {r[0] for r in rows}
    return _CHARGE_SET


_INTERRUPT_SET = None


def interrupt_skills(dex):
    """打断技能名集合（desc 含「打断」，15 个）。"""
    global _INTERRUPT_SET
    if _INTERRUPT_SET is None:
        rows = dex.db.execute(
            "SELECT name FROM skills WHERE desc LIKE '%打断%'").fetchall()
        _INTERRUPT_SET = {r[0] for r in rows}
    return _INTERRUPT_SET


def burst_bonus(desc):
    """从描述解析迸发加成：「威力+30」「威力变为1.5倍」等。"""
    out = {}
    m = re.search(r"威力\+?(\d+)", desc or "")
    if m:
        out["power_bonus"] = int(m.group(1))
    m2 = re.search(r"威力变为\s*([\d.]+)\s*倍", desc or "")
    if m2:
        out["power_mult"] = float(m2.group(1))
    return out
