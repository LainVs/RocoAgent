# -*- coding: utf-8 -*-
"""test_opponent_model.py —— 对手行为模型的回归测试。

覆盖的 bug（2026-11-21 实测）：
    1. 从 turns.jsonl 反推对面行为 -> 多进程交错，p_buff 虚高到 0.5+
    2. 我方名字带 #编号 -> 样本被切碎（147 组只有 1 组达门槛）
    3. 训练假人「60级水桶模板」混入 -> p_attack 被拉到 1.00

跑法：
    python src\test_opponent_model.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import opponent_model as OM

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


def test_norm_me():
    """立绘模板名归一化。"""
    print("=== 归一化 ===")
    cases = [
        ("银月狼王#2", "银月狼王"),
        ("银月狼王#7", "银月狼王"),
        ("冥古龙#17", "冥古龙"),
        ("wolf", "音速犬"),
        ("wolf_leader", "音速犬"),
        ("音速犬", "音速犬"),
        ("恶魔狼", "恶魔狼"),
        (None, None),
        ("", ""),
    ]
    for raw, want in cases:
        got = OM._norm_me(raw)
        check(got == want, "%r -> %r (want %r)" % (raw, got, want))


def test_is_dummy():
    """训练假人识别。"""
    print()
    print("=== 训练假人 ===")
    check(OM._is_dummy("60级水桶模板"), "60级水桶模板 是假人")
    check(OM._is_dummy("60级水桶模型"), "60级水桶模型 也是假人（防写错）")
    check(not OM._is_dummy("音速犬"), "音速犬 不是假人")
    check(not OM._is_dummy("白发路路"), "白发路路 不是假人")
    check(not OM._is_dummy(None), "None 不是假人")


def test_build_from_actions():
    """从带 act 标签的记录建模型。"""
    print()
    print("=== build_from_actions ===")
    rows = [
        {"match": 1, "me": "音速犬#5", "foe": "白发路路", "act": "attack"},
        {"match": 1, "me": "音速犬#5", "foe": "白发路路", "act": "attack"},
        {"match": 1, "me": "音速犬#5", "foe": "白发路路", "act": "switch",
         "switched_to": "冥古龙"},
        {"match": 2, "me": "音速犬", "foe": "白发路路", "act": "buff"},
        # 假人：应被排除
        {"match": 2, "me": "音速犬", "foe": "60级水桶模板", "act": "attack"},
        # 缺字段：应被跳过
        {"match": 2, "me": None, "foe": "白发路路", "act": "attack"},
        {"match": 2, "me": "音速犬", "foe": None, "act": "attack"},
    ]
    model, n_matches = OM.build_from_actions(rows)
    key = "音速犬|白发路路"
    check(key in model, "归一化后合并为 %r" % key)
    if key in model:
        e = model[key]
        check(e["n"] == 4, "样本数 4（实际 %d）" % e["n"])
        check(abs(e["p_attack"] - 0.5) < 1e-6,
              "p_attack 0.5（实际 %s）" % e["p_attack"])
        check(abs(e["p_switch"] - 0.25) < 1e-6,
              "p_switch 0.25（实际 %s）" % e["p_switch"])
        check(abs(e["p_buff"] - 0.25) < 1e-6,
              "p_buff 0.25（实际 %s）" % e["p_buff"])
        check(e["switch_top"] == [("冥古龙", 1)],
              "换人目标（实际 %s）" % e["switch_top"])
    check("音速犬|60级水桶模板" not in model, "假人被排除")
    check(n_matches == 2, "识别 2 场（实际 %d）" % n_matches)


def test_reliable_threshold():
    """样本量门槛。"""
    print()
    print("=== 样本量门槛 ===")
    rows = [{"match": 1, "me": "甲", "foe": "乙", "act": "attack"}
            for _ in range(OM.MIN_SAMPLES - 1)]
    model, _ = OM.build_from_actions(rows)
    check(not model["甲|乙"]["reliable"],
          "%d 条不达门槛" % (OM.MIN_SAMPLES - 1))

    rows.append({"match": 1, "me": "甲", "foe": "乙", "act": "attack"})
    model, _ = OM.build_from_actions(rows)
    check(model["甲|乙"]["reliable"],
          "%d 条达门槛" % OM.MIN_SAMPLES)


def test_real_data_quality():
    """真实数据的 p_buff 应在合理区间（不是 0.5+）。"""
    print()
    print("=== 真实数据质量 ===")
    acts = OM.load_actions()
    if not acts:
        print("  跳过（没有 opponent_actions.jsonl）")
        return
    model, _ = OM.build_from_actions(acts)
    buffs = [v["p_buff"] for v in model.values() if v["n"] >= 4]
    if not buffs:
        print("  跳过（样本不足）")
        return
    avg = sum(buffs) / len(buffs)
    # 修复前从 turns.jsonl 反推时典型值是 0.50~0.57
    check(avg < 0.45, "p_buff 均值 %.2f < 0.45（不是虚高）" % avg)
    high = [b for b in buffs if b > 0.6]
    check(len(high) <= len(buffs) * 0.2,
          "p_buff > 0.6 的组只占 %d/%d" % (len(high), len(buffs)))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    test_norm_me()
    test_is_dummy()
    test_build_from_actions()
    test_reliable_threshold()
    test_real_data_quality()
    print()
    if FAILS:
        print("FAILED %d:" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS")
