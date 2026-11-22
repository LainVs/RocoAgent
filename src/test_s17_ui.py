# -*- coding: utf-8 -*-
"""S17 测试：手操 UI 接入（evolve 动作 + state 展示）。"""
import sys

sys.path.insert(0, "src")
import manual_battle as MB  # noqa: E402

FAILS = []
D = None


def check(cond, msg):
    print(("  \u2713 " if cond else "  \u2717 ") + msg)
    if not cond:
        FAILS.append(msg)


def main():
    print("--- 1. \u706b\u795e\u9996\u9886\u8840\u8109 evolve ---")
    s = MB.Session()
    r = s.new(team_a=["\u706b\u795e"],
              cfg_a=[{"name": "\u706b\u795e", "iv": 60, "max_hp": 9999,
                      "bloodline": "\u9996\u9886",
                      "moves": ["\u706b\u4e91\u8f66"]}],
              opponent_mode="fixed", team_b=["\u6c34\u84dd\u84dd"],
              cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60, "max_hp": 9999,
                      "moves": ["\u62cd\u51fb"]}],
              policy_b="greedy", skip_starter=True)
    check(not r.get("error"), "new ok: %s" % r.get("error"))
    st = s.state()
    print("  evolution:", st.get("evolution"))
    check(st["evolution"]["usable"], "evolution \u53ef\u7528")
    check(st["me"]["bloodline"] == "\u9996\u9886", "bloodline \u5c55\u793a")
    r2 = s.act({"kind": "evolve", "i": 0})
    evs = r2.get("events") or []
    tr = [e for e in evs if "\u9996\u9886\u5316" in e]
    print("  ", tr)
    check(bool(tr), "evolve \u89e6\u53d1\u9996\u9886\u5316")
    check(s.state()["me"]["name"] == "\u70c8\u706b\u6218\u795e",
          "\u5f62\u6001 = \u70c8\u706b\u6218\u795e")
    check(s.state()["evolution"]["used"], "used \u8bb0\u5f55")
    # 第二次 → 报错
    s.begin_if_needed = True
    r3 = s.act({"kind": "evolve", "i": 0})
    check(r3.get("error") and "1 \u6b21" in r3["error"],
          "\u7b2c\u4e8c\u6b21\u62a5\u9519\uff08%s\uff09" % r3.get("error"))

    print("--- 2. \u6b63\u4f4d\u5b9d\u5251 allowed_slots ---")
    s2 = MB.Session()
    r4 = s2.new(team_a=["\u97f3\u901f\u72ac"],
                cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                        "max_hp": 9999, "bloodline": "\u666e\u901a",
                        "moves": ["\u62cd\u51fb", "\u541e\u566c"]}],
                opponent_mode="fixed", team_b=["\u6c34\u84dd\u84dd"],
                cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                        "max_hp": 9999, "moves": ["\u62cd\u51fb"]}],
                policy_b="greedy", skip_starter=True)
    # 手动挂槽位锁（载体不在测试队）
    s2.bt.a[0].trait = "\u6b63\u4f4d\u5b9d\u5251"
    import nrc_fx
    nrc_fx.trait_fire(s2.bt.a[0], None, "PASSIVE")
    st2 = s2.state()
    check(st2["me"]["allowed_slots"] == [0], "allowed_slots \u5c55\u793a")

    print("--- 3. \u613f\u529b\u5f3a\u5316 \u5bf9\u9996\u9886\u8840\u8109 \u4e0d\u53ef\u7528 ---")
    st3 = s.state()
    check(st3["magic"]["usable"] is False,
          "\u9996\u9886\u8840\u8109 \u2192 magic.usable False")

    print()
    if FAILS:
        print("FAILED %d" % len(FAILS))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
