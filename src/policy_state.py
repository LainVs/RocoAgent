# -*- coding: utf-8 -*-
"""S27：policy_v2 的 Battle→state 提取器（正式模块）。

从 Battle 对象提取 policy_v2.decide 需要的完整 state——
robot 实盘接线（P4.5）时的参考实现：实盘侧把 OCR/感知字段
对齐到这里的键位即可。

用法：
    from policy_state import battle_to_state
    state = battle_to_state(bt)
    act, why, detail = policy_v2.decide(state)
    bt.step(*act, "energy", None)   # 对方动作另出
"""
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else ".")


def battle_to_state(bt):
    """Battle → policy_v2 state。

    - skills dmg 用 SIM.calc_damage 现算（等价于实盘的卡牌预览值）
    - magic/evolution 形状与 S17 手操 state 一致
    - bench 含存活与阵亡（policy 自己按 alive 过滤）
    """
    import simulator as SIM

    me, foe = bt.me, bt.foe
    skills = []
    for i, m in enumerate(me.moves):
        dmg = 0
        if (m.get("power") or 0) > 0:
            try:
                dmg = SIM.calc_damage(me, foe, m)
            except Exception:
                dmg = 0
        skills.append({"i": i, "dmg": dmg,
                       "cost": int(m.get("cost") or 0),
                       "kind": "num" if (m.get("power") or 0) > 0
                       else "self_buff", "lit": True})
    bench = []
    for side in ("a", "b"):
        team = bt.a if side == "a" else bt.b
        cur = bt.ia if side == "a" else bt.ib
        if side == "a":
            for idx, p in enumerate(team):
                if idx == cur:
                    continue
                bench.append({"name": p.name, "hp": int(p.hp),
                              "hp_max": int(p.max_hp),
                              "alive": bool(p.alive), "slot": idx + 1})
    return {
        "me": {"name": me.name, "hp": int(me.hp), "hp_max": int(me.max_hp),
               "energy": int(me.energy), "types": list(me.types or []),
               "bloodline": getattr(me, "bloodline", None),
               "skills": skills},
        "foe": {"name": foe.name, "hp": int(foe.hp),
                "hp_max": int(foe.max_hp), "types": list(foe.types or [])},
        "bench": bench,
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
