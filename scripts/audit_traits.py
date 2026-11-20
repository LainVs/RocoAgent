# -*- coding: utf-8 -*-
"""audit_traits.py —— "特性 -> 属性"覆盖率的缺口审计（只读）

为什么要分类
------------
粗看有 187 只"没有特性"、63 只"特性是？？？"，但其中绝大多数**不是数据缺口**：

    pve_leader   首领化形态：特性被游戏主动隐藏（299002 的 desc = "看不出特性"），
                  永远读不到，只能靠头像 -------- 不是缺口
    npc           NPC/剧情（名字含"老师"、id 440xxx/8xxx）------------- 不是缺口
    placeholder   占位半成品（total<=5）------------------------------ 不是缺口
    unimplemented 未实装 -------------------------------------------- 不是缺口
    test          测试宠 -------------------------------------------- 不是缺口

只有 `normal` 且 implemented=1 的才是真缺口。

用法
    python scripts\\audit_traits.py
    python scripts\\audit_traits.py --list-gap
"""
from __future__ import annotations

import argparse
import collections
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import paths  # noqa: E402

UNKNOWN = {"？？？", "?", "", "未知"}
LEADER_FEATURE = 299002          # 游戏用来表示"看不出特性"


def classify(row, is_pve_leader):
    n = row["name"] or ""
    sid = row["id"]
    if is_pve_leader:
        # 299002（"？？？"）= PvE 专属首领化条目。
        # **PvP 里首领化后特性照常显示**，所以这些不该进 PvP 的统计口径。
        return "pve_leader"
    if "测试" in n:
        return "test"
    if "老师" in n or (440000 <= sid < 450000) or (8000 <= sid < 9000):
        return "npc"
    if (row["total"] or 0) <= 5:
        return "placeholder"
    if not row["implemented"]:
        return "unimplemented"
    if row["provisional"]:
        return "provisional"
    if "_" in n:
        return "variant"
    return "normal"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-gap", action="store_true")
    a = ap.parse_args()

    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row

    species = {int(r["id"]): r for r in db.execute(
        "SELECT id, name, types, type_names, implemented, provisional, "
        "is_leader, stage, total FROM species")}

    prim = collections.defaultdict(list)
    leaders = set()
    for r in db.execute(
            "SELECT fp.species_id, fp.is_primary, fp.feature_id, f.name "
            "FROM feature_pets fp JOIN features f ON f.id=fp.feature_id"):
        sid = int(r["species_id"])
        if int(r["feature_id"]) == LEADER_FEATURE:
            leaders.add(sid)
        if r["is_primary"]:
            prim[sid].append(r["name"])

    kind = {sid: classify(species[sid], sid in leaders) for sid in species}

    no_prim = [s for s in species if s not in prim]
    unknown_t = [s for s in species if s in prim and
                 any((t or "").strip() in UNKNOWN for t in prim[s])]
    gap = [s for s in set(no_prim) | set(unknown_t) if kind[s] == "normal"]

    print("全库 %d 只" % len(species))
    print("\n=== 全库分类 ===")
    for k, v in collections.Counter(kind.values()).most_common():
        print("   %-14s %4d" % (k, v))

    print("\n=== 看起来是缺口的两类 ===")
    for ids, title in ((no_prim, "没有 primary 特性"),
                       (unknown_t, "特性是'？？？'")):
        c = collections.Counter(kind[i] for i in ids)
        print("  %s（%d 只）：%s"
              % (title, len(ids),
                 "  ".join("%s=%d" % (k, v) for k, v in c.most_common())))

    print("\n=== 真缺口：implemented 且不是特殊形态 ===")
    print("   %d 只" % len(gap))
    if a.list_gap:
        for i in sorted(gap, key=lambda x: -(species[x]["total"] or 0)):
            r = species[i]
            print("   %7d %-16s %-10s total=%-4s feats=%s"
                  % (i, r["name"], r["type_names"], r["total"],
                     "/".join(prim.get(i, [])) or "-"))

    # 已实装物种的可靠覆盖率
    ok = amb = unk = absent = 0
    idx = collections.defaultdict(set)
    for sid, names in prim.items():
        if kind[sid] != "normal":
            continue
        pass
    db2 = sqlite3.connect(str(paths.DB))
    db2.row_factory = sqlite3.Row
    tmap = collections.defaultdict(lambda: {"ids": [], "types": set()})
    for r in db2.execute(
            "SELECT f.name t, fp.species_id sid, sp.type_names tn "
            "FROM feature_pets fp JOIN features f ON f.id=fp.feature_id "
            "JOIN species sp ON sp.id=fp.species_id WHERE fp.is_primary=1"):
        if (r["t"] or "").strip() in UNKNOWN:
            continue
        tmap[r["t"]]["ids"].append(r["sid"])
        tmap[r["t"]]["types"].add(r["tn"])
    for sid in species:
        if kind[sid] != "normal" or not species[sid]["implemented"]:
            continue
        names = [t for t in prim.get(sid, []) if (t or "").strip() not in UNKNOWN]
        if not names:
            absent += 1
            continue
        ts = set()
        for t in names:
            ts |= tmap[t]["types"]
        if len(ts) == 1:
            ok += 1
        else:
            amb += 1
    tot = ok + amb + absent
    print("\n=== 正常已实装物种的特性覆盖率 ===")
    print("   合计 %d 只：可定属性 %d（%.0f%%）/ 有歧义 %d / 无特性 %d"
          % (tot, ok, 100.0 * ok / max(1, tot), amb, absent))
    db.close()
    db2.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
