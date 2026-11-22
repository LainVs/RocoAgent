# -*- coding: utf-8 -*-
"""sim_runner.py —— 模拟器的命令行批量入口（给 ui_server 子进程调用）。

为什么单独一个文件：ui_server 用 subprocess 隔离跑模拟器，
simulator.py 的 selfplay() 打印的是给人看的文本，前端要的是 JSON。
这里负责：解析参数 -> 跑 N 局 -> 输出一份 JSON（stdout）。

用法：
    python src/sim_runner.py --team-a 音速犬,恶魔狼 --team-b 白发路路,寂灭骨龙 \
        --policy-a smart --policy-b greedy --games 20 --seed 0
"""
from __future__ import annotations

import argparse
import io
import json
import statistics
import sys

ROOT = None


def main(argv=None):
    ap = argparse.ArgumentParser(description="批量模拟对局，输出 JSON")
    ap.add_argument("--team-a", required=True, help="逗号分隔的精灵名")
    ap.add_argument("--team-b", required=True)
    ap.add_argument("--cfg-a", default="", help="JSON：[{iv,nature,moves,max_hp},...]")
    ap.add_argument("--cfg-b", default="")
    ap.add_argument("--policy-a", default="smart",
                    choices=("greedy", "smart", "buff"))
    ap.add_argument("--policy-b", default="greedy",
                    choices=("greedy", "smart", "buff"))
    ap.add_argument("--games", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    # simulator 依赖 src 在 sys.path，用本文件位置锚定
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "src"))
    global ROOT
    ROOT = root

    import simulator as SIM

    def jload(s):
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return None

    ta = [x.strip() for x in (args.team_a or "").split(",") if x.strip()]
    tb = [x.strip() for x in (args.team_b or "").split(",") if x.strip()]
    ca = jload(args.cfg_a)
    cb = jload(args.cfg_b)
    ta = [x for x in ta if SIM.Dex().species(x)]
    tb = [x for x in tb if SIM.Dex().species(x)]
    if not ta or not tb:
        print(json.dumps({"error": "队伍里有图鉴查不到的精灵",
                          "team_a": ta, "team_b": tb}, ensure_ascii=False))
        return 1

    pol_cls = {"greedy": SIM.GreedyPolicy, "smart": SIM.SmartPolicy,
               "buff": SIM.BuffPolicy}
    pa_cls, pb_cls = pol_cls.get(args.policy_a, SIM.SmartPolicy), \
        pol_cls.get(args.policy_b, SIM.GreedyPolicy)

    dex = SIM.Dex()
    wins = {"a": 0, "b": 0, "draw": 0}
    turns = []
    examples = []
    for i in range(max(1, min(args.games, 500))):
        pa, pb = pa_cls(), pb_cls()
        w, t, ha, hb = SIM.run_match(dex, ta, tb, pa, pb,
                                     seed=(args.seed or 0) + i,
                                     cfg_a=ca, cfg_b=cb)
        wins[w if w in ("a", "b") else "draw"] += 1
        turns.append(t)
        if len(examples) < 5:
            examples.append({"game": i + 1, "winner": w, "turns": t,
                             "hp_a": round(ha, 1), "hp_b": round(hb, 1)})

    out = {
        "games": len(turns),
        "wins_a": wins["a"], "wins_b": wins["b"], "draws": wins["draw"],
        "win_rate_a": round(100.0 * wins["a"] / max(1, len(turns)), 1),
        "avg_turns": round(statistics.mean(turns), 1) if turns else 0,
        "median_turns": statistics.median(turns) if turns else 0,
        "examples": examples,
        "team_a": ta, "team_b": tb,
        "policy_a": args.policy_a, "policy_b": args.policy_b,
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
