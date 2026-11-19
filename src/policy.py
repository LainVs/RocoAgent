"""policy.py - 决策层：state -> action

规则优先级（目标：打进前 50%，先保证不犯大错）
  1. 能一击必杀 → 选最省能量的必杀技能
  2. 能量不够任何技能 → 聚能
  3. 预计这回合会死，且有更优的替补 → 换人
  4. 对面开防御 → 用状态技（力量增效类）不被打断
  5. 否则 → 有效伤害最高的技能
  6. 所有技能都被抵抗 → 换人（若替补有优势）否则硬打

输出：
  {"action": "skill", "key": 2, "reason": "..."}
  {"action": "switch", "slot": 4, "reason": "..."}
  {"action": "charge", "reason": "能量不足"}
"""
import os
import paths

import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

sys.path.insert(0, str(paths.ROOT / "src"))
import damage as D


def _my_pet(state):
    return D.pokemon(state["me"]["pet"])


def _foe_pet(state):
    return D.pokemon(state["foe"]["pet"])


def evaluate_skills(state):
    """给我方每个技能算对当前对手的伤害。"""
    me = _my_pet(state)
    foe = _foe_pet(state)
    out = []
    if not me or not foe:
        return out
    for s in state["me"].get("skills", []):
        r = D.estimate(s["name"], state["me"]["pet"], state["foe"]["pet"],
                       atk_buff_layers=len(state["me"].get("buffs", [])))
        if r:
            r["key"] = s["key"]
            r["cost"] = s.get("cost", 0)
            out.append(r)
    return out


def decide(state):
    me = state["me"]
    foe = state["foe"]
    hp = me.get("hp_abs") or 0
    energy = me.get("energy", 0)
    foe_hp = foe.get("hp_abs") or 0

    skills = evaluate_skills(state)

    # 1. 能秒
    killers = [s for s in skills if foe_hp and s["dmg"] >= foe_hp and s["cost"] <= energy]
    if killers:
        best = min(killers, key=lambda s: s["cost"])
        return {"action": "skill", "key": best["key"],
                "reason": "必杀：%s 伤害 %d ≥ 对面 %d" % (best["skill"], best["dmg"], foe_hp)}

    usable = [s for s in skills if s["cost"] <= energy]

    # 2. 能量不够
    if not usable:
        return {"action": "charge", "reason": "能量 %d 不足任何技能" % energy}

    # 3. 会被秒 → 换人
    incoming = state.get("incoming_damage")
    if incoming and hp and incoming >= hp and state.get("bench_me"):
        return {"action": "switch", "slot": state["bench_me"][0],
                "reason": "预计承受 %d ≥ 我方 %d，换人挡刀" % (incoming, hp)}

    # 4. 对面开防御 → 用状态技
    if foe.get("guarding"):
        status = [s for s in usable if s["base"] == 0]
        if status:
            return {"action": "skill", "key": status[0]["key"],
                    "reason": "对面防御中，用状态技不被中断"}

    # 5. 有效伤害最高
    dmg_skills = [s for s in usable if s["base"] > 0]
    if dmg_skills:
        best = max(dmg_skills, key=lambda s: s["dmg"])
        return {"action": "skill", "key": best["key"],
                "reason": "最高伤害 %s %d（%s%.0f%%）" % (
                    best["skill"], best["dmg"], "克制" if best["strong"] else ("抵抗" if best["resisted"] else "普通"),
                    best["type_mult"] * 100)}

    # 6. 只剩状态技
    return {"action": "skill", "key": usable[0]["key"], "reason": "无攻击技，用状态技"}


if __name__ == "__main__":
    # 自测：构造一个局面
    sample = {
        "me": {"pet": "冰钻布鲁斯", "hp_abs": 345, "energy": 10, "buffs": [],
               "skills": [
                   {"key": 1, "name": "倾泻"},
                   {"key": 2, "name": "先发制人"},
                   {"key": 3, "name": "丢冰块"},
                   {"key": 4, "name": "力量增效"},
               ]},
        "foe": {"pet": "月牙雪熊", "hp_abs": 300},
        "bench_me": [5, 4, 3],
    }
    print(json.dumps(decide(sample), ensure_ascii=False, indent=2))
