# -*- coding: utf-8 -*-
"""test_anticipate.py —— 对手行为预判：分层回退 + 阈值。

背景（2026-11-22）：原来只查 (我|对面) 这一对，实测 53 组里只有 1 组
达到 MIN_SAMPLES=12，anticipate 永远返回 None。现在按
对位 -> 对面 -> 全局 回退，对位层做 Dirichlet 平滑。

跑法：
    python src\\test_anticipate.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anticipate as A
import opponent_model as OM

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


def entry(n, pa, ps, pb, reliable=False, level="pair", tops=None):
    return {"n": n, "reliable": reliable, "level": level,
            "p_attack": pa, "p_switch": ps, "p_buff": pb,
            "switch_top": tops or []}


def main():
    print("== anticipate ==")

    # 1. 对位样本充足 -> 用对位层
    m = {"A|B": entry(20, 0.2, 0.1, 0.7, True, "pair"),
         "__foe__|B": entry(40, 0.9, 0.05, 0.05, True, "foe")}
    e, lv = A.lookup(m, "A", "B")
    check(lv == "pair" and e["n"] == 20, "pair wins when n>=MIN (%s)" % lv)
    r = A.advice("A", "B", 100, 100, 366, 400, model=m)
    check(r and r["kind"] == "buff", "p_buff 0.7 -> buff advice")
    check(r and r.get("level") == "pair", "advice carries level")

    # 2. 对位样本不足 -> 回退到对面层（低置信）
    m = {"A|B": entry(2, 0.9, 0.05, 0.05, False, "pair"),
         "__foe__|B": entry(40, 0.1, 0.1, 0.8, True, "foe")}
    e, lv = A.lookup(m, "A", "B")
    check(lv == "foe", "falls back to foe level (%s)" % lv)
    r = A.advice("A", "B", 100, 100, 366, 400, model=m)
    check(r and r["kind"] == "buff" and r["confidence"] == "low",
          "foe-level advice is low confidence")

    # 3. 对位只有 2 条、对面层也没有 -> weak；少于 3 条不猜
    m = {"A|B": entry(2, 0.9, 0.05, 0.05, False, "pair")}
    e, lv = A.lookup(m, "A", "B")
    check(lv == "weak", "weak level (%s)" % lv)
    check(A.advice("A", "B", 100, 100, 366, 400, model=m) is None,
          "weak (<3 samples) -> no advice")

    # 4. 对面偏换人 -> 给出换上谁
    m = {"A|B": entry(20, 0.1, 0.8, 0.1, True, "pair",
                      tops=[["C", 12], ["D", 4]])}
    r = A.advice("A", "B", 100, 100, 366, 400, model=m)
    check(r and r["kind"] == "switch" and r.get("target") == "C",
          "switch advice carries target")

    # 5. 全局层兜底
    m = {"__global__|__all__": entry(100, 0.6, 0.2, 0.2, True, "global")}
    e, lv = A.lookup(m, "X", "Y")
    check(lv == "global", "global fallback (%s)" % lv)

    # 6. 平滑：对位 1 条 + 对面层先验 -> 概率应靠近先验而不是 100%/0%
    rows = [{"me": "A", "foe": "B", "act": "attack", "match": 1}]
    for i in range(20):
        rows.append({"me": "C", "foe": "B", "act": "buff", "match": 2})
    rows.append({"me": "A", "foe": "B2", "act": "switch", "match": 3})
    model, _ = OM.build_from_actions(rows)
    pair = model.get("A|B")
    check(pair is not None and pair["n"] == 1, "pair keeps raw n=1")
    check(pair is not None and 0.05 <= pair["p_attack"] < 0.9,
          "pair smoothed toward foe prior (p_attack=%.2f)"
          % (pair["p_attack"] if pair else -1))
    check("__foe__|B" in model and "__global__|__all__" in model,
          "foe/global layers emitted")

    print()
    if FAILS:
        print("FAILED %d" % len(FAILS))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
