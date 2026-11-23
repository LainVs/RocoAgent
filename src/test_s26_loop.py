# -*- coding: utf-8 -*-
"""S26 测试：policy_v2 × Battle 全闭环集成。

决策层（S22）输出的 ("magic", i)/("evolve", i) 必须能被引擎（S8-S10）
真实执行——本测试用 battle_to_state 提取器驱动 policy_v2 决策，
灌回 Battle.step 验证执行。这也是 robot 实盘接线的 state 参考实现。
"""
import sys

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

import simulator as SIM  # noqa: E402
import policy_v2  # noqa: E402

FAILS = []
D = SIM.Dex()


def check(cond, msg):
    print(("  \u2713 " if cond else "  \u2717 ") + msg)
    if not cond:
        FAILS.append(msg)


def battle_to_state(bt):
    """Battle → policy_v2 state（robot 接线的参考实现）。"""
    me, foe = bt.me, bt.foe
    skills = []
    for i, m in enumerate(me.moves):
        dmg = 0
        if (m.get("power") or 0) > 0:
            try:
                dmg = SIM.calc_damage(me, foe, m)
            except Exception:
                dmg = 0
        skills.append({"i": i, "dmg": dmg, "cost": int(m.get("cost") or 0),
                       "kind": "num" if (m.get("power") or 0) > 0
                       else "self_buff", "lit": True})
    return {
        "me": {"name": me.name, "hp": int(me.hp), "hp_max": int(me.max_hp),
               "energy": int(me.energy), "types": list(me.types or []),
               "bloodline": getattr(me, "bloodline", None),
               "skills": skills},
        "foe": {"name": foe.name, "hp": int(foe.hp),
                "hp_max": int(foe.max_hp), "types": list(foe.types or [])},
        "bench": [{"name": p.name, "hp": int(p.hp), "hp_max": int(p.max_hp),
                   "alive": p.alive}
                  for p in (bt.a if True else bt.b) if p is not me],
        "magic": {"name": bt.team_magic.get("a") or "",
                  "used": bt.magic_state["a"]["used"],
                  "max_uses": 2, "cd": bt.magic_state["a"]["cd"],
                  "usable": (bool(bt.team_magic.get("a"))
                             and bt.magic_state["a"]["used"] < 2
                             and bt.magic_state["a"]["cd"] == 0)},
        "evolution": {"used": bool(bt.evolution_used.get("a")),
                      "usable": not bt.evolution_used.get("a")
                      and getattr(bt.me, "bloodline", None) == "首领"},
    }


def run_loop(bt, max_turns=12):
    """policy_v2 决策闭环：返回 (actions, all_events)。"""
    actions = []
    events_all = []
    bt.begin_turn()
    for _ in range(max_turns):
        if bt.done:
            break
        state = battle_to_state(bt)
        act, why, _ = policy_v2.decide(state)
        actions.append((act, why))
        kind = act[0]
        payload = act[1] if len(act) > 1 else None
        try:
            ev = bt.step(kind, payload, "energy", None)
        except Exception as e:
            events_all.append("EXCEPTION: %s" % e)
            break
        events_all.extend(ev)
        if bt.done:
            break
        bt.begin_turn()
    return actions, events_all


def main():
    print("--- 1. \u95ea\u7130\u7206\u53d1\u961f\uff1amagic \u51b3\u7b56\u88ab\u5f15\u64ce\u6267\u884c ---")
    bt = SIM.Battle(D, ["\u97f3\u901f\u72ac"], ["\u6c34\u84dd\u84dd"],
                    seed=0,
                    cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                            "max_hp": 9999, "bloodline": "\u706b",
                            "moves": ["\u706b\u4e91\u8f66",
                                      "\u4e91\u884c\u96e8\u65bd"]}],
                    cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                            "max_hp": 9999, "moves": ["\u62cd\u51fb"]}],
                    magic_kind_a="\u95ea\u7130\u7206\u53d1")
    actions, evs = run_loop(bt)
    magic_acts = [a for a, _ in actions if a[0] == "magic"]
    magic_ev = [e for e in evs if "\u95ea\u7130\u7206\u53d1" in e]
    print("  magic \u51b3\u7b56:", len(magic_acts), "\u6267\u884c\u4e8b\u4ef6:", magic_ev[:1])
    check(len(magic_acts) >= 1, "policy \u53d1\u51fa magic \u51b3\u7b56")
    check(len(magic_ev) >= 1, "\u5f15\u64ce\u6267\u884c\u95ea\u7130\u7206\u53d1")
    check(not any(e.startswith("EXCEPTION") for e in evs),
          "\u5168\u7a0b\u65e0\u5f02\u5e38")

    print("--- 2. \u9996\u9886\u8840\u8109\u961f\uff1aevolve \u51b3\u7b56\u88ab\u5f15\u64ce\u6267\u884c ---")
    bt2 = SIM.Battle(D, ["\u706b\u795e"], ["\u6c34\u84dd\u84dd"], seed=0,
                     cfg_a=[{"name": "\u706b\u795e", "iv": 60,
                             "max_hp": 9999, "bloodline": "\u9996\u9886",
                             "moves": ["\u706b\u4e91\u8f66"]}],
                     cfg_b=[{"name": "\u6c34\u84dd\u84dd", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    actions2, evs2 = run_loop(bt2, max_turns=3)
    evo_acts = [a for a, _ in actions2 if a[0] == "evolve"]
    evo_ev = [e for e in evs2 if "\u9996\u9886\u5316" in e]
    print("  evolve \u51b3\u7b56:", evo_acts[:1], "\u6267\u884c:", evo_ev[:1])
    check(len(evo_acts) >= 1, "policy \u53d1\u51fa evolve \u51b3\u7b56")
    check(len(evo_ev) >= 1, "\u5f15\u64ce\u6267\u884c\u9996\u9886\u5316")
    check(bt2.a[0].name == "\u70c8\u706b\u6218\u795e",
          "\u5f62\u6001\u53d8\u66f4\u751f\u6548")

    print("--- 3. \u5149\u5408\u6cbb\u6108\u4f4e\u8840\u7eed\u547d ---")
    bt3 = SIM.Battle(D, ["\u55b5\u55b5"], ["\u97f3\u901f\u72ac"], seed=0,
                     cfg_a=[{"name": "\u55b5\u55b5", "iv": 60,
                             "max_hp": 9999, "bloodline": "\u8349",
                             "moves": ["\u62cd\u51fb"]}],
                     cfg_b=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "moves": ["\u706b\u4e91\u8f66"]}],
                     magic_kind_a="\u5149\u5408\u6cbb\u6108")
    bt3.begin_turn()
    bt3.me.hp = int(bt3.me.max_hp * 0.3)   # 30% 低血
    state3 = battle_to_state(bt3)
    act3, why3, _ = policy_v2.decide(state3)
    print("  ", act3, why3[:60])
    check(act3[0] == "magic", "\u4f4e\u8840\u89e6\u53d1\u5149\u5408\u6cbb\u6108\u51b3\u7b56")

    print()
    if FAILS:
        print("FAILED %d" % len(FAILS))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
