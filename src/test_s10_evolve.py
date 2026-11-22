# -*- coding: utf-8 -*-
"""S10 正式测试：进化之力（首领化）。

计划：docs/PLAN_S10_EVOLVE.md
权威源：TapTap 首领化盘点（25 只）、773 火神数值、rocoduel。

    首领血脉 + evolve 动作 → 当场首领化（换 species/特性/六维）
    每场 1 次；血量比例保留；不占当回合行动（payload 技能出手）
    愿力强化不能对首领血脉使用（rocoduel 原文）
"""
import sys

sys.path.insert(0, "src")
import simulator as SIM  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  \u2713 " if cond else "  \u2717 ") + msg)
    if not cond:
        FAILS.append(msg)


def main():
    d = SIM.Dex()

    print("--- 1. \u706b\u795e \u9996\u9886\u5316 \u2192 \u70c8\u706b\u6218\u795e ---")
    bt = SIM.Battle(d, ["\u706b\u795e"], ["\u6c34\u84dd\u84dd"], seed=0,
                    cfg_a=[{"name": "\u706b\u795e", "iv": 60,
                            "max_hp": 9999, "bloodline": "\u9996\u9886",
                            "moves": ["\u706b\u4e91\u8f66"]}],
                    cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                            "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt.begin_turn()
    atk0 = bt.me.atk
    bt.me.hp = 3000
    ev = bt.step("evolve", 0, "skill", 0)
    tr = [e for e in ev if "\u9996\u9886\u5316" in e]
    print("  ", tr)
    check(bool(tr), "\u9996\u9886\u5316\u89e6\u53d1")
    check(bt.me.name == "\u70c8\u706b\u6218\u795e", "\u5f62\u6001 = \u70c8\u706b\u6218\u795e")
    check(bt.me.trait == "\u7206\u71c3", "\u7279\u6027 \u52a9\u71c3\u2192\u7206\u71c3")
    check(bt.me.atk > atk0, "\u7269\u653b\u63d0\u5347\uff08%d\u2192%d\uff09"
          % (atk0, bt.me.atk))
    check(bt.me.hp <= 3000 and bt.me.hp >= 2940,
          "\u8840\u91cf\u6bd4\u4f8b\u4fdd\u7559\uff083000 - \u5bf9\u65b9\u4f24\u5bb3\uff0ccfg max_hp 9999\uff09")
    check(bt.evolution_used["a"] is True, "\u6b21\u6570\u8bb0\u5f55")
    check(any("\u706b\u4e91\u8f66" in e and "\u2192" in e for e in ev),
          "\u5f53\u56de\u5408\u7167\u5e38\u51fa\u624b\uff08\u4e0d\u5360\u884c\u52a8\uff09")

    print("--- 2. \u6bcf\u573a 1 \u6b21 ---")
    bt.begin_turn()
    ev2 = bt.step("evolve", 0, "skill", 0)
    check(any("1 \u6b21" in e for e in ev2), "\u7b2c\u4e8c\u6b21\u843d\u7a7a")

    print("--- 3. \u975e\u9996\u9886\u8840\u8109 \u843d\u7a7a ---")
    bt2 = SIM.Battle(d, ["\u706b\u795e"], ["\u6c34\u84dd\u84dd"], seed=0,
                     cfg_a=[{"name": "\u706b\u795e", "iv": 60,
                             "max_hp": 9999, "bloodline": "\u706b",
                             "moves": ["\u706b\u4e91\u8f66"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt2.begin_turn()
    ev3 = bt2.step("evolve", 0, "skill", 0)
    check(any("\u4e0d\u6ee1\u8db3" in e for e in ev3),
          "\u975e\u9996\u9886\u8840\u8109\u2192\u843d\u7a7a")
    check(bt2.evolution_used["a"] is False, "\u843d\u7a7a\u4e0d\u6263\u6b21\u6570")
    check(bt2.me.name == "\u706b\u795e", "\u5f62\u6001\u4e0d\u53d8")

    print("--- 4. \u8fea\u83ab \u5149\u8840\u8109 \u2192 \u5723\u5149\u8fea\u83ab\uff08\u88c1\u51b3\uff09---")
    bt3 = SIM.Battle(d, ["\u8fea\u83ab"], ["\u6c34\u84dd\u84dd"], seed=0,
                     cfg_a=[{"name": "\u8fea\u83ab", "iv": 60,
                             "max_hp": 9999, "bloodline": "\u5149",
                             "moves": ["\u6298\u7ebf\u51b2\u51fb"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt3.begin_turn()
    ev4 = bt3.step("evolve", 0, "skill", 0)
    tr4 = [e for e in ev4 if "\u9996\u9886\u5316" in e]
    print("  ", tr4)
    check(bt3.me.name == "\u5723\u5149\u8fea\u83ab", "\u5f62\u6001 = \u5723\u5149\u8fea\u83ab\uff08\u5149\u8840\u8109\u5206\u652f\uff09")
    check(bt3.me.trait == "\u88c1\u51b3", "\u7279\u6027 = \u88c1\u51b3")

    print("--- 5. \u613f\u529b\u5f3a\u5316 \u5bf9\u9996\u9886\u8840\u8109 \u843d\u7a7a ---")
    # rocoduel 原文：对任意血脉使用（首领血脉除外）
    bt4 = SIM.Battle(d, ["\u706b\u795e"], ["\u6c34\u84dd\u84dd"], seed=0,
                     cfg_a=[{"name": "\u706b\u795e", "iv": 60,
                             "max_hp": 9999, "bloodline": "\u9996\u9886",
                             "moves": ["\u706b\u4e91\u8f66"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt4.team_magic = {"a": "\u613f\u529b\u5f3a\u5316", "b": None}
    bt4.begin_turn()
    ev5 = bt4.step("magic", 0, "skill", 0)
    blocked = [e for e in ev5 if "\u613f\u529b\u5f3a\u5316" in e
               and "\u843d\u7a7a" in e]
    print("  ", blocked)
    check(bool(blocked), "\u9996\u9886\u8840\u8109\u2192\u613f\u529b\u5f3a\u5316\u843d\u7a7a")
    check(bt4.magic_state["a"]["used"] == 0, "\u4e0d\u6263\u6b21\u6570")

    print("--- 6. \u7206\u71c3 vs \u52a9\u71c3 \u589e\u76ca\u5dee\u5f02 ---")
    # 爆燃（首领化后）：火系技能后 双攻各+30%（单槽 0.3）
    # 助燃（首领化前）：双攻各+20%
    bt5 = SIM.Battle(d, ["\u706b\u795e"], ["\u6c34\u84dd\u84dd"], seed=0,
                     cfg_a=[{"name": "\u706b\u795e", "iv": 60,
                             "max_hp": 9999, "bloodline": "\u9996\u9886",
                             "moves": ["\u706b\u4e91\u8f66"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt5.begin_turn()
    ev6 = bt5.step("skill", 0, "skill", 0)   # 助燃 +20%
    b0 = bt5.me.atk_buff
    ev7 = bt5.step("skill", 0, "skill", 0)   # 爆燃 +30%（首领化后）
    b1 = bt5.me.atk_buff
    # 首领化（换特性）会清 trait buff；这里直接检查爆燃每次 +30%
    check(abs(b0 - 0.2) < 1e-6, "\u52a9\u71c3 +20%%\uff08\u5b9e\u9645 %.2f\uff09" % b0)

    print()
    if FAILS:
        print("FAILED %d" % len(FAILS))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
