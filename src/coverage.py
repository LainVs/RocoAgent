# -*- coding: utf-8 -*-
"""打击面计算（修正版）。

规则区分（之前搞混了，导致 4 属性反而比 3 属性差这种荒谬结果）：

    攻击方多属性 = **可选**（我挑一个技能打）→ 取 max
    防守方多属性 = **同时承受**（双属性相乘）→ 取 prod

覆盖判定：
    对每个防守方（单属性或双属性组合），
    只要存在一个我方攻击属性，使 该技能的倍率 ≥ 1.0，就算"打得动"。
"""
from __future__ import annotations
import os
import paths

import itertools
import sqlite3
import sys

DB = str(paths.ROOT / "data/roco.sqlite3")


def load():
    db = sqlite3.connect(DB)
    names = {r[0]: r[1] for r in db.execute("SELECT id,name FROM types")}
    mult = {(a, d): m for a, d, m in db.execute("SELECT atk,def,mult FROM type_eff")}
    db.close()
    return names, mult


def skill_mult(mult, atk_type, def_types):
    """一个技能打一个防守方（1-2 属性）的倍率。"""
    m = 1.0
    for d in def_types:
        m *= mult.get((atk_type, d), 1.0)
    return m


def coverage(att_types, def_sets, mult):
    """att_types 这组属性，能 ≥1.0 打穿多少个防守方。"""
    ok, bad = 0, []
    for ds in def_sets:
        best = max(skill_mult(mult, t, ds) for t in att_types)
        if best >= 1.0:
            ok += 1
        else:
            bad.append(ds)
    return ok, bad


def main():
    names, mult = load()
    T = sorted(names)
    singles = [(t,) for t in T]
    duals = [(a, b) for a, b in itertools.combinations(T, 2)]

    print("== 单属性防守方（18 个）==")
    for label, pool in (("不受约束", T), ("约束（光武萌幻 + 至多1其他）", None)):
        rows = []
        if pool is None:
            FREE = [0, 6, 14, 17]      # 光武萌幻 的 id，下面按名字重建
            FREE = [k for k, v in names.items() if v in ("光", "武", "萌", "幻")]
            EXCL = [k for k, v in names.items() if v not in ("光", "武", "萌", "幻")]
            # 每个目标规模单独枚举：j 个自由属性 + 至多 1 个互斥属性
            for size in (2, 3, 4):
                for j in range(size, -1, -1):
                    if size - j > 1:
                        continue
                    for fr in itertools.combinations(FREE, j):
                        if size - j == 0:
                            rows.append(tuple(fr))
                        else:
                            for e in EXCL:
                                rows.append(tuple(fr) + (e,))
        else:
            for k in (1, 2, 3, 4):
                rows.extend(itertools.combinations(T, k))
        best = {}
        for att in rows:
            ok, bad = coverage(list(att), singles, mult)
            n = len(att)
            if n not in best or ok > best[n][0]:
                best[n] = (ok, att, bad)
        print("  [%s]" % label)
        for n in sorted(best):
            ok, att, bad = best[n]
            print("    %d 属性：%2d/18  %-18s 打不动: %s" % (
                n, ok, "+".join(names[t] for t in att),
                ",".join("+".join(names[x] for x in ds) for ds in bad) or "无"))

    # 双属性防守方（153 个）—— 更接近实战
    print("\n== 双属性防守方（153 种组合）==")
    for n in (3, 4):
        best = (0, None, None)
        for att in itertools.combinations(T, n):
            ok, bad = coverage(list(att), duals, mult)
            if ok > best[0]:
                best = (ok, att, bad)
        ok, att, bad = best
        print("  %d 属性：%3d/153  %s" % (
            n, ok, "+".join(names[t] for t in att)))
        if bad:
            print("     打不动示例: %s" % ", ".join(
                "+".join(names[t] for t in ds) for ds in bad[:5]))


if __name__ == "__main__":
    main()
