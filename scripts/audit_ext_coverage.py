# -*- coding: utf-8 -*-
"""评估：把外部 spirits.js 作为特性的"权威来源"，能修复多少、还缺多少。

外部数据（RokePvP-Calculator/data/spirits.js）：
    653 只精灵，每只有 n(名字) tr(特性) tr_desc(描述) a1/a2(属性) 六项种族值
这是社区维护、持续更新的数据，比我们 2026-04 导出的游戏快照新。

本脚本只做**评估**，不改库：
  1. 外部能覆盖我们多少只精灵
  2. 覆盖到的里面，特性/属性/种族值分别能修正多少
  3. 外部没有的，还缺什么
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402

EXT = r"D:\GameData\洛克王国世界\RokePvP-Calculator\data\spirits.js"
TYPE_ORDER = ("普通", "火", "水", "草", "电", "冰", "武", "毒", "地", "翼",
              "萌", "幻", "虫", "幽", "机械", "龙", "恶", "光")


def main():
    t = io.open(EXT, encoding="utf-8", errors="replace").read()
    arr = json.loads(t[t.index("["):t.rindex("]") + 1])
    ext = {}
    for e in arr:
        n = e.get("n")
        if not n:
            continue
        ext[n] = {
            "trait": (e.get("tr") or "").strip(),
            "desc": (e.get("tr_desc") or "").strip(),
            "types": [x for x in (e.get("a1"), e.get("a2")) if x],
            "stats": [e.get(k) for k in ("hp", "pa", "ma", "pd", "md", "sp")],
        }

    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id,name,type_names,hp,atk,spa,def,spd,spe FROM species"
    ).fetchall()
    ours = {r["name"]: r for r in rows}

    trait_now = {}
    for r in db.execute(
            "SELECT sp.name AS s, f.name AS f, fp.is_primary AS p "
            "FROM feature_pets fp JOIN species sp ON sp.id=fp.species_id "
            "JOIN features f ON f.id=fp.feature_id WHERE fp.is_primary=1"):
        trait_now[r["s"]] = r["f"]

    print("外部 %d 只 / 我们 %d 只" % (len(ext), len(ours)))
    both = sorted(set(ext) & set(ours))
    ext_only = sorted(set(ext) - set(ours))
    ours_only = sorted(set(ours) - set(ext))
    print("  两边都有 %d，仅外部有 %d，仅我们有 %d"
          % (len(both), len(ext_only), len(ours_only)))

    # 1) 特性
    t_ok = t_bad = t_missing = 0
    t_fix = []
    for n in both:
        e_tr = ext[n]["trait"]
        o_tr = trait_now.get(n)
        if not o_tr:
            t_missing += 1
            if e_tr:
                t_fix.append((n, "(无)", e_tr))
        elif e_tr and o_tr != e_tr:
            t_bad += 1
            t_fix.append((n, o_tr, e_tr))
        else:
            t_ok += 1
    print("\n=== 特性 ===")
    print("  一致 %d / 需修 %d / 我们缺失 %d" % (t_ok, t_bad, t_missing))

    # 2) 属性
    y_ok = y_bad = y_missing = 0
    y_fix = []
    for n in both:
        e_ty = ext[n]["types"]
        o_ty = json.loads(ours[n]["type_names"] or "[]")
        if not o_ty:
            y_missing += 1
            if e_ty:
                y_fix.append((n, [], e_ty))
        elif e_ty and sorted(o_ty) != sorted(e_ty):
            y_bad += 1
            y_fix.append((n, o_ty, e_ty))
        else:
            y_ok += 1
    print("\n=== 属性 ===")
    print("  一致 %d / 需修 %d / 我们缺失 %d" % (y_ok, y_bad, y_missing))
    for n, o, e in y_fix[:12]:
        print("    %-14s %s -> %s" % (n, o, e))

    # 3) 种族值
    s_ok = s_bad = 0
    s_fix = []
    for n in both:
        e_s = ext[n]["stats"]
        if any(v is None for v in e_s):
            continue
        o_s = [ours[n][k] for k in ("hp", "atk", "spa", "def", "spd", "spe")]
        try:
            o_s = [int(x) for x in o_s]
            e_s = [int(x) for x in e_s]
        except (TypeError, ValueError):
            s_bad += 1
            s_fix.append((n, o_s, e_s, "非数字"))
            continue
        # 注意外部顺序 hp/pa/ma/pd/md/sp，我们 hp/atk/spa/def/spd/spe 一致
        if o_s == e_s:
            s_ok += 1
        else:
            s_bad += 1
            s_fix.append((n, o_s, e_s, ""))
    print("\n=== 种族值 ===")
    print("  一致 %d / 不一致 %d" % (s_ok, s_bad))
    for n, o, e, tag in s_fix[:12]:
        print("    %-14s %s -> %s %s" % (n, o, e, tag))

    # 4) 外部独有
    print("\n=== 外部有、我们没有（可补）===")
    for n in ext_only[:20]:
        print("    %-16s 特性=%-12s 属性=%s"
              % (n, ext[n]["trait"], ext[n]["types"]))
    print("    ... 共 %d 只" % len(ext_only))

    db.close()


if __name__ == "__main__":
    main()
