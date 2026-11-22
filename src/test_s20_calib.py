# -*- coding: utf-8 -*-
"""S20 测试：校准表接进机器人预测链路。"""
import sys

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

import foe_calib  # noqa: E402
import policy_v2  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  \u2713 " if cond else "  \u2717 ") + msg)
    if not cond:
        FAILS.append(msg)


def main():
    print("--- 1. foe_calib.k ---")
    # 寂灭骨龙 k=0.34（校准表实测）
    k1 = foe_calib.k("\u5bc2\u706d\u9aa8\u9f99")
    print("  k(寂灭骨龙) =", k1)
    check(abs(k1 - 0.34) < 0.05, "\u5bc2\u706d\u9aa8\u9f99 k\u22480.34")
    k2 = foe_calib.k("\u4e0d\u5b58\u5728\u7684\u7cbe\u7075xyz")
    check(k2 == 1.0, "\u672a\u77e5\u5bf9\u624b k=1.0\uff08\u884c\u4e3a\u4e0d\u53d8\uff09")

    print("--- 2. decide \u63a5\u7ebf\uff08dmg \u7f29\u653e\uff09---")
    # 构造 state：寂灭骨龙（k=0.34）+ 一个 dmg=100 的技能
    state = {
        "me": {"name": "\u6c34\u84dd\u84dd", "hp": 400, "hp_max": 400,
               "energy": 6,
               "skills": [{"i": 1, "dmg": 100, "cost": 2, "kind": "num",
                           "lit": True},
                          {"i": 2, "dmg": 300, "cost": 4, "kind": "num",
                           "lit": True}]},
        "foe": {"name": "\u5bc2\u706d\u9aa8\u9f99", "hp": 900,
                "hp_max": 900},
        "bench": [],
    }
    act, why, detail = policy_v2.decide(state)
    print("  act:", act, "| why:", why[:60])
    # k=0.34 → dmg 100→34, 300→102；foe_hp 900 → 无 kill 路径
    # 行为应与未校准一致（都不 kill）——但 dmg 数值已缩放
    check(act[0] in ("skill", "energy"), "decide \u6b63\u5e38\u8fd4\u56de")
    # 对照：未校准（k=1）时 dmg 300 vs 校准后 102 —— 用同样 state 但
    # 把对手换成未校准精灵验证行为差异
    state2 = dict(state)
    state2["foe"] = {"name": "\u4e0d\u5b58\u5728xyz", "hp": 900,
                     "hp_max": 900}
    act2, why2, _ = policy_v2.decide(state2)
    print("  uncalibrated act:", act2, "| why:", why2[:60])

    print("--- 3. best_damage_from \u7f29\u653e ---")
    bd = policy_v2.best_damage_from("\u6c34\u84dd\u84dd",
                                    "\u5bc2\u706d\u9aa8\u9f99")
    print("  best_damage_from(\u5bc2\u706d\u9aa8\u9f99) =", bd)
    check(bd >= 0, "\u8fd4\u56de\u6b63\u503c")

    print("--- 4. kill \u9608\u503c\u6536\u7d27\uff08\u6821\u51c6\u540e\u66f4\u4fdd\u5b88\uff09---")
    # dmg 300 ×0.34 = 102 < 900 → 不再误判 kill
    state3 = {
        "me": {"name": "\u6c34\u84dd\u84dd", "hp": 400, "hp_max": 400,
               "energy": 6,
               "skills": [{"i": 1, "dmg": 300, "cost": 2, "kind": "num",
                           "lit": True}]},
        "foe": {"name": "\u5bc2\u706d\u9aa8\u9f99", "hp": 300,
                "hp_max": 900},
        "bench": [],
    }
    act3, why3, _ = policy_v3_ = policy_v2.decide(state3)
    print("  act:", act3, "| why:", why3[:90])
    print("  DEBUG why3 full:", why3)
    # 未校准时 300 >= 300 会判 kill；校准后 102 < 300 → 不判 kill
    _no_kill = "kills" not in (why3 or "")
    print("  no_kill =", _no_kill)
    check(_no_kill, "\u6821\u51c6\u540e\u4e0d\u8bef\u5224 kill")

    print()
    if FAILS:
        print("FAILED %d" % len(FAILS))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
