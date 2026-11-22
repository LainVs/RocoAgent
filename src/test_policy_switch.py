# -*- coding: utf-8 -*-
"""test_policy_switch.py —— 换人/攻击决策矩阵（2026-11-22 定案的 5 用例）。

手推真值见 docs/PLAN_POLICY_INCOMING.md §五。这些用例锁住两个语义：
  1. KO 幻想：替补整池 max 不给 KO 加成（×0.6 还能杀才算）
  2. 安全换人：必死 + 替补能扛 => 必须换，不受任何"省回合"逻辑压制

不依赖游戏/BLE，纯函数级验证。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"D:\Tools")

import paths  # noqa: E402

FAIL = []


def run(name, state, want):
    import policy_v2 as PV
    action, why, detail = PV.decide(state)
    ok = action == want
    print("%-34s want=%-14s got=%-14s %s" % (
        name, want, action, "OK" if ok else "**FAIL**"))
    print("    why: %s" % why)
    if not ok:
        FAIL.append(name)
    return ok


def pet(name, dmg, hp=366, hp_max=366, energy=3):
    return {"me": {"name": name, "hp": hp, "hp_max": hp_max, "energy": energy,
                   "skills": [{"i": 1, "dmg": dmg, "cost": 1, "kind": "num",
                               "lit": True, "priority": 0}]},
            "foe": {"name": "白发路路", "hp": 300, "hp_max": 420},
            "bench": [{"name": "恶魔狼", "hp": 417, "hp_max": 417,
                       "energy": 5}],
            "turn": 3, "buffed": False}


def foe_hp_state(hp):
    """改 foe 血量的辅助（run 前调用方自行改 state['foe']['hp']）。"""
    return hp


def main():
    print("== policy switch matrix ==")
    # A. 健康：攻击，不换（KO 幻想回归锁）
    s = pet("音速犬", 175)
    run("A healthy -> attack", s, ("skill", 1))

    # B. 必死 + 替补能扛 -> 必须换
    s = pet("音速犬", 175, hp=120)
    run("B dying -> switch", s, ("switch", 1))

    # C. 承伤不死、对面血量池六折杀不掉 -> 攻击
    s = pet("音速犬", 175, hp=150)
    s["foe"]["hp"] = 350
    run("C no kill anywhere -> attack", s, ("skill", 1))

    # D. 我卡杀不掉 + 替补六折能杀 -> 换
    s = pet("音速犬", 100)
    s["foe"]["hp"] = 150
    run("D bench lethal only -> switch", s, ("switch", 1))

    # E. 我卡能杀 -> 直接杀，不换
    s = pet("音速犬", 175)
    s["foe"]["hp"] = 150
    run("E card kills -> attack", s, ("skill", 1))

    print()
    if FAIL:
        print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
