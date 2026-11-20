# -*- coding: utf-8 -*-
"""apply_rocal_priority.py —— 存疑数据一律以 roco-cal 为准。

用户口径（2026-11-21）：存疑的 roco-cal 优先。

处理
----
logs/data_clean_report.json 里剩下的：
    stat_conflict   21 处  -> 全部用 roco-cal 覆盖
    trait_conflict   1 处  -> 用 roco-cal 覆盖（'游戏的本质6' 是 OCR 残字，
                               实际应为 '游戏的本质'，这里以 roco-cal 的写法为准）
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

REPORT = os.path.join(ROOT, "logs", "data_clean_report.json")


def pick_best_id(db, name):
    """同名多条时选真身（与 clean_gamedb_from_rocal.pick_best 同规则）。"""
    rows = db.execute("SELECT id,hp,atk,spa,def,spd,spe,implemented,total "
                      "FROM species WHERE name=?", (name,)).fetchall()
    if not rows:
        return None

    def score(r):
        vals = [r[i] for i in range(1, 7)]
        try:
            ints = [int(x) for x in vals]
        except (TypeError, ValueError):
            return (0, 0, 0)
        real = 0 if (any(v <= 0 for v in ints) or len(set(ints)) == 1) else 1
        return (real, r[7] or 0, r[8] or 0)

    return max(rows, key=score)[0]


def main():
    apply = "--apply" in sys.argv
    rep = json.load(io.open(REPORT, encoding="utf-8"))
    stats = rep.get("stat_conflict") or []
    traits = rep.get("trait_conflict") or []
    print("存疑种族值 %d，特性冲突 %d" % (len(stats), len(traits)))

    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row

    plan = []
    for r in stats:
        sid = pick_best_id(db, r["name"])
        if sid is None:
            print("  !! 找不到", r["name"])
            continue
        plan.append((sid, r["name"], r["rocal"]))

    print("\n=== 种族值（前 8）===")
    for sid, name, vals in plan[:8]:
        print("  id=%-7d %-14s -> %s" % (sid, name, vals))

    if not apply:
        print("\n（预览，加 --apply 执行）")
        db.close()
        return 0

    n = 0
    for sid, _name, v in plan:
        db.execute("UPDATE species SET hp=?,atk=?,spa=?,def=?,spd=?,spe=? "
                   "WHERE id=?", (v[0], v[1], v[2], v[3], v[4], v[5], sid))
        n += 1
    print("\n种族值已覆盖 %d 条" % n)

    # 特性冲突：以 roco-cal 为准
    nt = 0
    for r in traits:
        sid = pick_best_id(db, r["name"])
        if sid is None:
            continue
        want = (r.get("rocal") or "").strip()
        if not want:
            continue
        row = db.execute("SELECT id FROM features WHERE name=? LIMIT 1",
                         (want,)).fetchone()
        if row:
            fid = row[0]
        else:
            cur = db.execute(
                "INSERT INTO features(name,desc,effect_type) "
                "VALUES(?,?,'pet_feature')", (want, "（来自 roco-cal）"))
            fid = cur.lastrowid
        db.execute("DELETE FROM feature_pets WHERE species_id=? AND feature_id=?",
                   (sid, fid))
        db.execute("UPDATE feature_pets SET feature_id=? "
                   "WHERE species_id=? AND is_primary=1", (fid, sid))
        print("  特性 %s: %s -> %s" % (r["name"], r.get("ours"), want))
        nt += 1
    print("特性已覆盖 %d 条" % nt)

    db.commit()
    print("\n已提交")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
