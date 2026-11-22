# -*- coding: utf-8 -*-
"""S13 测试：SLOT_LOCK / AUTO_SWITCH / COUNTER_ACCUMULATE（9 特性）。

计划：docs/PLAN_S13_SWITCH.md
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
    print("--- 1. \u6b63\u4f4d\u5b9d\u5251\uff08\u69fd\u4f4d\u9501\u5b9a [0]\uff09---")
    bt = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"], seed=0,
                    cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                            "max_hp": 9999, "bloodline": "\u666e\u901a",
                            "moves": ["\u62cd\u51fb", "\u541e\u566c"]}],
                    cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                            "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt.a[0].trait = "\u6b63\u4f4d\u5b9d\u5251"
    evs = nrc_fx.trait_fire(bt.a[0], None, "PASSIVE")
    print("  ", evs)
    check(getattr(bt.a[0], "_allowed_slots", None) == {0}, "\u9501\u5b9a [0]")
    # 选 1 号位 → 回落 0 号位
    bt.begin_turn()
    ev = bt.step("skill", 1, "skill", 0)
    atk = [e for e in ev if "\u62cd\u51fb" in e and "\u2192" in e]
    check(bool(atk), "\u9501\u5916\u69fd\u56de\u843d\u5230 0 \u53f7\u4f4d\uff08\u62cd\u51fb\u51fa\u624b\uff09")

    print("--- 2. \u5b9d\u5251\u738b\u724c\uff08[0, 2]\uff09---")
    bt2 = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"], seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "bloodline": "\u666e\u901a",
                             "moves": ["\u62cd\u51fb", "\u706b\u4e91\u8f66",
                                       "\u541e\u566c"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt2.a[0].trait = "\u5b9d\u5251\u738b\u724c"
    nrc_fx.trait_fire(bt2.a[0], None, "PASSIVE")
    check(getattr(bt2.a[0], "_allowed_slots", None) == {0, 2},
          "\u9501\u5b9a [0, 2]")

    print("--- 3. \u8b66\u60d5\uff08\u80fd\u91cf 0 \u2192 \u81ea\u52a8\u8131\u79bb\uff09---")
    bt3 = SIM.Battle(D, ["\u97f3\u901f\u72ac", "\u6c34\u84dd\u84dd"],
                     ["\u97f3\u901f\u72ac"], seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]},
                            {"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}],
                     cfg_b=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt3.a[0].trait = "\u8b66\u60d5"
    nrc_fx.trait_fire(bt3.a[0], bt3.b[0], "ON_TURN_END", {"battle": bt3})
    bt3.begin_turn()
    bt3.me.energy = 0
    old = bt3.me.name
    ev = bt3.step("skill", 0, "skill", 0)
    sw = [e for e in ev if "\u81ea\u52a8\u8131\u79bb" in e]
    print("  ", sw)
    check(bool(sw), "\u80fd\u91cf 0 \u2192 \u81ea\u52a8\u8131\u79bb")
    check(bt3.me.name != old, "\u66ff\u8865\u5165\u573a\uff08%s\u2192%s\uff09"
          % (old, bt3.me.name))

    print("--- 4. \u817e\u632a\uff08\u653b\u51fb\u5e94\u5bf9 1 \u6b21 \u2192 \u53d8\u8eab\u68cb\u7eee\u540e\uff09---")
    # 腾挪载体是棋棋X（白/黑子）——直接用名字验证：造一个名字含白子的测试场景
    # 用 rebuild 逻辑单测
    # 腾挪 = 攻击技应对 1 次：我方用应对！愿力冲击（攻击类，
    # desc 应对状态）反制对方的状态技（力量增效）
    bt4 = SIM.Battle(D, ["\u6c34\u84dd\u84dd"], ["\u97f3\u901f\u72ac"],
                     seed=0,
                     cfg_a=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999,
                             "moves": ["\u5e94\u5bf9\uff01\u613f\u529b\u51b2\u51fb"]}],
                     cfg_b=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999,
                             "moves": ["\u529b\u91cf\u589e\u6548"]}])
    bt4.a[0].name = "\u68cb\u9f50\u58ee\uff08\u767d\u5b50\uff09"  # 模拟白子载体
    bt4.a[0].trait = "\u817e\u632a"
    bt4.begin_turn()
    ev4 = bt4.step("skill", 0, "skill", 0)
    tr4 = [e for e in ev4 if "\u53d8\u8eab" in e or "\u5e94\u5bf9" in e]
    print("  ", tr4)
    # 防御应对攻击（对方拍击是攻击类）→ 腾挪阈值 1 → 变身
    check(bt4.a[0].name == "\u68cb\u7eee\u540e\uff08\u767d\u5b50\uff09",
          "\u53d8\u8eab \u68cb\u7eee\u540e\uff08\u767d\u5b50\uff09\uff08\u5b9e\u9645 %s\uff09"
          % bt4.a[0].name)
    check(bt4.a[0].hp >= bt4.a[0].max_hp - 200,
          "\u56de\u6ee1\u8840\uff08\u5bf9\u65b9\u540e\u7eed\u4f24\u5bb3\u540e\u4ecd\u63a5\u8fd1\u6ee1）")
    check(bt4.a[0].energy >= SIM.ENERGY_CAP - 4,
          "\u56de\u6ee1\u80fd\u91cf\uff08\u5e94\u5bf9\u6280\u80fd\u8017\u80fd\u540e\u4ecd\u9ad8\uff0c%d\uff09"
          % bt4.a[0].energy)

    print("--- 5. \u4fdd\u536b\uff08\u9608\u503c 2\uff09---")
    bt5 = SIM.Battle(D, ["\u6c34\u84dd\u84dd"], ["\u97f3\u901f\u72ac"], seed=0,
                     cfg_a=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u9632\u5fa1"]}],
                     cfg_b=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt5.a[0].name = "\u68cb\u9f50\u58ee\uff08\u9ed1\u5b50\uff09"
    bt5.a[0].trait = "\u4fdd\u536b"
    bt5.begin_turn()
    ev5 = bt5.step("skill", 0, "skill", 0)
    check(bt5.a[0].name != "\u68cb\u7eee\u540e\uff08\u9ed1\u5b50\uff09",
          "\u7b2c 1 \u6b21\u9608\u503c 2 \u2192 \u4e0d\u53d8\u8eab")
    bt5.begin_turn()
    bt5.step("skill", 0, "skill", 0)
    check(bt5.a[0].name == "\u68cb\u7eee\u540e\uff08\u9ed1\u5b50\uff09",
          "\u7b2c 2 \u6b21 \u2192 \u53d8\u8eab\u68cb\u7eee\u540e\uff08\u9ed1\u5b50\uff09\uff08%s\uff09"
          % bt5.a[0].name)

    print()
    if FAILS:
        print("FAILED %d" % len(FAILS))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
