# -*- coding: utf-8 -*-
"""test_policy_cost.py —— 验证策略真的知道技能耗能。

回归的 bug（2026-11-21 09:00）
------------------------------
用户报"有 1 费 360 伤害的技能却打了 2 费伤害更低的"。

查下来 `robot._policy_state` 里：

    cost = 0
    meta = E.D.skill(s.get("name") or "")   # name 恒为 None
    if meta:
        cost = int(meta.get("energy_consumption") or 0)

机器人**不读技能名**（turns.jsonl 的 skills 只有 c/i/lit/plate/t/v），
所以 `s.get("name")` 恒为 None，`E.D.skill(None)` 返回 None，**cost 恒为 0**。

后果：policy_v2 的 energy_after 恒等于 energy，w_energy 项对所有技能相同，
等于"不知道任何技能耗能"。初版 policy_legacy 用 card_costs（查表），一直是对的。

跑法：
    python src\\test_policy_cost.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import policy_v2 as P

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


def state(skills, energy=10, my_hp=400, foe_hp=400, turn=3):
    return {
        "me": {"name": "测试", "hp": my_hp, "hp_max": my_hp,
               "energy": energy, "skills": skills},
        "foe": {"name": "对手", "hp": foe_hp, "hp_max": foe_hp},
        "bench": [], "turn": turn, "buffed": False,
    }


def card(i, dmg, cost, lit=True, kind="num"):
    return {"i": i, "dmg": dmg, "cost": cost, "kind": kind, "lit": lit}


def score_of(detail, slot):
    """从 decide 的 detail 里取某槽位的得分。"""
    for sc, act in (detail.get("all") or []):
        if isinstance(act, tuple) and act == ("skill", slot):
            return sc
    return None


def test_cost_affects_score():
    """相同伤害、不同耗能 -> 低耗技能得分应更高。"""
    print("=== 耗能影响得分 ===")
    # energy 用 6（有 cost=6 的卡亮着时的下界）
    skills = [card(1, 200, 6), card(2, 200, 1)]
    act, why, detail = P.decide(state(skills, energy=6))
    s1 = score_of(detail, 1)
    s2 = score_of(detail, 2)
    check(s1 is not None and s2 is not None, "两个槽位都有得分")
    if s1 is not None and s2 is not None:
        check(s2 > s1,
              "低耗技能(cost1)得分 %.4f > 高耗(cost6)得分 %.4f" % (s2, s1))
        check(act == ("skill", 2), "实际选了低耗技能（%s）" % (act,))


def test_cost_zero_is_flat():
    """cost 参与计算：相同 cost 得分相同，不同 cost 得分不同。"""
    print()
    print("=== cost 参与计算 ===")
    # 相同伤害、相同 cost -> 得分应完全相同
    same = [card(1, 200, 2), card(2, 200, 2)]
    _, _, d_same = P.decide(state(same, energy=6))
    a = score_of(d_same, 1)
    b = score_of(d_same, 2)
    check(a is not None and b is not None and abs(a - b) < 1e-9,
          "cost 相同时两个槽位得分相同（%.6f vs %.6f）" % (a or 0, b or 0))

    # 不同 cost -> 得分应不同
    diff = [card(1, 200, 0), card(2, 200, 5)]
    _, _, d_diff = P.decide(state(diff, energy=6))
    c = score_of(d_diff, 1)
    d = score_of(d_diff, 2)
    check(c is not None and d is not None and abs(c - d) > 1e-6,
          "cost 不同时得分不同（%.4f vs %.4f）" % (c or 0, d or 0))


def test_real_scenario():
    """用户报的真实场景：扬沙 360/cost1 vs 主轴 150/cost2。"""
    print()
    print("=== 真实场景：扬沙 vs 主轴 ===")
    skills = [card(1, 360, 1), card(2, 150, 2)]
    act, why, detail = P.decide(state(skills))
    check(act == ("skill", 1), "选扬沙（360/cost1），实际 %s" % (act,))

    # 反过来：如果扬沙读成 60（OCR 错误），应该选主轴
    skills2 = [card(1, 60, 1), card(2, 150, 2)]
    act2, _, _ = P.decide(state(skills2))
    check(act2 == ("skill", 2),
          "扬沙只读到 60 时选主轴（%s）" % (act2,))


def test_high_cost_needs_energy():
    """高耗技能在能量不足时不应被优先（lit=False 或能量算不过来）。"""
    print()
    print("=== 高耗 vs 低耗，能量有限 ===")
    # energy=1，cost6 的技能放不出（这里用 lit=False 表达）
    skills = [card(1, 400, 6, lit=False), card(2, 150, 2)]
    act, _, _ = P.decide(state(skills, energy=1))
    check(act == ("skill", 2),
          "400/cost6 不可点时选 150/cost2（%s）" % (act,))


def test_robot_cost_source():
    """确认 robot._decide_v2 的 cost 来自 my_card_costs，不是技能名。"""
    print()
    print("=== robot 的 cost 来源 ===")
    import inspect
    import robot
    src = inspect.getsource(robot.Bot._decide_v2)
    check('slot_costs.get(s["index"]' in src,
          'cost 来自 slot_costs（查表）')
    # 注释里会提到旧写法，所以只检查真正的代码行
    code_lines = [ln for ln in src.splitlines()
                  if "E.D.skill(s.get" in ln and not ln.strip().startswith("#")]
    check(not code_lines,
          '不再用 E.D.skill(name) 查耗能（name 恒为 None）')
    check("POLICY WARN" in src,
          "有 cost 全为 0 的退化检测")
    check("energy_lb" in src,
          "energy 用点亮卡最大耗能作下界（不直接用点亮数）")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    test_cost_affects_score()
    test_cost_zero_is_flat()
    test_real_scenario()
    test_high_cost_needs_energy()
    test_robot_cost_source()
    print()
    if FAILS:
        print("FAILED %d:" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS")
