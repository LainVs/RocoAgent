# -*- coding: utf-8 -*-
"""test_policy_weights.py —— 策略权重的回归测试（不需要游戏）。

为什么需要
----------
2026-11-21 用模拟器做参数搜索时发现几个**危险值**，
一旦被误改会让胜率归零：

    w_damage_dealt = 0.15  -> 0.0%   （太看重防御，不敢打）
    w_damage_taken = 0.30  -> 38.0%  （过度怕受伤）
    w_damage_taken = 0.45  -> 20.4%
    switch_cost    = 0.05  -> 38.0%  （换人太便宜，乱换）
    charge_score   = 0.12  -> 0.0%   （过度聚能）

本测试把"当前配置必须明显优于危险值"固化下来，
以后任何人改 config/policy_weights.json 都会立刻发现。

用法
----
    python scripts\\test_policy_weights.py
"""
from __future__ import annotations

import io
import json
import os
import sys
from collections import Counter

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import simulator as SIM  # noqa: E402
from tune_weights2 import ParamPolicy, load_weights  # noqa: E402

N = 150


def rate(w):
    dex = SIM.Dex()
    team = [x for x in SIM.DEFAULT_TEAM_A if dex.species(x)]
    wins = Counter()
    for i in range(N):
        r = SIM.run_match(dex, team, team, ParamPolicy(w), SIM.GreedyPolicy(),
                          seed=i)
        wins[r[0]] += 1
    return 100.0 * wins["a"] / N


def main():
    base_w = load_weights()
    base = rate(base_w)
    print("当前配置胜率: %.1f%%" % base)

    # 当前配置必须明显高于 50%（说明策略有效）
    assert base >= 52.0, \
        "当前配置胜率 %.1f%% 偏低（应 >= 52%%），检查 config/policy_weights.json" % base
    print("  [OK] 当前配置有效（>= 52%%）")

    # 危险值必须明显更差（否则说明测试本身失效）
    BAD = [
        ("w_damage_dealt", 0.15, 30.0),
        ("w_damage_taken", 0.45, 40.0),
        ("charge_score", 0.12, 30.0),
        ("switch_cost", 0.05, 45.0),
    ]
    ok = 0
    for key, val, ceiling in BAD:
        w = dict(base_w)
        w[key] = val
        r = rate(w)
        hit = r < ceiling
        ok += 1 if hit else 0
        print("  %-16s = %-5s -> %5.1f%%  期望 < %.0f%%  %s"
              % (key, val, r, ceiling, "OK" if hit else "**FAIL**"))
        assert hit, ("危险值 %s=%s 的胜率 %.1f%% 竟然 >= %.0f%%，"
                     "说明模拟器或权重链路有问题" % (key, val, r, ceiling))

    print("\nALL OK（%d 个危险值全部被正确识别）" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
