# -*- coding: utf-8 -*-
"""test_trait_db.py —— 特性反查层的回归测试（不需要游戏）

验收点：
  ① 特性唯一 -> 能给出属性
  ② 多物种但属性一致 -> 能给出属性（不需要知道是哪只）
  ③ 多物种且属性不同 -> types=None（绝不猜）；给了候选物种则能收窄
  ④ 未识别特性 -> unknown
  ⑤ 覆盖率与实测一致（583/1024 量级）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import trait_db


def main():
    idx = trait_db.index()
    print("特性数 %d" % len(idx))

    sole = next(t for t, e in idx.items()
                if len(e["ids"]) == 1 and len(e["types"]) == 1)
    r = trait_db.resolve(sole)
    assert r["how"] == "unique" and r["types"], r
    print("  ① unique   %-10s -> %s" % (sole, r["types"]))

    # 首领化形态的特性是游戏隐藏的（'？？？'）——此时头像给了物种就必须用
    leader = next((sid for sid in (4011, 4005, 4006, 4007) if sid), None)
    r = trait_db.resolve("？？？", portrait_id=leader)
    assert r["types"] and r["how"] == "portrait", r
    print("  ①' ？？？+头像 %-6d -> %s（否则会白白丢掉已知物种）"
          % (leader, r["types"]))

    multi_same = next(t for t, e in idx.items()
                      if len(e["ids"]) > 1 and len(e["types"]) == 1)
    r = trait_db.resolve(multi_same)
    assert r["how"] == "same_types" and r["types"], r
    print("  ② same     %-10s -> %s（%d 只候选）"
          % (multi_same, r["types"], len(r["candidates"])))

    multi_diff = next(t for t, e in idx.items()
                      if len(e["ids"]) > 1 and len(e["types"]) > 1)
    r = trait_db.resolve(multi_diff)
    assert r["how"] == "ambiguous" and r["types"] is None, r
    print("  ③ ambiguous %-9s -> None（%d 只候选 / %d 种属性）"
          % (multi_diff, len(r["candidates"]), len(idx[multi_diff]["types"])))
    r2 = trait_db.resolve(multi_diff, portrait_id=r["candidates"][0])
    assert r2["how"] == "portrait" and r2["types"], r2
    print("      + 头像 %d        -> %s" % (r["candidates"][0], r2["types"]))

    for bad in ("", None, "？？？", "不存在的特性xyz"):
        r = trait_db.resolve(bad)
        assert r["how"] == "unknown" and r["types"] is None, (bad, r)
    print("  ④ unknown  空/『？？？』/不存在 -> unknown ✓")

    rep = trait_db.report()
    ok = rep["unique"] + rep["same"]
    rate = 100.0 * ok / max(1, rep["total"])
    print("  ⑤ 覆盖率 %.1f%%（%d/%d）" % (rate, ok, rep["total"]))
    assert 40 <= rate <= 80, rate
    print("\nALL OK")


if __name__ == "__main__":
    main()
