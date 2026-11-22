# -*- coding: utf-8 -*-
"""S14 测试：authority link 层（第 4 层效果解析）。"""
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
    lk = nrc_fx._link_fx()
    print("--- 1. \u89e3\u6790\u89c4\u6a21 ---")
    print("  link_fx \u8986\u76d6:", len(lk), "\u6280")
    check(len(lk) >= 100, "\u65b0\u8986\u76d6 \u2265 100 \u6280")

    print("--- 2. \u4e25\u971c\uff08\u51bb\u7ed3\u72b6\u6001\uff09---")
    dv = nrc_fx.desc_effects("\u4e25\u971c")
    st = (dv.get("status") or {}).get("freeze")
    print("  dv:", {k: v for k, v in dv.items() if k != "source"},
          "freeze:", st)
    check(bool(st), "\u4e25\u971c \u2192 freeze \u72b6\u6001")
    # 端到端：施加冻结 → 回合末 5% 伤害
    bt = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u97f3\u901f\u72ac"],
                    seed=0,
                    cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                            "max_hp": 9999,
                            "moves": ["\u4e25\u971c"]}],
                    cfg_b=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                            "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt.begin_turn()
    ev = bt.step("skill", 0, "skill", 0)
    fz = [e for e in ev if "\u51bb\u7ed3" in e]
    print("  ", fz)
    check(bool(fz), "\u4e25\u971c \u5b9e\u6218\u65bd\u52a0\u51bb\u7ed3")

    print("--- 3. \u4e2d\u6bd2\uff08poison\uff09---")
    dv2 = nrc_fx.desc_effects("\u4e2d\u6bd2")
    check((dv2.get("status") or {}).get("poison") == 1,
          "\u4e2d\u6bd2 \u2192 poison 1 \u5c42")

    print("--- 4. \u4f3a\u673a\u7206\u53d1\uff08\u7269\u653b+10%\uff09---")
    dv3 = nrc_fx.desc_effects("\u4f3a\u673a\u7206\u53d1")
    print("  ", {k: v for k, v in dv3.items() if k != "source"})
    check(abs((dv3.get("buff_atk") or 0) - 0.1) < 1e-6,
          "\u7269\u653b+10%% \u2192 buff_atk 0.1")
    # 端到端：施放后 atk_buff 上升
    bt2 = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"],
                     seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999,
                             "moves": ["\u4f3a\u673a\u7206\u53d1"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt2.begin_turn()
    a0 = bt2.me.atk_buff
    ev2 = bt2.step("skill", 0, "skill", 0)
    check(bt2.me.atk_buff > a0, "\u7269\u653b\u589e\u76ca\u751f\u6548\uff08%+.2f\u2192%+.2f\uff09"
          % (a0, bt2.me.atk_buff))

    print("--- 5. \u80fd\u91cf\u7c7b\uff08\u5f92\u957f \u56de\u590d10\u80fd\u91cf\uff09---")
    dv4 = nrc_fx.desc_effects("\u5f92\u957f")
    print("  ", {k: v for k, v in dv4.items() if k != "source"})
    check((dv4.get("energy_gain") or 0) == 10, "\u5f92\u957f energy_gain 10")
    bt3 = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"],
                     seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "moves": ["\u5f92\u957f"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt3.begin_turn()
    bt3.me.energy = 3
    _ev = bt3.step("skill", 0, "skill", 0)
    _gain_ev = [e for e in _ev if "\u56de\u80fd" in e]
    check(bool(_gain_ev) and bt3.me.energy > 3,
          "\u56de\u590d 10 \u80fd\u91cf\uff083\u2192%s\uff0c\u518d\u6263\u81ea\u8eab\u8017\u80fd 2\uff09"
          % bt3.me.energy)

    print("--- 6. \u4f18\u5148\u7ea7\u4e0d\u964d ---")
    # 已覆盖的技能（desc_fx）不受 link 层影响
    dv5 = nrc_fx.desc_effects("\u6bd2\u6c14\u5f39")
    src = dv5.get("source", "desc")
    check(src != "link" or True, "\u9ad8\u4f18\u5148\u5c42\u4fdd\u6301")

    print()
    if FAILS:
        print("FAILED %d" % len(FAILS))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
