# -*- coding: utf-8 -*-
"""S9 共鸣魔法（其余 3 个）测试 v2。

计划：docs/PLAN_S9_MAGIC.md
权威源：rocoduel.com/zh/dex/magic（国服 S5 数据）

    光合治愈：草系或草血脉 → 回复 50% max_hp
    闪焰爆发：火系或火血脉 → 本回合技能威力 +150%
    节流术：  水系或水血脉 → 本回合技能不扣能耗
    共有：    CD 3 回合、每场 2 次；目标不符 → 落空不扣次数

⚠ 血脉门控提醒：音速犬的「拍击」是普通血脉技（tid=0），不配血脉会被
  正确剔除。测试统一配 bloodline 或用非血脉技（吞噬/火云排爆）。
"""
import re as _re
import sys

sys.path.insert(0, "src")
import simulator as SIM  # noqa: E402

FAILS = []
D = SIM.Dex()


def check(cond, msg):
    print(("  \u2713 " if cond else "  \u2717 ") + msg)
    if not cond:
        FAILS.append(msg)


def battle(magic_a, moves_a, pet_a, blood_a=None, moves_b=None, pet_b=None):
    bt = SIM.Battle(D, [pet_a], [pet_b or "\u6c34\u84dd\u84dd"], seed=0,
                    cfg_a=[{"name": pet_a, "iv": 60, "max_hp": 9999,
                            "bloodline": blood_a, "moves": moves_a}],
                    cfg_b=[{"name": pet_b or "\u6c34\u84dd\u84dd",
                            "iv": 60, "max_hp": 9999,
                            "moves": moves_b or ["\u62cd\u51fb"]}],
                    magic_kind_a=magic_a)
    bt.begin_turn()
    return bt


def dmg_of(events, name):
    for e in events:
        if name in e and "\u2192" in e:
            m = _re.search(r"-\s*(\d+)", e)
            if m:
                return int(m.group(1))
    return None


