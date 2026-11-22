# -*- coding: utf-8 -*-
"""给模拟器引擎拍「黄金基线」：重构前先锁住行为。

为什么先拍基线（2026-11-22）
---------------------------
手操对战窗口必须和批量模拟**用同一套战斗循环**，否则同一个局面
手操和批量会给出不同结果，那这个窗口就没有参考价值。
所以要把 `run_match()` 拆成可复用的 `Battle` 类 —— 这是动核心逻辑，
必须有回归保护。

这个游戏 PVP 没有伤害波动（见 sim_runner 注释），所以同一个
(队伍, 策略, seed) 的结果是**完全确定**的，可以逐字段比对。

用法：
    & $py scripts/snap_sim_baseline.py            # 重新生成基线
    & $py src/test_sim_engine.py                  # 比对
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import lineup_lib as LL  # noqa: E402
import simulator as SIM  # noqa: E402

OUT = os.path.join(ROOT, "data", "fixtures", "sim_baseline.json")

# 固定用例：不用随机抽阵容，保证任何人都能复现
CASES = [
    ("mirror", "音速犬,恶魔狼,卡瓦重", "音速犬,恶魔狼,卡瓦重",
     "smart", "smart"),
    ("fire_vs_ice", "音速犬", "冰钻布鲁斯", "smart", "greedy"),
    ("big_teams", "音速犬,恶魔狼,卡瓦重,银月狼王,冰钻布鲁斯,火神",
     "白发路路,寂灭骨龙,烈火战神,水灵,卡瓦重,炽心勇狮",
     "smart", "greedy"),
    ("greedy_pair", "音速犬,恶魔狼", "卡瓦重,火神", "greedy", "greedy"),
    ("buff_policy", "卡瓦重", "恶魔狼", "buff", "smart"),
]
SEEDS = [0, 1, 2]
POL = {"smart": SIM.SmartPolicy, "greedy": SIM.GreedyPolicy,
       "buff": SIM.BuffPolicy}


def run(cases=None):
    dex = SIM.Dex()
    out = []
    for name, ta, tb, pa, pb in (cases or CASES):
        ta_l = [x for x in ta.split(",") if x]
        tb_l = [x for x in tb.split(",") if x]
        for seed in SEEDS:
            w, t, ha, hb = SIM.run_match(
                dex, ta_l, tb_l, POL[pa](), POL[pb](), seed=seed)
            out.append({"case": name, "team_a": ta_l, "team_b": tb_l,
                        "policy_a": pa, "policy_b": pb, "seed": seed,
                        "winner": w, "turns": t,
                        "hp_a": ha, "hp_b": hb})
    return out


def main():
    rows = run()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    payload = {
        "note": "模拟器引擎黄金基线。数据/技能表变动后需要重拍，"
                "用 scripts/snap_sim_baseline.py",
        "rows": rows,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print("写出 %s：%d 行（%d 用例 x %d seed）"
          % (OUT, len(rows), len(CASES), len(SEEDS)))
    for r in rows[:4]:
        print("  %-12s seed%d -> %s %d 回合"
              % (r["case"], r["seed"], r["winner"], r["turns"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
