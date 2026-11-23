# -*- coding: utf-8 -*-
"""S22 测试：决策层 magic/evolve 分支。"""
import sys

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

import policy_v2  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  \u2713 " if cond else "  \u2717 ") + msg)
    if not cond:
        FAILS.append(msg)


def _mk_state(magic=None, evo=None, fire=False, grass=False, water=False,
              leader=False, low_hp=False, energy=6, foe_types=None):
    me = {"name": "\u6d4b\u8bd5\u7075", "hp": 400, "hp_max": 400,
          "energy": energy, "types": [],
          "skills": [{"i": 1, "dmg": 120, "cost": 2, "kind": "num",
                      "lit": True},
                     {"i": 2, "dmg": 60, "cost": 1, "kind": "num",
                      "lit": True}]}
    if fire:
        me["types"] = ["\u706b"]
        me["bloodline"] = "\u706b"
    if grass:
        me["types"] = ["\u8349"]
        me["bloodline"] = "\u8349"
    if water:
        me["types"] = ["\u6c34"]
        me["bloodline"] = "\u6c34"
    if leader:
        me["bloodline"] = "\u9996\u9886"
    if low_hp:
        me["hp"] = 120
    foe = {"name": "\u5bf9\u624b", "hp": 300, "hp_max": 900,
           "types": foe_types or []}
    st = {"me": me, "foe": foe, "bench": []}
    if magic:
        st["magic"] = magic
    if evo is not None:
        st["evolution"] = evo
    return st


def main():
    print("--- 1. evolve \u53ef\u7528\u5373\u8fdb ---")
    st = _mk_state(leader=True, evo={"used": False, "usable": True})
    act, why, _ = policy_v2.decide(st)
    print("  ", act, why[:60])
    check(act[0] == "evolve", "\u8fd4\u56de evolve \u52a8\u4f5c")
    check(act[1] == 1, "payload = \u6700\u5f3a\u6280\u80fd\u69fd\u4f4d")

    print("--- 2. evolve \u5df2\u7528 \u2192 \u4e0d\u89e6\u53d1 ---")
    st = _mk_state(leader=True, evo={"used": True, "usable": False})
    act, why, _ = policy_v2.decide(st)
    check(act[0] != "evolve", "\u4e0d\u518d\u8fd4\u56de evolve")

    print("--- 3. \u95ea\u7130\u7206\u53d1\uff08\u706b\u7cfb+\u5f3a\u653b\uff09---")
    st = _mk_state(fire=True,
                   magic={"name": "\u95ea\u7130\u7206\u53d1",
                          "usable": True, "used": 0, "max_uses": 2,
                          "cd": 0})
    act, why, _ = policy_v2.decide(st)
    print("  ", act, why[:60])
    check(act[0] == "magic" and act[1] == 1, "magic + \u6700\u5f3a\u653b\u51fb\u69fd\u4f4d")

    print("--- 4. \u5149\u5408\u6cbb\u6108\uff08\u8349\u7cfb\u4f4e\u8840\uff09---")
    st = _mk_state(grass=True, low_hp=True,
                   magic={"name": "\u5149\u5408\u6cbb\u6108",
                          "usable": True, "used": 0, "max_uses": 2,
                          "cd": 0})
    act, why, _ = policy_v2.decide(st)
    print("  ", act, why[:60])
    check(act[0] == "magic", "\u4f4e\u8840\u89e6\u53d1\u5149\u5408\u6cbb\u6108")
    # 高血不触发
    st2 = _mk_state(grass=True,
                    magic={"name": "\u5149\u5408\u6cbb\u6108",
                           "usable": True, "used": 0, "max_uses": 2,
                           "cd": 0})
    act2, _, _ = policy_v2.decide(st2)
    check(act2[0] != "magic", "\u9ad8\u8840\u4e0d\u89e6\u53d1")

    print("--- 5. \u8282\u6d41\u672f\uff08\u6c34\u7cfb\u4f4e\u80fd\u91cf\uff09---")
    st = _mk_state(water=True, energy=1,
                   magic={"name": "\u8282\u6d41\u672f",
                          "usable": True, "used": 0, "max_uses": 2,
                          "cd": 0})
    act, why, _ = policy_v2.decide(st)
    print("  ", act, why[:60])
    check(act[0] == "magic", "\u4f4e\u80fd\u91cf\u89e6\u53d1\u8282\u6d41\u672f")

    print("--- 6. \u613f\u529b\u5f3a\u5316\uff08\u8840\u8109\u514b\u5236\u22652\uff09---")
    st = _mk_state(leader=False, foe_types=["\u8349"],
                   magic={"name": "\u613f\u529b\u5f3a\u5316",
                          "usable": True, "used": 0, "max_uses": 2,
                          "cd": 0})
    st["me"]["bloodline"] = "\u706b"      # 火血脉 vs 草系 → 克制 2.0
    st["foe"]["hp"] = 500                 # 500/900 = 56% > 40%
    act, why, _ = policy_v2.decide(st)
    print("  ", act, why[:60])
    check(act[0] == "magic", "\u514b\u5236\u22652 \u89e6\u53d1\u613f\u529b\u5f3a\u5316")

    print("--- 7. \u65e0\u5b57\u6bb5 \u2192 \u884c\u4e3a\u4e0d\u53d8 ---")
    st = _mk_state(fire=True)
    act, why, _ = policy_v2.decide(st)
    check(act[0] in ("skill", "energy"), "\u5411\u540e\u517c\u5bb9\uff08%s\uff09"
          % (act[0],))

    print()
    if FAILS:
        print("FAILED %d" % len(FAILS))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
