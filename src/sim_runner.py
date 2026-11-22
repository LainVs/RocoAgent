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
    ap.add_argument("--team-b", default="")
    ap.add_argument("--cfg-a", default="", help="JSON：[{iv,nature,moves,max_hp},...]")
    ap.add_argument("--cfg-b", default="")
    ap.add_argument("--opponent-mode", default="fixed",
                    choices=("fixed", "lineups"),
                    help="fixed=用 team-b/cfg-b；lineups=每局随机抽一套真实阵容")
    ap.add_argument("--policy-a", default="smart",
                    choices=("greedy", "smart", "buff"))
    ap.add_argument("--policy-b", default="greedy",
                    choices=("greedy", "smart", "buff"))
    ap.add_argument("--games", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--weather", default="none",
                    help="天气 id（none/rain/sandstorm/blizzard/thunder）")
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

    dex = SIM.Dex()
    ta = [x for x in ta if dex.species(x)]
    tb = [x for x in tb if dex.species(x)]
    if not ta:
        print(json.dumps({"error": "我方队伍里有图鉴查不到的精灵",
                          "team_a": ta}, ensure_ascii=False))
        return 1

    pol_cls = {"greedy": SIM.GreedyPolicy, "smart": SIM.SmartPolicy,
               "buff": SIM.BuffPolicy}
    pa_cls, pb_cls = pol_cls.get(args.policy_a, SIM.SmartPolicy), \
        pol_cls.get(args.policy_b, SIM.GreedyPolicy)

    # 随机对手：从 158 套真实玩家阵容里抽。
    #
    # 为什么要这个（2026-11-22，S2/S3 标定）
    # -------------------------------------
    # 这个游戏的 PVP **没有伤害波动**（BWIKI 2026-05-08 明确写
    # "不论PVE还是PVP，都不存在伤害波动"；我们自己 1871 个带卡面读数的
    # 回合也印证了：读数比值只出现 1.5 / 1.333 / 1.25 这种**精确倍率**
    # （强化等级、暴击），没有 0.85~1.0 那种连续散布）。
    # 所以固定阵容 + 固定策略跑 30 局，就是同一局重复 30 次 —— 30/0 或
    # 0/30 都是正确结果，那个百分比没有概率含义。
    # 让胜率有意义只能靠**对手多样性**，也就是这个开关。
    pool = None
    if args.opponent_mode == "lineups":
        import lineup_lib as LL
        pool, _lstats = LL.load(dex)
        if not pool:
            print(json.dumps({"error": "真实阵容库为空"},
                             ensure_ascii=False))
            return 1
    elif not tb:
        print(json.dumps({"error": "固定对手模式需要 --team-b"},
                         ensure_ascii=False))
        return 1

    import random
    rnd = random.Random(args.seed or 0)
    used = {}
    wins = {"a": 0, "b": 0, "draw": 0}
    turns = []
    examples = []
    for i in range(max(1, min(args.games, 500))):
        if pool is not None:
            lu = pool[rnd.randrange(len(pool))]
            tb_i, cb_i = lu["team"], lu["cfg"]
            key = lu["title"] or "(无标题)"
            used[key] = used.get(key, 0) + 1
        else:
            tb_i, cb_i, key = tb, cb, ""
        pa, pb = pa_cls(), pb_cls()
        w, t, ha, hb = SIM.run_match(dex, ta, tb_i, pa, pb,
                                     seed=(args.seed or 0) + i,
                                     cfg_a=ca, cfg_b=cb_i,
                                     weather=args.weather)
        wins[w if w in ("a", "b") else "draw"] += 1
        turns.append(t)
        if len(examples) < 5:
            eg = {"game": i + 1, "winner": w, "turns": t,
                  "hp_a": round(ha, 1), "hp_b": round(hb, 1),
                  "team_b": tb_i}
            if key:
                eg["opponent"] = key
            examples.append(eg)

    out = {
        "games": len(turns),
        "wins_a": wins["a"], "wins_b": wins["b"], "draws": wins["draw"],
        "win_rate_a": round(100.0 * wins["a"] / max(1, len(turns)), 1),
        "avg_turns": round(statistics.mean(turns), 1) if turns else 0,
        "median_turns": statistics.median(turns) if turns else 0,
        "examples": examples,
        "team_a": ta, "team_b": tb,
        "opponent_mode": args.opponent_mode,
        "opponents": used,
        "policy_a": args.policy_a, "policy_b": args.policy_b,
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