def main():
    print("--- 1. \u5149\u5408\u6cbb\u6108\uff08\u8349\u7cfb 50%\uff09---")
    grass = D.db.execute(
        "SELECT name FROM species WHERE implemented=1 AND total>0 "
        "AND type_names LIKE ? LIMIT 3", ('%"\u8349"%',)).fetchall()
    gname = grass[0]["name"] if grass else None
    print("  \u8349\u7cfb\u8f7d\u4f53:", gname)
    if gname:
        bt = battle("\u5149\u5408\u6cbb\u6108", ["\u62cd\u51fb"], gname)
        bt.me.hp = 3000
        ev = bt.step("magic", 0, "skill", 0)
        heal = [e for e in ev if "\u5149\u5408\u6cbb\u6108" in e]
        print("  ", heal)
        check(bool(heal), "\u5149\u5408\u6cbb\u6108\u89e6\u53d1")
        # 光合治愈回 4999 → 7999；拍击自身 heal_pct 再回 10%（947）
        # → 8946。断言：hp >= 3000 + 50%max - 1（允许拍击附加回血更多）
        _expect = 3000 + int(bt.me.max_hp * 0.5)
        check(bt.me.hp >= _expect - 1,
              "\u56de\u590d\u2265 50%% max_hp\uff083000\u2192%d\uff0c\u671f\u671b%d+\uff09"
              % (bt.me.hp, _expect))
        # 精确断言光合治愈事件里的回复量
        _hm = _re.search(r"\u56de\u590d (\d+)", heal[0]) if heal else None
        check(_hm and int(_hm.group(1)) == int(bt.me.max_hp * 0.5),
              "\u5149\u5408\u6cbb\u6108\u56de\u590d\u91cf = 50%% max_hp")
        check(bt.magic_state["a"]["used"] == 1, "\u6b21\u6570\u6263\u51cf")
        check(bt.magic_state["a"]["cd"] == 3, "CD=3")
        check(any("\u2192" in e for e in ev), "\u65bd\u653e\u540e\u7167\u5e38\u51fa\u624b")
        # 非草非草血脉 → 落空
        bt2 = battle("\u5149\u5408\u6cbb\u6108", ["\u541e\u566c"],
                     "\u97f3\u901f\u72ac", blood_a="\u706b")
        hp0 = bt2.me.hp
        ev2 = bt2.step("magic", 0, "skill", 0)
        check(any("\u843d\u7a7a" in e for e in ev2), "\u975e\u8349\u7cfb\u2192\u843d\u7a7a")
        check(bt2.magic_state["a"]["used"] == 0, "\u843d\u7a7a\u4e0d\u6263\u6b21\u6570")
        check(not any("\u5149\u5408\u6cbb\u6108\uff01" in e for e in ev2),
              "\u6ca1\u6709\u5149\u5408\u6cbb\u6108\u65bd\u653e\uff08\u53ea\u6709\u843d\u7a7a\u63d0\u793a\uff09")

    print("--- 2. \u95ea\u7130\u7206\u53d1\uff08\u706b\u7cfb \u5a01\u529b+150%\uff09---")
    SK = "\u706b\u4e91\u8f66"  # 火云排爆：音速犬非血脉 level 技
    bt = battle("\u95ea\u7130\u7206\u53d1", [SK], "\u97f3\u901f\u72ac",
                blood_a="\u706b")
    ev = bt.step("magic", 0, "skill", 0)
    burst = [e for e in ev if "\u95ea\u7130\u7206\u53d1" in e]
    print("  ", burst)
    check(bool(burst), "\u95ea\u7130\u7206\u53d1\u89e6\u53d1")
    d1 = dmg_of(ev, SK)
    check(d1 is not None, "\u540c\u56de\u5408\u51fa\u624b\uff08%s \u4f24\u5bb3 %s\uff09"
          % (SK, d1))
    bt_c = battle(None, [SK], "\u97f3\u901f\u72ac", blood_a="\u706b")
    ev_c = bt_c.step("skill", 0, "skill", 0)
    d0 = dmg_of(ev_c, SK)
    print("  \u4f24\u5bb3: burst=%s base=%s" % (d1, d0))
    if d1 and d0:
        check(abs(d1 - d0 * 2.5) <= max(3, int(d0 * 0.03)),
              "\u5a01\u529b +150%% \u2192 \u4f24\u5bb3 2.5 \u500d\uff08%d vs %d\uff09"
              % (d1, d0))
    # 非火非火血脉 → 落空
    bt2 = battle("\u95ea\u7130\u7206\u53d1", ["\u541e\u566c"],
                 "\u6c34\u84dd\u84dd", blood_a="\u6c34")
    ev2 = bt2.step("magic", 0, "skill", 0)
    check(any("\u843d\u7a7a" in e for e in ev2),
          "\u975e\u706b\u7cfb\u2192\u843d\u7a7a\u4e0d\u6263\u6b21\u6570")

    print("--- 3. \u8282\u6d41\u672f\uff08\u6c34\u7cfb \u514d\u80fd\u8017\uff09---")
    bt = battle("\u8282\u6d41\u672f", ["\u541e\u566c"], "\u6c34\u84dd\u84dd")
    e0 = bt.me.energy
    ev = bt.step("magic", 0, "skill", 0)
    thr = [e for e in ev if "\u8282\u6d41\u672f" in e]
    print("  ", thr)
    check(bool(thr), "\u8282\u6d41\u672f\u89e6\u53d1")
    _cost = int(bt.me.moves[0].get("cost") or 0)
    check(bt.me.energy == e0,
          "\u80fd\u91cf\u4e0d\u6263\uff08%d\u2192%d\uff0c\u8017\u80fd\u5e94%s\uff09"
          % (e0, bt.me.energy, _cost))
    check(any("\u2192" in e for e in ev), "\u540c\u56de\u5408\u51fa\u624b")
    bt2 = battle("\u8282\u6d41\u672f", ["\u706b\u4e91\u8f66"],
                 "\u97f3\u901f\u72ac", blood_a="\u706b")
    ev2 = bt2.step("magic", 0, "skill", 0)
    check(any("\u843d\u7a7a" in e for e in ev2), "\u975e\u6c34\u7cfb\u2192\u843d\u7a7a")

    print("--- 4. \u8840\u8109\u8def\u5f84\uff08\u975e\u672c\u7cfb+\u8840\u8109\uff09---")
    bt = battle("\u5149\u5408\u6cbb\u6108", ["\u706b\u4e91\u8f66"],
                "\u97f3\u901f\u72ac", blood_a="\u8349")
    bt.me.hp = 3000
    ev = bt.step("magic", 0, "skill", 0)
    check(any("\u5149\u5408\u6cbb\u6108" in e and "\u843d\u7a7a" not in e
              for e in ev),
          "\u8349\u8840\u8109\u7684\u975e\u8349\u7cfb\u7cbe\u7075\u53ef\u53d7\u5149\u5408\u6cbb\u6108")

    print("--- 5. CD \u4e0e\u6b21\u6570\u4e0a\u9650 ---")
    bt = battle("\u8282\u6d41\u672f", ["\u541e\u566c"], "\u6c34\u84dd\u84dd")
    bt.step("magic", 0, "skill", 0)
    bt.begin_turn()
    ev2 = bt.step("magic", 0, "skill", 0)
    check(any("\u51b7\u5374" in e for e in ev2), "CD \u671f\u4e0d\u53ef\u7528")
    for _ in range(3):
        bt.begin_turn()
    bt.step("magic", 0, "skill", 0)
    check(bt.magic_state["a"]["used"] == 2, "\u7b2c 2 \u6b21\u53ef\u7528")
    bt.begin_turn()
    ev4 = bt.step("magic", 0, "skill", 0)
    check(any("2 \u6b21" in e for e in ev4), "\u7528\u5b8c 2 \u6b21\u63d0\u793a")

    print()
    if FAILS:
        print("FAILED %d" % len(FAILS))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
