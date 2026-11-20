# -*- coding: utf-8 -*-
"""test_skill_kind.py —— 验证技能分类与 policy_v2 的 buff 判定。

覆盖的回归点（2026-11-21 实测 bug）：
    实盘连续 7 局在 T1/T2 打 slot 4，而真实队伍所有 slot 4 都是
    「借用」/「取念」（随机复制技能）。旧代码把 power=0 一律当 buff，
    导致每局开局白亏一回合。

跑法：
    python src/test_skill_kind.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import skill_kind as SK
import policy_v2 as P

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


# ---------------------------------------------------------------- 分类

def test_classify():
    print("=== 技能分类 ===")
    cases = [
        # 复制技能：绝不能当 buff
        ("借用", SK.KIND_COPY),
        ("取念", SK.KIND_COPY),
        ("复写", SK.KIND_COPY),
        # 自身增益
        ("钧势", SK.KIND_SELF_BUFF),
        ("分光", SK.KIND_SELF_BUFF),
        ("不动如山", SK.KIND_SELF_BUFF),
        # 给敌减益
        ("霜冻", SK.KIND_ENEMY_DEBUFF),
        ("雪球", SK.KIND_ENEMY_DEBUFF),
        # 减伤
        ("冰墙", SK.KIND_REDUCTION),
        ("镜像反射", SK.KIND_REDUCTION),
        # 治疗
        ("休息回复", SK.KIND_HEAL),
        # 伤害
        ("升龙咆哮", SK.KIND_DAMAGE),
        ("坟场搏击", SK.KIND_DAMAGE),
        # 查不到
        ("这个技能不存在", SK.KIND_UNKNOWN),
        (None, SK.KIND_UNKNOWN),
        ("", SK.KIND_UNKNOWN),
    ]
    for name, want in cases:
        got = SK.classify(name)
        check(got == want, "%s -> %s (want %s)" % (name, got, want))


def test_predicates():
    print()
    print("=== 谓词 ===")
    check(SK.is_copy("借用") and SK.is_copy("取念"), "借用/取念 is_copy")
    check(not SK.is_copy("钧势"), "钧势 not is_copy")
    check(SK.is_buff("钧势") and SK.is_buff("分光"), "钧势/分光 is_buff")
    check(not SK.is_buff("借用"), "借用 not is_buff")
    check(not SK.is_buff("冰墙"), "冰墙 not is_buff")
    check(not SK.is_buff("升龙咆哮"), "升龙咆哮 not is_buff")
    check(abs(SK.damage_reduction("冰墙") - 0.8) < 1e-6, "冰墙 减伤 0.8")
    check(SK.damage_reduction("钧势") == 0.0, "钧势 减伤 0")


def test_coverage():
    print()
    print("=== 覆盖率 ===")
    st = SK.stats()
    total = sum(st.values())
    check(total > 550, "技能库覆盖 %d 个（>550）" % total)
    check(st.get(SK.KIND_DAMAGE, 0) > 300,
          "伤害技能 %d 个" % st.get(SK.KIND_DAMAGE, 0))
    check(st.get(SK.KIND_COPY, 0) == 3,
          "复制技能 %d 个（应为 3）" % st.get(SK.KIND_COPY, 0))
    check(st.get(SK.KIND_UNKNOWN, 0) == 0,
          "无 unknown（%d）" % st.get(SK.KIND_UNKNOWN, 0))


# ---------------------------------------------------------------- 策略

def _state(skills, turn=1, buffed=False):
    return {
        "me": {"name": "测试", "hp": 300, "hp_max": 300, "energy": 10,
               "skills": skills},
        "foe": {"name": "对手", "hp": 300, "hp_max": 300},
        "bench": [],
        "turn": turn,
        "buffed": buffed,
    }


def test_policy_copy_not_buff():
    """回归点：复制技能绝不能被优先选择。"""
    print()
    print("=== policy_v2：复制技能不当 buff ===")
    skills = [
        {"i": 1, "dmg": 180, "cost": 3, "kind": "num", "lit": True},
        {"i": 2, "dmg": 120, "cost": 2, "kind": "num", "lit": True},
        {"i": 3, "dmg": 100, "cost": 2, "kind": "num", "lit": True},
        {"i": 4, "dmg": None, "cost": 0, "kind": SK.KIND_COPY,
         "name": "借用", "lit": True},
    ]
    d = P.decide(_state(skills, turn=1))
    picked = d[0] if isinstance(d, tuple) else d
    # decide 返回结构可能不同，统一取第一项
    reason = d[-1] if isinstance(d, tuple) else ""
    check(not (isinstance(picked, tuple) and picked[1] == 4),
          "T1 不选 slot4（借用）：%s" % (reason,))

    # 4 张全是复制技能时才允许选它（不能卡死）
    only_copy = [
        {"i": 1, "dmg": None, "cost": 0, "kind": SK.KIND_COPY,
         "name": "借用", "lit": True},
        {"i": 2, "dmg": None, "cost": 0, "kind": SK.KIND_COPY,
         "name": "取念", "lit": True},
    ]
    d2 = P.decide(_state(only_copy, turn=1))
    check(d2 is not None, "全是复制技能时不崩：%s" % (d2,))


def test_policy_self_buff_still_priority():
    """真正的自身增益仍应在前两回合优先。"""
    print()
    print("=== policy_v2：真 buff 仍然优先 ===")
    skills = [
        {"i": 1, "dmg": 180, "cost": 3, "kind": "num", "lit": True},
        {"i": 2, "dmg": 120, "cost": 2, "kind": "num", "lit": True},
        {"i": 3, "dmg": 100, "cost": 2, "kind": "num", "lit": True},
        {"i": 4, "dmg": None, "cost": 2, "kind": SK.KIND_SELF_BUFF,
         "name": "钧势", "lit": True},
    ]
    d = P.decide(_state(skills, turn=1))
    picked = d[0] if isinstance(d, tuple) else d
    reason = d[-1] if isinstance(d, tuple) else ""
    check(isinstance(picked, tuple) and picked[1] == 4,
          "T1 选 slot4（钧势 buff）：%s" % (reason,))

    # 已经叠过就不该再叠
    d2 = P.decide(_state(skills, turn=2, buffed=True))
    picked2 = d2[0] if isinstance(d2, tuple) else d2
    check(not (isinstance(picked2, tuple) and picked2[1] == 4),
          "buffed=True 后不再叠")


def test_policy_kill_over_buff():
    """能一击必杀时不叠 buff。"""
    print()
    print("=== policy_v2：能杀就杀 ===")
    skills = [
        {"i": 1, "dmg": 400, "cost": 3, "kind": "num", "lit": True},
        {"i": 4, "dmg": None, "cost": 2, "kind": SK.KIND_SELF_BUFF,
         "name": "钧势", "lit": True},
    ]
    d = P.decide(_state(skills, turn=1))
    picked = d[0] if isinstance(d, tuple) else d
    check(isinstance(picked, tuple) and picked[1] == 1,
          "能杀时选攻击而非 buff")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    test_classify()
    test_predicates()
    test_coverage()
    test_policy_copy_not_buff()
    test_policy_self_buff_still_priority()
    test_policy_kill_over_buff()
    print()
    if FAILS:
        print("FAILED %d:" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS")
