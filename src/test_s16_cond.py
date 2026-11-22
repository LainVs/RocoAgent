# -*- coding: utf-8 -*-
"""S16 测试：HP 阈值条件引擎 + 全机制集成回归。"""
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
    print("--- 1. \u4e91\u884c\u96e8\u65bd\uff08hp>50% \u4f24\u5bb3+30%\uff09---")
    bt = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"], seed=0,
                    cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                            "max_hp": 9999, "moves": ["\u706b\u4e91\u8f66",
                                                      "\u4e91\u884c\u96e8\u65bd"]}],
                    cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                            "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt.begin_turn()
    # 先放云行雨施（hp 100% > 50% → 条件挂载）
    wi = next(i for i, m in enumerate(bt.me.moves)
              if m["name"] == "\u4e91\u884c\u96e8\u65bd")
    ev = bt.step("skill", wi, "skill", 0)
    cd = [e for e in ev if "\u6761\u4ef6\u89c4\u5219" in e]
    print("  ", cd)
    check(bool(cd), "\u6761\u4ef6\u89c4\u5219\u6302\u8f7d")
    # 下回合火云车（hp 仍 >50%）→ 伤害 +30%
    bt.begin_turn()
    ev2 = bt.step("skill", 0, "skill", 0)
    import re as _re
    d1 = None
    for e in ev2:
        if "\u706b\u4e91\u8f66" in e and "\u2192" in e:
            d1 = int(_re.search(r"-\s*(\d+)", e).group(1))
    # 对照：无条件，且同样在 T2 打（专注力只加成入场首回合）
    bt2 = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"], seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "moves": ["\u706b\u4e91\u8f66",
                                                       "\u4e91\u884c\u96e8\u65bd"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt2.begin_turn()
    _wi = next(i for i, m in enumerate(bt2.me.moves)
               if m["name"] == "\u4e91\u884c\u96e8\u65bd")
    bt2.step("skill", _wi, "skill", 0)     # T1 空过同样动作（耗能/时序对齐）
    bt2.begin_turn()
    bt2.a[0]._cond_mods = None             # 清掉条件 → 纯净对照
    ev3 = bt2.step("skill", 0, "skill", 0)
    d0 = None
    for e in ev3:
        if "\u706b\u4e91\u8f66" in e and "\u2192" in e:
            d0 = int(_re.search(r"-\s*(\d+)", e).group(1))
    print("  cond=%s base=%s" % (d1, d0))
    if d1 and d0:
        check(abs(d1 - d0 * 1.3) <= max(3, int(d0 * 0.04)),
              "hp>50%% \u4f24\u5bb3 +30%%\uff08%d vs %d\uff09" % (d1, d0))

    print("--- 2. \u4e91\u884c\u96e8\u65bd hp<50% \u2192 \u53d7\u4f24 -30% ---")
    bt.begin_turn()
    bt.me.hp = bt.me.max_hp // 4        # 25% < 50%
    ev4 = bt.step("skill", 0, "skill", 0)
    # 对方拍击的伤害应小于 base（-30%）
    d2 = None
    for e in ev4:
        if "\u62cd\u51fb" in e and "\u2192" in e:
            d2 = int(_re.search(r"-\s*(\d+)", e).group(1))
    # 对照：对方拍击打无条件音速犬
    d2b = None
    for e in ev3:
        if "\u62cd\u51fb" in e and "\u2192" in e:
            d2b = int(_re.search(r"-\s*(\d+)", e).group(1))
    print("  taken: cond=%s base=%s" % (d2, d2b))
    if d2 and d2b:
        check(d2 < d2b, "hp<50%% \u53d7\u4f24 -30%%\uff08%d < %d\uff09" % (d2, d2b))

    print("--- 3. \u5165\u68a6\uff08hp<50% \u5168\u5c5e\u6027+20%\uff09---")
    dv = nrc_fx.desc_effects("\u5165\u68a6")
    cm = dv.get("cond_mods") or {}
    check(cm.get("hp_lt") == 50 and abs(cm.get("all_stats_pct", 0) - 0.2)
          < 1e-6, "\u5165\u68a6 \u6761\u4ef6\u89e3\u6790")

    print("--- 4. \u96c6\u6210\u56de\u5f52\uff08\u591a\u673a\u5236 6v6 \u8dd1\u6ee1 30 \u56de\u5408\uff09---")
    cfg_a = [{"name": "\u97f3\u901f\u72ac", "iv": 60, "max_hp": 9999,
              "bloodline": "\u706b",
              "moves": ["\u706b\u4e91\u8f66", "\u9632\u5fa1"]},
             {"name": "\u5723\u5149\u8fea\u83ab", "iv": 60, "max_hp": 9999,
              "bloodline": "\u5149",
              "moves": ["\u6298\u7ebf\u51b2\u51fb"]},
             {"name": "\u6c34\u84dd\u84dd", "iv": 60, "max_hp": 9999,
              "moves": ["\u5929\u6d2a", "\u9632\u5fa1"]}]
    cfg_b = [{"name": "\u97f3\u901f\u72ac", "iv": 60, "max_hp": 9999,
              "moves": ["\u706b\u4e91\u8f66"]},
             {"name": "\u6c34\u84dd\u84dd", "iv": 60, "max_hp": 9999,
              "moves": ["\u541e\u566c", "\u9632\u5fa1"]},
             {"name": "\u706b\u795e", "iv": 60, "max_hp": 9999,
              "bloodline": "\u9996\u9886",
              "moves": ["\u706b\u4e91\u8f66", "\u9632\u5fa1"]}]
    bt4 = SIM.Battle(D, ["\u97f3\u901f\u72ac", "\u5723\u5149\u8fea\u83ab",
                         "\u6c34\u84dd\u84dd"],
                     ["\u97f3\u901f\u72ac", "\u6c34\u84dd\u84dd",
                      "\u706b\u795e"],
                     cfg_a=cfg_a, cfg_b=cfg_b, seed=7,
                     magic_kind_a="\u613f\u529b\u5f3a\u5316")
    bt4.begin_turn()
    n_ev = 0
    keys_hit = set()
    KW = ("\u613f\u529b", "\u5e94\u5bf9", "\u8131\u79bb", "\u8fc5\u6377",
          "\u9996\u9886\u5316", "\u51cf\u4f24")
    for turn in range(30):
        if bt4.done:
            break
        ev = bt4.step("skill", 0, "skill", 0)
        n_ev += len(ev)
        for e in ev:
            for k in KW:
                if k in e:
                    keys_hit.add(k)
        if not bt4.done:
            bt4.begin_turn()
    print("  turns=%d done=%s events=%d keys=%s"
          % (bt4.turn, bt4.done, n_ev, sorted(keys_hit)))
    check(bt4.done in ("a", "b", None), "\u5bf9\u5c40\u72b6\u6001\u5408\u6cd5")
    check(n_ev > 30, "\u4e8b\u4ef6\u6d41\u975e\u7a7a\uff08%d\uff09" % n_ev)
    check(bt4.turn >= 30, "\u8dd1\u6ee1 30 \u56de\u5408\u65e0\u5d29\u6e83")

    print()
    if FAILS:
        print("FAILED %d" % len(FAILS))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
