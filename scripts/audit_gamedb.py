# -*- coding: utf-8 -*-
"""audit_gamedb.py —— 全库数据体检（只读）。

检查项
------
1. 特性来源对比：pet_species.pet_feature vs innate_skills.feature_skill_ids
   vs 外部 RokePvP-Calculator/spirits.js
2. 属性缺失 / 占位（[] / null / 空）
3. 种族值占位（'--' / 0 / 全同模板）
4. 技能池缺失
5. 进化链 / 名字重复
6. 外部数据源覆盖率

输出：一份可核对的报告，不修改任何东西。
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402

GAME = str(paths.GAME_DATA)
EXT_SPIRITS = r"D:\GameData\洛克王国世界\RokePvP-Calculator\data\spirits.js"


def load_json(name):
    return json.load(io.open(os.path.join(GAME, name), encoding="utf-8"))


def load_ext():
    t = io.open(EXT_SPIRITS, encoding="utf-8", errors="replace").read()
    arr = json.loads(t[t.index("["):t.rindex("]") + 1])
    out = {}
    for e in arr:
        n = e.get("n")
        if n:
            out[n] = {"trait": e.get("tr") or "",
                      "desc": e.get("tr_desc") or "",
                      "types": [x for x in (e.get("a1"), e.get("a2")) if x],
                      "stats": {k: e.get(k) for k in
                                ("hp", "pa", "ma", "pd", "md", "sp")}}
    return out


def section(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main():
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row

    species = load_json("pet_species.json")
    innate = load_json("innate_skills.json")
    skill_map = load_json("skill_map.json")
    ext = load_ext()

    # ---------------- 1. 特性三源对比
    section("1. 特性来源对比")
    inn_pets = innate.get("pets") or {}
    print("pet_species.json      : %d 条" % len(species))
    print("innate_skills.pets    : %d 条" % len(inn_pets))
    print("外部 spirits.js       : %d 条" % len(ext))

    agree = 0
    disagree = []
    only_inn = 0
    for sid, sp in species.items():
        pf = sp.get("pet_feature")
        inn = inn_pets.get(sid) or {}
        ids = inn.get("feature_skill_ids") or []
        i0 = ids[0] if ids else None
        if pf is None and i0 is None:
            continue
        if pf == i0:
            agree += 1
        else:
            disagree.append((sp.get("name"), pf, i0,
                             (skill_map.get(str(pf)) or {}).get("name"),
                             (skill_map.get(str(i0)) or {}).get("name")))
    print("\npet_feature vs feature_skill_ids:")
    print("  一致: %d   不一致: %d" % (agree, len(disagree)))
    for n, pf, i0, pfn, i0n in disagree[:10]:
        print("    %-14s pet_feature=%-8s(%s)  innate=%-8s(%s)"
              % (n, pf, pfn, i0, i0n))

    # 我们库当前用的 vs 外部
    section("1b. 我们库 vs 外部 spirits.js（特性）")
    ours = defaultdict(list)
    for r in db.execute(
            "SELECT sp.name AS s, f.name AS f, fp.is_primary AS p "
            "FROM feature_pets fp JOIN species sp ON sp.id=fp.species_id "
            "JOIN features f ON f.id=fp.feature_id"):
        ours[r["s"]].append((r["f"], r["p"]))
    both = sorted(set(ext) & set(ours))
    same = [n for n in both
            if ext[n]["trait"] and ext[n]["trait"] in [x[0] for x in ours[n]]]
    print("  可比 %d 只：一致 %d，不一致 %d"
          % (len(both), len(same), len(both) - len(same)))

    # pet_feature 重建后能对上多少
    by_pf = {}
    for sid, sp in species.items():
        pf = sp.get("pet_feature")
        if pf:
            nm = (skill_map.get(str(pf)) or {}).get("name")
            if nm:
                by_pf.setdefault(sp.get("name"), []).append(nm)
    same2 = [n for n in both if n in by_pf and ext[n]["trait"] in by_pf[n]]
    print("  若改用 pet_feature：一致 %d / %d (%.1f%%)"
          % (len(same2), len(both), 100.0 * len(same2) / max(1, len(both))))
    print("  当前（feature_skill_ids）：一致 %d / %d (%.1f%%)"
          % (len(same), len(both), 100.0 * len(same) / max(1, len(both))))

    # ---------------- 2. 属性缺失
    section("2. 属性缺失 / 占位")
    bad_types = db.execute(
        "SELECT name, type_names FROM species "
        "WHERE type_names IS NULL OR type_names='' OR type_names='[]' "
        "OR type_names='null'").fetchall()
    print("  属性为空: %d 只" % len(bad_types))
    for r in bad_types[:10]:
        print("    ", r["name"])
    # 外部有、我们没有
    miss_type = [n for n in ext if n in {x["name"] for x in
                                         db.execute("SELECT name FROM species")}
                 and ext[n]["types"]
                 and not (db.execute("SELECT type_names FROM species WHERE name=?",
                                     (n,)).fetchone()["type_names"] or "[]")
                 .strip("[] ")]
    print("  外部有属性但我们为空: %d" % len(miss_type))

    # ---------------- 3. 种族值占位
    section("3. 种族值占位 / 异常")
    placeholder = db.execute(
        "SELECT name,hp,atk,def,spa,spd,spe FROM species "
        "WHERE typeof(hp)='text' OR hp IS NULL OR hp=0 "
        "OR typeof(atk)='text' OR atk IS NULL OR atk=0").fetchall()
    print("  种族值为文本/空/0: %d 只" % len(placeholder))
    for r in placeholder[:10]:
        print("    %-14s hp=%r atk=%r" % (r["name"], r["hp"], r["atk"]))
    # 全 5 的模板值
    tpl = db.execute(
        "SELECT name FROM species WHERE hp=5 AND atk=5 AND def=5 "
        "AND spa=5 AND spd=5 AND spe=5").fetchall()
    print("  全 5 占位模板: %d 只" % len(tpl))
    if tpl:
        print("    样例:", ", ".join(r["name"] for r in tpl[:8]))

    # ---------------- 4. 技能池
    section("4. 技能池")
    no_skill = db.execute(
        "SELECT name FROM species s WHERE NOT EXISTS "
        "(SELECT 1 FROM species_skills x WHERE x.species_id=s.id)").fetchall()
    print("  没有技能: %d 只" % len(no_skill))
    for r in no_skill[:10]:
        print("    ", r["name"])

    # ---------------- 5. 覆盖
    section("5. 外部数据能补多少")
    our_names = {r["name"] for r in db.execute("SELECT name FROM species")}
    ext_only = sorted(set(ext) - our_names)
    print("  外部有、我们库里没有: %d 只" % len(ext_only))
    for n in ext_only[:12]:
        print("    %-14s %s" % (n, ext[n]["trait"]))

    db.close()


if __name__ == "__main__":
    main()
