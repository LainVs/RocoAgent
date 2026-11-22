# -*- coding: utf-8 -*-
"""S15 测试：应对技效果引擎（效果子句 → ctx 原语 → Battle 消费）。

计划：docs/PLAN_S15_COUNTER.md
"""
import sys

sys.path.insert(0, "src")
import nrc_fx  # noqa: E402
import simulator as SIM  # noqa: E402

FAILS = []
D = SIM.Dex()


def check(cond, msg):
    print(("  \u2713 " if cond else "  \u2717 ") + msg)
    if not cond:
        FAILS.append(msg)


def main():
    print("--- 1. \u523a\u76fe\uff08\u5e94\u5bf9\u653b\u51fb\uff1a\u654c\u65b9\u7269\u653b-70%\uff09---")
    bt = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"], seed=0,
                    cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                            "max_hp": 9999, "moves": ["\u523a\u76fe"]}],
                    cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                            "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt.begin_turn()
    ev = bt.step("skill", 0, "skill", 0)
    dg = [e for e in ev if "\u5e94\u5bf9\u51cf\u76ca" in e]
    print("  ", dg)
    check(bool(dg), "\u5e94\u5bf9\u51cf\u76ca\u4e8b\u4ef6")
    check(bt.foe.atk_buff < 0, "\u654c\u65b9\u7269\u653b\u4e0b\u964d\uff08%+.2f\uff09"
          % bt.foe.atk_buff)

    print("--- 2. \u5439\u708e\uff08\u5e94\u5bf9\u72b6\u6001\uff1a\u5a01\u529b\u7ffb\u500d\uff09---")
    # 吹炎应对状态技 → 威力 ×2
    bt2 = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"], seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "moves": ["\u5439\u708e"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999,
                             "moves": ["\u529b\u91cf\u589e\u6548"]}])
    bt2.begin_turn()
    ev2 = bt2.step("skill", 0, "skill", 0)
    sb = [e for e in ev2 if "\u5e94\u5bf9\u589e\u76ca" in e]
    print("  ", sb)
    check(bool(sb) and bt2.me.atk_buff > 0,
          "\u5439\u708e \u5e94\u5bf9\u589e\u76ca \u7269\u653b+100%%"
          "\uff08atk %+.2f\uff09" % bt2.me.atk_buff)

    print("--- 3. \u5e94\u5bf9\uff01\u653e\u6674\uff08\u6539\u4e3a\u6c38\u4e45+100%\uff09---")
    bt3 = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"], seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999,
                             "moves": ["\u5e94\u5bf9\uff01\u653e\u6674"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u9632\u5fa1"]}])
    bt3.begin_turn()
    ev3 = bt3.step("skill", 0, "skill", 0)
    pp = [e for e in ev3 if "\u6c38\u4e45\u5a01\u529b" in e]
    print("  ", pp)
    check(bool(pp), "\u6c38\u4e45\u5a01\u529b\u6302\u8f7d")
    check(getattr(bt3.me, "_perm_power_delta", 0) == 100, "delta=100")

    print("--- 4. \u5e94\u5bf9\uff01\u6076\u4f5c\u5267\uff08\u654c\u65b9\u5931\u53bb6\u80fd\u91cf\uff09---")
    bt4 = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"], seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999,
                             "moves": ["\u5e94\u5bf9\uff01\u6076\u4f5c\u5267"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u9632\u5fa1"]}])
    bt4.begin_turn()
    e0 = bt4.foe.energy
    ev4 = bt4.step("skill", 0, "skill", 0)
    ee = [e for e in ev4 if "\u654c\u65b9\u80fd\u91cf" in e]
    print("  ", ee)
    check(bt4.foe.energy == max(0, e0 - 6), "\u654c\u65b9\u80fd\u91cf -6\uff08%d\u2192%d\uff09"
          % (e0, bt4.foe.energy))

    print("--- 4b. \u5077\u88ad\uff08\u5e94\u5bf9\u72b6\u6001\uff1a\u5a01\u529b\u00d74\uff09---")
    bt4b = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"],
                      seed=0,
                      cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                              "max_hp": 9999, "moves": ["\u5077\u88ad"]}],
                      cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                              "max_hp": 9999,
                              "moves": ["\u529b\u91cf\u589e\u6548"]}])
    bt4b.begin_turn()
    ev4b = bt4b.step("skill", 0, "skill", 0)
    mul4b = [e for e in ev4b if "\u5e94\u5bf9\u5a01\u529b" in e]
    print("  ", mul4b)
    check(bool(mul4b), "\u5077\u88ad \u5e94\u5bf9\u5a01\u529b \u00d74 \u89e6\u53d1")

    print("--- 5. \u5438\u9b42\uff08\u6539\u4e3a\u6253\u65ad / \u6076\u4f5c\u5267 \u6253\u65ad\u7c7b\uff09---")
    # 摇篮曲系：额外打断
    bt5 = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"], seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999,
                             "moves": ["\u5e94\u5bf9\uff01\u6447\u7bee\u66f2"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u9632\u5fa1"]}])
    bt5.begin_turn()
    ev5 = bt5.step("skill", 0, "skill", 0)
    it = [e for e in ev5 if "\u6253\u65ad" in e]
    print("  ", it)
    check(bool(it), "\u989d\u5916\u6253\u65ad\u751f\u6548")

    print()
    if FAILS:
        print("FAILED %d" % len(FAILS))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
