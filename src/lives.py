# -*- coding: utf-8 -*-
"""魔力结算：一次阵亡到底扣几点。

规则（用户确认）：
    - 每方 4 点魔力，精灵力竭时判定
    - 基础 1 点，所有修正**叠加**
    - 诈死(卡瓦重)：自己力竭 -1  → 最低 0
    - 付给恶魔的赎价 / 飓风：自己被击败时 +1
    - 付给恶魔的赎价：自己击杀时，对面 +1
    - 御驾亲征：棋契陛下力竭时 +4（整条命）

用法：
    cost(victim_traits, killer_traits) -> int
"""
from __future__ import annotations

BASE = 1
LIFE_TOTAL = 4

# 特性 -> 对"自己阵亡"的修正
SELF_FAINT = {
    "诈死": -1,
    "付给恶魔的赎价": +1,
    "飓风": +1,
}
# 特性 -> 对"被击杀方"的额外修正（击杀者的特性）
KILL_EXTRA = {
    "付给恶魔的赎价": +1,
}
# 力竭时直接扣死的特性：不走叠加，直接给固定值
FIXED = {
    "御驾亲征": 4,
}


def cost(victim_traits, killer_traits=()):
    """一次阵亡，阵亡方要扣几点魔力。所有修正叠加，结果不小于 0。"""
    for t in victim_traits:
        if t in FIXED:
            return FIXED[t]
    c = BASE
    for t in victim_traits:
        c += SELF_FAINT.get(t, 0)
    for t in killer_traits:
        c += KILL_EXTRA.get(t, 0)
    return max(0, c)


def describe(victim_traits, killer_traits=()):
    n = cost(victim_traits, killer_traits)
    parts = ["基础1"]
    for t in victim_traits:
        d = SELF_FAINT.get(t)
        if d:
            parts.append("%s%+d" % (t, d))
    for t in killer_traits:
        d = KILL_EXTRA.get(t)
        if d:
            parts.append("被%s击杀%+d" % (t, d))
    return "%d 点（%s）" % (n, " ".join(parts))


if __name__ == "__main__":
    cases = [
        ((), (), 1, "普通精灵阵亡"),
        (("诈死",), (), 0, "卡瓦重阵亡（少扣1 -> 0）"),
        (("付给恶魔的赎价",), (), 2, "帕尔自己阵亡"),
        ((), ("付给恶魔的赎价",), 2, "被帕尔击杀"),
        (("付给恶魔的赎价",), ("付给恶魔的赎价",), 3, "帕尔互杀（叠加）"),
        (("诈死",), ("付给恶魔的赎价",), 1, "卡瓦重被帕尔击杀"),
        (("御驾亲征",), (), 4, "棋契陛下力竭"),
    ]
    bad = 0
    for v, k, want, note in cases:
        got = cost(v, k)
        ok = got == want
        bad += 0 if ok else 1
        print("%-22s %d (want %d) %s  | %s" % (note, got, want,
                                              "OK" if ok else "FAIL",
                                              describe(v, k)))
    print("\nall ok" if bad == 0 else "\n%d FAIL" % bad)
