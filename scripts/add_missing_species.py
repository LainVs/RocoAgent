# -*- coding: utf-8 -*-
"""add_missing_species.py —— 把 roco-cal 有、我们没有的精灵补进库。

策略（用户口径：缺失的该补）
------------------------------
纯新精灵（不含括号，50 只）    -> 直接插入 species + feature_pets
形态变体（含括号，168 只）      -> 插入时在 note 记 form_of 指向基础形态，
                                  不覆盖基础形态的数据

id 分配
-------
roco-cal 的 "id" 是图鉴编号（"001"），不是唯一键（同编号多形态）。
我们库的 id 是 INTEGER PRIMARY KEY。新精灵用 900000 + 序号，避开现有段。

用法
----
    python scripts\\add_missing_species.py            # 预览
    python scripts\\add_missing_species.py --apply    # 执行
"""
from __future__ import annotations

import glob
import io
import json
import os
import re
import sqlite3
import sys

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402

CAL_PETS = os.path.join(ROOT, "data", "external", "roco-cal", "pets")
ID_BASE = 2000000000   # 避开现有 9 位 id（最大 990000115）


def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def load_cal():
    out = {}
    for f in glob.glob(os.path.join(CAL_PETS, "*.json")):
        try:
            j = json.load(io.open(f, encoding="utf-8-sig"))
        except Exception:
            continue
        n = j.get("name")
        if n:
            out[n] = j
    return out


def main():
    apply = "--apply" in sys.argv
    cal = load_cal()
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row

    have = {r[0] for r in db.execute("SELECT name FROM species")}
    miss = sorted(set(cal) - have)

    def is_form(n):
        return bool(re.search(r"[（(]", n))

    pure = [n for n in miss if not is_form(n)]
    forms = [n for n in miss if is_form(n)]

    print("缺失总计 %d：纯新 %d，形态 %d" % (len(miss), len(pure), len(forms)))

    # 已有特性名（避免重复插入）
    feats = {r[0]: r[1] for r in db.execute("SELECT name,id FROM features")}
    max_id = db.execute("SELECT MAX(id) FROM species").fetchone()[0] or 0
    # 现有 id 最大到 9 位数（998553646 这类），新 id 从 max+1 起，避开所有现有段
    next_id = max(ID_BASE, max_id + 1)

    plan = []
    for n in miss:
        j = cal[n]
        sid = next_id
        next_id += 1
        plan.append({
            "id": sid, "name": n,
            "trait": (j.get("特性") or "").strip(),
            "types": [x for x in (j.get("elements") or []) if x],
            "stats": [num(j.get("hp")), num(j.get("atk")), num(j.get("mag")),
                      num(j.get("def")), num(j.get("res")), num(j.get("spd"))],
            "total": num(j.get("种族资质")),
            "form": is_form(n),
        })

    print("\n=== 预览（前 8）===")
    for p in plan[:8]:
        print("  id=%-7d %-18s 特性=%-12s 属性=%-14s %s"
              % (p["id"], p["name"], p["trait"], "/".join(p["types"]),
                 "[形态]" if p["form"] else ""))

    if not apply:
        print("\n（预览模式，加 --apply 执行）")
        db.close()
        return 0

    print("\n=== 执行 ===")
    n_sp = n_feat = n_link = 0
    for p in plan:
        db.execute(
            "INSERT OR REPLACE INTO species("
            "id,name,types,type_names,hp,atk,spa,def,spd,spe,total,"
            "implemented,provisional) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,0)",
            (p["id"], p["name"],
             json.dumps([0] * len(p["types"])),
             json.dumps(p["types"], ensure_ascii=False),
             p["stats"][0], p["stats"][1], p["stats"][2],
             p["stats"][3], p["stats"][4], p["stats"][5], p["total"]))
        n_sp += 1

        if p["trait"]:
            fid = feats.get(p["trait"])
            if fid is None:
                cur = db.execute(
                    "INSERT INTO features(name,desc,effect_type) "
                    "VALUES(?,?,'pet_feature')",
                    (p["trait"], "（来自 roco-cal）"))
                fid = cur.lastrowid
                feats[p["trait"]] = fid
                n_feat += 1
            db.execute("INSERT OR REPLACE INTO feature_pets"
                       "(species_id,feature_id,is_primary) VALUES(?,?,1)",
                       (p["id"], fid))
            n_link += 1

    db.commit()
    print("  插入精灵 %d 只" % n_sp)
    print("  新增特性 %d 个" % n_feat)
    print("  特性关联 %d 条" % n_link)

    total = db.execute("SELECT COUNT(*) FROM species").fetchone()[0]
    print("  库内精灵总数: %d" % total)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
