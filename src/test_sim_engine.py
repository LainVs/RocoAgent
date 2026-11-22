# -*- coding: utf-8 -*-
"""test_sim_engine.py - 模拟器引擎的黄金基线回归。

背景：2026-11-22 把 `run_match()` 拆成可单步的 `Battle` 类（手操对战
窗口要和批量模拟共用同一套战斗循环）。动的是核心逻辑，必须有保护。

这个游戏 PVP **没有伤害波动**（见 sim_runner.py 里的长注释：BWIKI
明确写「不论PVE还是PVP，都不存在伤害波动」，我们自己的 1871 条卡面
读数也只出现 1.5/1.333/1.25 这种精确倍率，没有连续散布）。
所以同一组 (队伍, 策略, seed) 的结果是**完全确定**的，可以逐字段比对。

基线：data/fixtures/sim_baseline.json（5 用例 × 3 seed）
重拍：& $py scripts/snap_sim_baseline.py
      数据/技能表变动（比如换赛季）之后需要重拍，重拍前先看清楚差异是不是
      换数据导致的，不要为了让测试变绿而无脑重拍。

usage: python test_sim_engine.py
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import paths  # noqa: E402,F401

import simulator as SIM  # noqa: E402

BASE = os.path.join(ROOT, "data", "fixtures", "sim_baseline.json")


def main():
    if not os.path.exists(BASE):
        print("FAILED 1: 基线文件不存在: %s" % BASE)
        print("先跑: & $py scripts/snap_sim_baseline.py")
        return 1
    want = json.load(io.open(BASE, encoding="utf-8"))["rows"]

    bad = 0
    print("--- 引擎基线（%d 行）---" % len(want))
    for r in want:
        pol = {"smart": SIM.SmartPolicy, "greedy": SIM.GreedyPolicy,
               "buff": SIM.BuffPolicy}
        dex = SIM.Dex()
        w, t, ha, hb = SIM.run_match(
            dex, r["team_a"], r["team_b"],
            pol[r["policy_a"]](), pol[r["policy_b"]](), seed=r["seed"])
        got = {"winner": w, "turns": t, "hp_a": ha, "hp_b": hb}
        diffs = [k for k in got if got[k] != r[k]]
        bad += 1 if diffs else 0
        line = "%-12s seed%-2d %s %2d 回合" % (r["case"], r["seed"], w, t)
        if diffs:
            print("%s **FAIL** %s" % (line, {
                k: (r[k], got[k]) for k in diffs}))
        else:
            print("%s OK" % line)

    print()
    # 单步接口本身也要能用：手操窗口就是靠它推进的
    print("--- Battle 单步接口 ---")
    dex = SIM.Dex()
    bt = SIM.Battle(dex, ["音速犬"], ["冰钻布鲁斯"], seed=0)
    ok = bt.begin_turn()
    ev = bt.auto_step(SIM.SmartPolicy(), SIM.GreedyPolicy())
    if not ok or not ev:
        print("Battle.begin_turn/auto_step 不可用 **FAIL**")
        bad += 1
    else:
        print("第 1 回合事件 %d 条，例如：%s" % (len(ev), ev[0]))
        print("状态 turn=%d done=%s" % (bt.turn, bt.done))

    print()
    if bad:
        print("FAILED %d" % bad)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
