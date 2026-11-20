# -*- coding: utf-8 -*-
"""tune_weights2.py —— 第二轮参数搜索（伤害/击杀/换人权重）。

在 buff 参数已调好的基础上，继续搜：
    w_damage_dealt     打出伤害的权重
    w_ko               击杀的权重
    w_damage_taken     承受伤害的权重
    switch_cost        换人的代价
    switch_margin      换人需要的优势阈值
    charge_score       聚能的评分

方法：同队自对弈，每次只改一个参数，其他保持当前值（坐标下降）。
"""
from __future__ import annotations

import io
import json
import os
import sys
from collections import Counter

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))

import simulator as SIM  # noqa: E402

W_PATH = os.path.join(ROOT, "config", "policy_weights.json")


class ParamPolicy(SIM.Policy):
    """用权重参数化的简化策略，便于搜索。

    决策 = argmax over 候选：
        攻击: w_dmg * (dmg/foe_max) + w_ko*(能杀) - w_taken*(受伤/my_max)
        换人: -switch_cost + switch_margin*(新对位优势)
        聚能: charge_score
        叠buff: buff_bonus
    """

    name = "param"

    def __init__(self, w):
        self.w = w
        self._buffed = False
        self._switched_at = -99

    def choose(self, me, foe, bench, turn):
        w = self.w
        affordable = [i for i, mv in enumerate(me.moves)
                      if mv["cost"] <= me.energy]
        incoming = max((SIM.calc_damage(foe, me, mv) for mv in foe.moves),
                       default=0)
        best = (None, -9e9, "")

        # 攻击候选
        for i in affordable:
            d = SIM.calc_damage(me, foe, me.moves[i])
            kills = d >= foe.hp
            sc = (w["w_damage_dealt"] * d / max(1, foe.max_hp)
                  + w["w_ko"] * (1.0 if kills else 0.0)
                  - w["w_damage_taken"] * incoming / max(1, me.max_hp))
            if sc > best[1]:
                best = (("skill", i), sc, "attack")

        # 聚能
        sc = w["charge_score"] - w["w_damage_taken"] * incoming / max(1, me.max_hp)
        if sc > best[1]:
            best = (("energy", None), sc, "charge")

        # 叠 buff（前两回合优先，且血够）
        #
        # 关键结论（模拟器实测，2026-11-21）：
        #   · TunablePolicy（无条件 T1-T2 叠一次）    -> 66.7%
        #   · ParamPolicy（buff 参与打分竞争）        -> 50.7%
        # 差 16 个百分点。说明 buff **不该跟攻击比分数**：
        # 只要血够、前两回合、还没叠过，就叠 —— 因为它加的是
        # 后续每一回合的 20%，而攻击只是这一回合的一次输出。
        survives = me.hp - incoming > me.max_hp * w["buff_survive_floor"]
        if not self._buffed and survives and turn <= 2:
            # 能一击必杀时不叠（直接赢）
            best_d = max((SIM.calc_damage(me, foe, me.moves[i])
                          for i in affordable), default=0)
            if best_d < foe.hp:
                best = (("buff", None), 9e9, "buff")

        # 换人
        if bench and (turn - self._switched_at) >= 2:
            sc = -w["switch_cost"] + w["switch_margin"]
            if sc > best[1] and me.hp < me.max_hp * 0.35:
                self._switched_at = turn
                best = (("switch", 0), sc, "switch")

        # 记下"这回合真的要叠 buff"
        if best[0][0] == "buff":
            self._buffed = True

        # 注意：run_match 期望 (kind, payload)，这里 best[0] 已经是这个形状。
        return best[0]


def load_weights():
    try:
        return json.load(io.open(W_PATH, encoding="utf-8"))
    except Exception:
        return {}


def evaluate(w, n=250):
    dex = SIM.Dex()
    team = [x for x in SIM.DEFAULT_TEAM_A if dex.species(x)]
    wins = Counter()
    for i in range(n):
        r = SIM.run_match(dex, team, team, ParamPolicy(w), SIM.GreedyPolicy(),
                          seed=i)
        wins[r[0]] += 1
    return 100.0 * wins["a"] / n


def main():
    w = load_weights()
    # 补齐缺失的键
    defaults = {"w_damage_dealt": 0.25, "w_ko": 0.30, "w_damage_taken": 0.15,
                "w_type_matchup": 0.05, "w_energy": 0.03, "w_pet_count": 0.02,
                "switch_cost": 0.12, "switch_margin": 0.04,
                "buff_survive_floor": 0.25, "buff_bonus": 0.10,
                "charge_score": 0.06}
    for k, v in defaults.items():
        w.setdefault(k, v)

    base = evaluate(w)
    print("基准（当前权重）: %.1f%%" % base)
    print()

    GRID = {
        "w_damage_dealt": [0.15, 0.25, 0.40, 0.55],
        "w_ko": [0.15, 0.30, 0.50, 0.70],
        "w_damage_taken": [0.05, 0.15, 0.30, 0.45],
        "switch_cost": [0.05, 0.12, 0.25],
        "charge_score": [0.02, 0.06, 0.12],
    }

    improved = {}
    for key, vals in GRID.items():
        print("=== %s ===" % key)
        old = w[key]
        best_v, best_r = old, base
        for v in vals:
            w[key] = v
            r = evaluate(w)
            mark = ""
            if r > best_r:
                best_v, best_r = v, r
                mark = "  <- best"
            print("  %-8s -> %5.1f%%%s" % (v, r, mark))
        w[key] = best_v
        if best_v != old:
            improved[key] = (old, best_v, best_r)
        print()

    final = evaluate(w)
    print("=== 结果 ===")
    print("基准 %.1f%% -> 调优后 %.1f%%" % (base, final))
    if improved:
        print("\n建议修改：")
        for k, (o, n, r) in improved.items():
            print("  %-18s %s -> %s   (%.1f%%)" % (k, o, n, r))
        print("\n完整权重：")
        print(json.dumps({k: w[k] for k in defaults}, ensure_ascii=False,
                         indent=1))
    else:
        print("\n当前权重已是最优，无需修改")
    return 0


if __name__ == "__main__":
    sys.exit(main())
