# -*- coding: utf-8 -*-
"""S12 测试：ABILITY_COMPUTE 开场技能改写（6 特性）。"""
import sys

sys.path.insert(0, "src")
import simulator as SIM  # noqa: E402

FAILS = []
D = SIM.Dex()


def check(cond, msg):
    print(("  \u2713 " if cond else "  \u2717 ") + msg)
    if not cond:
        FAILS.append(msg)


def main():
    print("--- 1. \u5feb\u9524\uff08\u80fd\u8017<3 \u8fc5\u6377\uff09---")
    bt = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"], seed=0,
                    cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                            "bloodline": "\u666e\u901a",
                            "max_hp": 9999, "moves": ["\u62cd\u51fb",
                                                      "\u541e\u566c"]}],
                    cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                            "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    # 手动挂特性（音速犬特性=专注力）
    bt.a[0].trait = "\u5feb\u9524"
    evs = [e for team in (bt.a,) for p in team
           for e in __import__("nrc_fx").trait_fire(
               p, None, "ON_BATTLE_START")]
    print("  ", evs)
    sw = getattr(bt.a[0], "_swift_pred_state", None) or []
    check("\u62cd\u51fb" in sw and "\u541e\u566c" not in sw,
          "\u62cd\u51fb(cost1)\u8fc5\u6377\uff0c\u541e\u566c(cost6)\u4e0d\u8fc5\u6377")

    print("--- 2. \u4e0d\u79fb\uff08\u7eaf\u653b\u51fb +30%\uff09---")
    bt2 = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"], seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "moves": ["\u706b\u4e91\u8f66"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt2.a[0].trait = "\u4e0d\u79fb"
    import nrc_fx
    evs2 = nrc_fx.trait_fire(bt2.a[0], None, "ON_BATTLE_START")
    print("  ", evs2)
    mods = getattr(bt2.a[0], "_skill_power_mods", None) or {}
    check(mods.get("\u706b\u4e91\u8f66") == 0.3, "\u706b\u4e91\u8f66 \u5a01\u529b+30% \u6302\u8f7d")
    # 对照伤害
    import re as _re
    bt2.begin_turn()
    ev_a = bt2.step("skill", 0, "skill", 0)
    d1 = None
    for e in ev_a:
        if "\u706b\u4e91\u8f66" in e and "\u2192" in e:
            d1 = int(_re.search(r"-\s*(\d+)", e).group(1))
    bt3 = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"], seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "moves": ["\u706b\u4e91\u8f66"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt3.a[0].trait = ""              # 中性基线（不移侧也无攻击增益）
    bt3.begin_turn()
    ev_b = bt3.step("skill", 0, "skill", 0)
    d0 = None
    for e in ev_b:
        if "\u706b\u4e91\u8f66" in e and "\u2192" in e:
            d0 = int(_re.search(r"-\s*(\d+)", e).group(1))
    print("  \u4f24\u5bb3: mod=%s base=%s" % (d1, d0))
    if d1 and d0:
        check(abs(d1 - d0 * 1.3) <= max(3, int(d0 * 0.03)),
              "\u5a01\u529b +30%% \u2192 1.3 \u500d\uff08%d vs %d\uff09" % (d1, d0))

    print("--- 3. \u6eb6\u89e3\u6269\u6563\uff08\u6bd2\u7cfb\u8ba1\u6570\u2192\u6c34\u7cfb\u4e2d\u6bd2\uff09---")
    bt4 = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u97f3\u901f\u72ac"], seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "bloodline": "\u6bd2",
                             "moves": ["\u6ce1\u6cab", "\u5929\u6d2a",
                                       "\u6e83\u70c2\u89e6\u78b0"]}],
                     cfg_b=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt4.a[0].trait = "\u6eb6\u89e3\u6269\u6563"
    evs4 = nrc_fx.trait_fire(bt4.a[0], None, "ON_BATTLE_START")
    print("  ", evs4)
    check(getattr(bt4.a[0], "_poison_skill_count", 0) == 1,
          "1 \u4e2a\u6bd2\u7cfb\u6280\u80fd\uff08\u6e83\u70c2\u89e6\u78b0\uff09")
    bt4.begin_turn()
    # 用水系技（泡沫）打 → 附加 1 层中毒
    wi = next(i for i, m in enumerate(bt4.me.moves)
              if m["name"] == "\u5929\u6d2a")
    ev = bt4.step("skill", wi, "skill", 0)
    ps = [e for e in ev if "\u4e2d\u6bd2" in e]
    print("  ", ps)
    check(any("\u4e2d\u6bd2" in e for e in ps),
          "\u6c34\u7cfb\u6280\u80fd\u9644\u52a0\u4e2d\u6bd2\uff08\u6eb6\u89e3\u6269\u6563\uff09")

    print("--- 4. \u98d3\u98ce\uff08\u7ffc\u7cfb\u5171\u4eab\u8fc5\u6377 + \u88ab\u51fb\u8d25\u9b54\u529b\uff09---")
    bt5 = SIM.Battle(D, ["\u97f3\u901f\u72ac", "\u706b\u795e"],
                     ["\u97f3\u901f\u72ac"], seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "bloodline": "\u666e\u901a",
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]},
                            {"name": "\u706b\u795e", "iv": 60,
                             "bloodline": "\u666e\u901a",
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt5.a[0].trait = "\u98d3\u98ce"
    # 队友翼系？火神非翼——手动给队友挂翼系类型模拟
    bt5.a[1].types = ["\u7ffc"]
    evs5 = nrc_fx.trait_fire(bt5.a[0], None, "ON_BATTLE_START")
    print("  ", evs5)
    sw5 = getattr(bt5.a[0], "_swift_pred_state", None) or []
    check("\u62cd\u51fb" in sw5, "\u961f\u53cb\u7ffc\u7cfb\u540c\u540d\u6280\u80fd \u2192 \u8fc5\u6377")
    # 被击败魔力 -1（ON_BE_KILLED）
    bt5.begin_turn()
    bt5.me.trait = "\u98d3\u98ce"
    m0 = bt5.magic["a"]
    evs5b = nrc_fx.trait_fire(bt5.me, bt5.foe, "ON_BE_KILLED")
    print("  ", evs5b)
    check(bt5.magic["a"] == m0 - 1, "\u88ab\u51fb\u8d25 \u9b54\u529b -1")

    print()
    if FAILS:
        print("FAILED %d" % len(FAILS))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
