# -*- coding: utf-8 -*-
"""对比三个数据源，定出"哪个字段信谁"。

数据源
------
A. 游戏原始快照（2026-04 导出）
     pet_species.json / innate_skills.json / skill_map.json
     优点：字段全（技能池、进化、特性 desc）
     缺点：过时，且有命名错误（name 字段填了精灵名）

B. roco-cal 最新（2026-11-20，已同步 S5）
     rocodatebase/data/pets_w_skill_json/*.json（665 只）
     优点：新、S5 覆盖、字段干净（特性/属性/种族值）
     缺点：没有技能池细节

C. RokePvP-Calculator spirits.js（653 只）
     优点：有 tr_desc（特性描述）
     缺点：社区维护，可能滞后

本脚本给出三方在"特性/属性/种族值"上的一致率矩阵。
"""
from __future__ import annotations

import glob
import io
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402

CAL = r"D:\GameData\洛克王国世界\roco-cal-latest\src-tauri\resources\rocodatebase\data\pets_w_skill_json"
PVP = r"D:\GameData\洛克王国世界\RokePvP-Calculator\data\spirits.js"


def load_cal():
    out = {}
    for f in glob.glob(os.path.join(CAL, "*.json")):
        try:
            j = json.load(io.open(f, encoding="utf-8"))
        except Exception:
            continue
        n = j.get("name")
        if not n:
            continue
        out[n] = {
            "trait": (j.get("特性") or "").strip(),
            "types": [x for x in (j.get("elements") or []) if x],
            "stats": [j.get("hp"), j.get("atk"), j.get("mag"),
                      j.get("def"), j.get("res"), j.get("spd")],
            "total": j.get("种族资质"),
            "id": j.get("id"),
        }
    return out


def load_pvp():
    t = io.open(PVP, encoding="utf-8", errors="replace").read()
    arr = json.loads(t[t.index("["):t.rindex("]") + 1])
    out = {}
    for e in arr:
        n = e.get("n")
        if n:
            out[n] = {
                "trait": (e.get("tr") or "").strip(),
                "desc": (e.get("tr_desc") or "").strip(),
                "types": [x for x in (e.get("a1"), e.get("a2")) if x],
                "stats": [e.get(k) for k in ("hp", "pa", "ma", "pd", "md", "sp")],
            }
    return out


def load_ours():
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    out = {}
    for r in db.execute("SELECT name,type_names,hp,atk,spa,def,spd,spe FROM species"):
        out[r["name"]] = {
            "types": json.loads(r["type_names"] or "[]"),
            "stats": [r["hp"], r["atk"], r["spa"], r["def"], r["spd"], r["spe"]],
            "trait": "",
        }
    # 用 primary 特性；没有 primary 就取第一个
    rows = db.execute(
        "SELECT sp.name AS s, f.name AS f, fp.is_primary AS p "
        "FROM feature_pets fp "
        "JOIN species sp ON sp.id=fp.species_id "
        "JOIN features f ON f.id=fp.feature_id "
        "ORDER BY fp.is_primary DESC").fetchall()
    for r in rows:
        ent = out.get(r["s"])
        if ent is None:
            continue
        if not ent["trait"]:
            ent["trait"] = r["f"]
    db.close()
    return out


def norm_stats(s):
    try:
        return [int(x) for x in s]
    except (TypeError, ValueError):
        return None


def main():
    cal = load_cal()
    pvp = load_pvp()
    ours = load_ours()
    print("A 游戏快照(我们库) : %d" % len(ours))
    print("B roco-cal (S5)    : %d" % len(cal))
    print("C RokePvP          : %d" % len(pvp))

    abc = sorted(set(cal) & set(pvp) & set(ours))
    print("\n三方都有: %d 只" % len(abc))

    # 特性三方一致率
    st = Counter()
    for n in abc:
        a = ours[n].get("trait") or ""
        b = cal[n]["trait"]
        c = pvp[n]["trait"]
        if b and c and b == c:
            st["B=C"] += 1
            st["A=B"] += (1 if a == b else 0)
        elif b and a == b:
            st["A=B!=C"] += 1
        elif c and a == c:
            st["A=C!=B"] += 1
        elif b and c:
            st["三方都不同"] += 1
        else:
            st["信息不足"] += 1
    print("\n=== 特性 ===")
    print("  B(roco-cal) == C(RokePvP) : %d  <- 这两家一致时可信度最高"
          % st["B=C"])
    print("    其中 A(我们) 也对      : %d" % st["A=B"])
    print("    其中 A 错              : %d" % (st["B=C"] - st["A=B"]))
    print("  A==B 但 !=C              : %d" % st["A=B!=C"])
    print("  A==C 但 !=B              : %d" % st["A=C!=B"])
    print("  三方都不同               : %d" % st["三方都不同"])

    # 属性
    ty = Counter()
    for n in abc:
        a = sorted(ours[n]["types"])
        b = sorted(cal[n]["types"])
        c = sorted(pvp[n]["types"])
        if b and c and b == c:
            ty["B=C"] += 1
            ty["A=B"] += (1 if a == b else 0)
        elif a == b:
            ty["A=B!=C"] += 1
        elif a == c:
            ty["A=C!=B"] += 1
        elif b and c:
            ty["三方都不同"] += 1
    print("\n=== 属性 ===")
    print("  B==C: %d（其中 A 也对 %d，A 错 %d）"
          % (ty["B=C"], ty["A=B"], ty["B=C"] - ty["A=B"]))
    print("  A==B 但 !=C: %d" % ty["A=B!=C"])
    print("  A==C 但 !=B: %d" % ty["A=C!=B"])
    print("  三方都不同 : %d" % ty["三方都不同"])

    # 种族值
    sd = Counter()
    for n in abc:
        a = norm_stats(ours[n]["stats"])
        b = norm_stats(cal[n]["stats"])
        c = norm_stats(pvp[n]["stats"])
        if a is None or b is None or c is None:
            continue
        if b == c:
            sd["B=C"] += 1
            sd["A=B"] += (1 if a == b else 0)
        elif a == b:
            sd["A=B!=C"] += 1
        elif a == c:
            sd["A=C!=B"] += 1
        else:
            sd["三方都不同"] += 1
    print("\n=== 种族值 ===")
    print("  B==C: %d（其中 A 也对 %d，A 错 %d）"
          % (sd["B=C"], sd["A=B"], sd["B=C"] - sd["A=B"]))
    print("  A==B 但 !=C: %d" % sd["A=B!=C"])
    print("  A==C 但 !=B: %d" % sd["A=C!=B"])
    print("  三方都不同 : %d" % sd["三方都不同"])

    # 覆盖
    print("\n=== 覆盖 ===")
    print("  B(roco-cal) 有、我们没有: %d" % len(set(cal) - set(ours)))
    print("  C(RokePvP)  有、我们没有: %d" % len(set(pvp) - set(ours)))
    print("  我们独有的（多为形态变体）: %d" % len(set(ours) - set(cal) - set(pvp)))


if __name__ == "__main__":
    main()
