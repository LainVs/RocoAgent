# -*- coding: utf-8 -*-
"""S6.5：条件型状态施加（灵魂灼伤/溶解扩散/溶解腐蚀）+ 萌化 + 蓄力。

原文驱动（游戏内原文，不是猜测）：
  灵魂灼伤：冰系技能使敌方获得4层灼烧，火系技能使敌方获得2层冻结
  溶解扩散：每携带1个毒系技能进入战斗，水系技能使敌方获得1层中毒
  溶解腐蚀：水系技能使敌方获得2层中毒
  萌化：退化到上一阶，种族资质相应降低；化茧=受致命伤+1层并免疫；
        无忧无虑=层数不限；自由自在=每层连击+2
  蓄力：先蓄力 1 回合，下回合才能释放；蓄力期间免疫所有离场效果
"""
import re
import sys

sys.path.insert(0, "src")

# ---- 1. 条件型施加表（技能名 -> [(条件系别, 状态, 层数)]）
# 从原文抽取，人工核对（见 probe_cond2 输出）
COND_STATUS = {
    "灵魂灼伤": [("冰", "burn", 4), ("火", "freeze", 2)],
    "溶解扩散": [("水", "poison", 1)],
    "溶解腐蚀": [("水", "poison", 2)],
}


def cond_status_apply(skill_name, move_element):
    """条件型施加：返回 [(state_key, stacks)]，条件不匹配返回空。

    「灵魂灼伤：冰系技能使敌方获得4层灼烧，火系技能使敌方获得2层冻结」
    指该技能自己有两种释放模式（这就是「选择」机制，21 技的一种）——
    玩家选系别释放。move_element 为 None（type_name=NULL 的特殊技能）时
    取第一个分支（冰系，灼烧数值更高），手操窗口后续加选择 UI。
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


# ---- 2. 萌化
def moe_on_lethal(pet):
    """化茧：受到致命伤害时 +1 层萌化并免疫该次伤害。

    返回 True 表示本次伤害被免疫（调用方应跳过 hp 扣减）。
    """
    if getattr(pet, "trait", "") != "化茧":
        return False
    st = getattr(pet, "status", None) or {}
    if st.get("moe", 0) >= 1 and not getattr(pet, "_moe_unlimited", False):
        pass  # 层数不设上限（无忧无虑），普通宠物也能叠
    st["moe"] = st.get("moe", 0) + 1
    pet.status = st
    return True


def moe_stat_mult(pet):
    """萌化的属性乘数：每层 -10% 全属性（近似，公开量化缺）。

    原文只说「种族资质相应降低」，没有给出精确百分比。
    这里取 -10%/层是社区通行口径，测试里锁定该假设以便将来修正。
    """
    st = getattr(pet, "status", None) or {}
    n = min(int(st.get("moe", 0) or 0), 6)     # 最多退化到 0 阶（约 6 层）
    return max(0.4, 1.0 - 0.1 * n)


def moe_combo_bonus(pet):
    """自由自在：每层萌化连击 +2。"""
    if getattr(pet, "trait", "") != "自由自在":
        return 0
    st = getattr(pet, "status", None) or {}
    return int(st.get("moe", 0)) * 2


# ---- 3. 蓄力
def is_charge_skill(desc_or_name):
    """技能是否需要蓄力（desc 含「蓄力」）。"""
    import battle_fx
    return "蓄力" in (desc_or_name or "")


CHARGE_SET = None


def charge_skills(dex):
    """蓄力技能名集合（懒加载，24 个）。"""
    global CHARGE_SET
    if CHARGE_SET is None:
        rows = dex.db.execute(
            "SELECT name FROM skills WHERE desc LIKE '%蓄力%'").fetchall()
        CHARGE_SET = {r[0] for r in rows}
    return CHARGE_SET
