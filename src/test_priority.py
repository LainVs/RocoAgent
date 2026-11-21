# -*- coding: utf-8 -*-
"""test_priority.py —— 先发制人（priority>0）的决策回归。

用户要求："先发制人的什么时候使用"。

规则（policy_v2.decide）：
  1. 这回合会死 + 先发技能能反杀 -> 用先发技能（不用换人）
  2. 先发技能打不死 -> 不因为它 priority 高就优先（该换人还是换人）
  3. 不会因为 priority>0 就压过明显更高的伤害

跑法：
    python src\\test_priority.py
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


def card(i, dmg, cost=2, lit=True, priority=0, kind="num"):
    return {"i": i, "dmg": dmg, "cost": cost, "kind": kind,
            "lit": lit, "priority": priority}


def state(skills, my_hp=400, foe_hp=400, bench=None):
    return {
        "me": {"name": "测试", "hp": my_hp, "hp_max": 400,
               "energy": 10, "skills": skills},
        "foe": {"name": "对手", "hp": foe_hp, "hp_max": 400},
        "bench": bench or [], "turn": 3, "buffed": False,
    }


def main():
    print("== 先发制人 ==")

    # 1. 这回合会死（未知对手 -> 默认承受 35% 最大血 = 140），
    #    先发技能 150 能反杀 120 血的对手 -> 必须用它，不换人。
    st = state([card(1, 150, priority=1), card(2, 300)],
               my_hp=100, foe_hp=120,
               bench=[{"name": "替补", "hp": 300, "hp_max": 300}])
    act, why, _d = P.decide(st)
    check(act == ("skill", 1), "dying + priority kill -> skill 1 (%s)" % (act,))
    check("priority" in (why or ""), "reason mentions priority (%s)" % why)

    # 2. 先发技能打不死 -> 不该硬打；有能扛住的替补就换人。
    st = state([card(1, 50, priority=1), card(2, 300)],
               my_hp=100, foe_hp=120,
               bench=[{"name": "替补", "hp": 300, "hp_max": 300}])
    act, why, _d = P.decide(st)
    check(act == ("switch", 1), "priority cannot kill -> switch (%s)" % (act,))

    # 3. 不会因为 priority>0 就压过更高的伤害（两张都能杀，选伤害高的）。
    st = state([card(1, 150, priority=1), card(2, 300)],
               my_hp=400, foe_hp=120)
    act, why, _d = P.decide(st)
    check(act == ("skill", 2), "higher damage wins over priority (%s)" % (act,))

    print()
    if FAILS:
        print("FAILED %d" % len(FAILS))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
