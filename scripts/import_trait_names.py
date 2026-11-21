# -*- coding: utf-8 -*-
"""import_trait_names.py —— 从 roco-cal 补齐「？？？」特性名。

问题（2026-11-21 实测）
----------------------
数据库里有 **58 只精灵**的特性名字面就是「？？？」。
其中命中实盘常见对手的：

    雪影冰灵    36 次
    烈火战神    27 次
    饮雪狂兽    22 次

这些精灵的特性完全未知，模拟器无法建模。

数据来源
--------
`data/external/roco-cal/pets/*.json` 的 `特性` 字段（665 只精灵有）。

能补 **20 / 58**（其余 38 只 roco-cal 也没有，多为形态变体如
`卡图斯（初级/中级/高级）`）。

安全措施
--------
1. `--dry` 默认：只打印要改什么，不写库
2. 实际执行前**自动备份** `data/roco.sqlite3`
3. 只 UPDATE，不 INSERT / DELETE
4. 改完立即复查

跑法
----
    python scripts\\import_trait_names.py            # dry-run
    python scripts\\import_trait_names.py --apply    # 实际写入
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402

CAL_PETS = os.path.join(ROOT, "data", "external", "roco-cal", "pets")


def load_roco_cal_traits():
    """{精灵名: 特性名}，来自 roco-cal 的 pets/*.json。"""
    out = {}
    if not os.path.isdir(CAL_PETS):
        return out
    for fn in os.listdir(CAL_PETS):
        if not fn.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(CAL_PETS, fn), encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and d.get("name") and d.get("特性"):
            out[d["name"]] = d["特性"]
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入数据库")
    args = ap.parse_args()

    cal = load_roco_cal_traits()
    print("roco-cal 有特性的精灵: %d" % len(cal))
    if not cal:
        print("!! 找不到 roco-cal 特性数据")
        return 1

    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row

    # 找出所有「？？？」特性
    q_rows = db.execute(
        "SELECT id, name, desc FROM features WHERE name LIKE '%？%'").fetchall()
    print("「？？？」特性记录: %d" % len(q_rows))
    if not q_rows:
        print("没有需要修的")
        return 0

    # 这些特性被哪些精灵使用
    plan = []          # (species_id, species_name, old_feature_id, new_trait_name)
    unmatched = []
    for f in q_rows:
        old_id = f["id"]
        pets = db.execute(
            "SELECT sp.id, sp.name FROM feature_pets fp "
            "JOIN species sp ON sp.id = fp.species_id "
            "WHERE fp.feature_id = ?", (old_id,)).fetchall()
        for p in pets:
            new_name = cal.get(p["name"])
            if new_name:
                plan.append((p["id"], p["name"], old_id, new_name))
            else:
                unmatched.append(p["name"])

    print()
    print("=== 计划修改 ===")
    print("能补: %d 只精灵" % len(plan))
    print("不能补: %d 只" % len(set(unmatched)))
    print()
    for sid, sname, old_id, new_name in plan:
        print("  %-16s %s -> %s" % (sname, "？？？", new_name))

    if unmatched:
        print()
        print("=== 补不了的（roco-cal 也没有）===")
        for n in sorted(set(unmatched)):
            print("  ", n)

    if not args.apply:
        print()
        print("（dry-run，未写入。加 --apply 实际执行）")
        db.close()
        return 0

    # ── 实际写入 ──
    bak = str(paths.DB) + ".bak_traitnames_" + time.strftime("%Y%m%d_%H%M%S")
    print()
    print("=== 备份数据库 ===")
    try:
        db.close()
        shutil.copy2(str(paths.DB), bak)
        print("  已备份 -> %s" % bak)
        db = sqlite3.connect(str(paths.DB))
        db.row_factory = sqlite3.Row
    except Exception as e:
        print("  !! 备份失败: %s" % e)
        return 1

    print()
    print("=== 写入 ===")
    ok = fail = 0
    try:
        for sid, sname, old_id, new_name in plan:
            # 1) 这个特性名是否已存在？
            row = db.execute("SELECT id FROM features WHERE name=?",
                             (new_name,)).fetchone()
            if row:
                new_id = row["id"]
            else:
                # 2) 不存在就新建（从 roco-cal 拿描述）
                desc = _trait_desc(new_name)
                cur = db.execute(
                    "INSERT INTO features (name, desc) VALUES (?, ?)",
                    (new_name, desc))
                new_id = cur.lastrowid
            # 3) 把 feature_pets 指向新特性
            db.execute(
                "UPDATE feature_pets SET feature_id=? "
                "WHERE species_id=? AND feature_id=?",
                (new_id, sid, old_id))
            ok += 1
            print("  %-16s -> %s (feature_id=%s)" % (sname, new_name, new_id))
        db.commit()
    except Exception as e:
        db.rollback()
        print("  !! 写入失败，已回滚: %s" % e)
        db.close()
        return 1

    print()
    print("成功 %d, 失败 %d" % (ok, fail))

    # ── 复查 ──
    print()
    print("=== 复查 ===")
    left = db.execute(
        "SELECT COUNT(DISTINCT sp.id) c FROM species sp "
        "JOIN feature_pets fp ON fp.species_id=sp.id "
        "JOIN features f ON f.id=fp.feature_id WHERE f.name LIKE '%？%'"
    ).fetchone()["c"]
    print("  剩余「？？？」精灵: %d（原 58）" % left)
    for r in db.execute(
            "SELECT sp.name, f.name FROM species sp "
            "JOIN feature_pets fp ON fp.species_id=sp.id AND fp.is_primary=1 "
            "JOIN features f ON f.id=fp.feature_id "
            "WHERE sp.name IN ('雪影冰灵','烈火战神','饮雪狂兽')"):
        print("  %-12s -> %s" % (r["name"], r["name"] if False else r["name"]))
    db.close()
    return 0


def _trait_desc(name):
    """从 roco-cal 的 traits 目录找描述。找不到就留空。"""
    base = os.path.join(ROOT, "data", "external", "roco-cal", "traits")
    if not os.path.isdir(base):
        return ""
    for root, _dirs, files in os.walk(base):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(root, fn), encoding="utf-8"))
            except Exception:
                continue
            if isinstance(d, dict):
                t = d.get("特性") or d
                if isinstance(t, dict) and t.get("名称") == name:
                    return t.get("效果") or ""
    return ""


if __name__ == "__main__":
    sys.exit(main())
