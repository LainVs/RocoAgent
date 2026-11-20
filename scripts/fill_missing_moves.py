# -*- coding: utf-8 -*-
"""fill_missing_moves.py —— 用 roco-cal 补精灵技能池。

问题（实测 2026-11-21）
----------------------
1242 只精灵里 **680 只没有任何技能记录**（55%），包括我们队伍里的
冥古龙、刨雪鹅 —— 它们上场等于废掉。

roco-cal 的 pets/*.json 里有完整技能表：
    "skills": [{"skill_name": "诡刺", "unlock_condition": "（默认）Lv.1"}, ...]

策略
----
1. 只补"库里 0 技能"的精灵，不动已有的
2. 技能名先在 skills 表里找；找不到就用 roco-cal 的技能库补一条
3. source 标 'rocal'，便于追溯

用法
----
    python scripts\\fill_missing_moves.py            # 预览
    python scripts\\fill_missing_moves.py --apply    # 执行
"""
from __future__ import annotations

import glob
import io
import json
import os
import sqlite3
import sys

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402

CAL_PETS = os.path.join(ROOT, "data", "external", "roco-cal", "pets")
CAL_SKILLS = os.path.join(ROOT, "data", "external", "roco-cal", "skills")


def load_cal_pets():
    out = {}
    for f in glob.glob(os.path.join(CAL_PETS, "*.json")):
        try:
            j = json.load(io.open(f, encoding="utf-8-sig"))
        except Exception:
            continue
        n = j.get("name")
        sk = j.get("skills") or []
        if n and sk:
            out[n] = [s.get("skill_name") for s in sk
                      if s.get("skill_name")]
    return out


def load_cal_skill_db():
    """roco-cal 的按属性技能库 -> {技能名: {power,cost,type}}"""
    out = {}
    for f in glob.glob(os.path.join(CAL_SKILLS, "*.json")):
        try:
            j = json.load(io.open(f, encoding="utf-8-sig"))
        except Exception:
            continue
        items = j if isinstance(j, list) else (j.get("skills") or [])
        for s in items:
            if not isinstance(s, dict):
                continue
            name = s.get("name") or s.get("skill_name")
            if name:
                out[name] = {
                    "power": s.get("power") or s.get("威力"),
                    "cost": s.get("cost") or s.get("energy") or s.get("耗能"),
                    "type": s.get("type") or s.get("element") or s.get("属性"),
                }
    return out


def main():
    apply = "--apply" in sys.argv
    cal_pets = load_cal_pets()
    cal_sk = load_cal_skill_db()
    print("roco-cal: %d 只有技能表，%d 个技能定义"
          % (len(cal_pets), len(cal_sk)))

    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row

    # 库里 0 技能的精灵
    empty = db.execute(
        "SELECT s.id, s.name FROM species s WHERE NOT EXISTS "
        "(SELECT 1 FROM species_skills x WHERE x.species_id=s.id)").fetchall()
    print("库里无技能: %d 只" % len(empty))

    # 现有技能名 -> id
    sk_ids = {r[0]: r[1] for r in
              db.execute("SELECT name,id FROM skills")}
    print("skills 表: %d 个" % len(sk_ids))

    plan = []
    need_new_skill = set()
    for r in empty:
        names = cal_pets.get(r["name"])
        if not names:
            continue
        plan.append((r["id"], r["name"], names))
        for n in names:
            if n not in sk_ids:
                need_new_skill.add(n)

    print("可补精灵: %d 只" % len(plan))
    print("需要新建技能: %d 个" % len(need_new_skill))

    print("\n=== 预览（前 8）===")
    for sid, name, names in plan[:8]:
        print("  %-14s id=%-8d %d 个技能: %s"
              % (name, sid, len(names), ", ".join(names[:5])))

    if not apply:
        print("\n（预览，加 --apply 执行）")
        db.close()
        return 0

    print("\n=== 执行 ===")
    n_new_sk = 0
    for n in sorted(need_new_skill):
        d = cal_sk.get(n) or {}
        try:
            cur = db.execute(
                "INSERT INTO skills(name,power,cost,type_name) "
                "VALUES(?,?,?,?)",
                (n, d.get("power"), d.get("cost"), d.get("type")))
            sk_ids[n] = cur.lastrowid
            n_new_sk += 1
        except Exception:
            pass
    print("  新建技能 %d 个" % n_new_sk)

    n_link = 0
    for sid, name, names in plan:
        for i, sn in enumerate(names):
            skid = sk_ids.get(sn)
            if not skid:
                continue
            try:
                db.execute(
                    "INSERT OR REPLACE INTO species_skills"
                    "(species_id,skill_id,level,source) VALUES(?,?,?,?)",
                    (sid, skid, 1, "rocal"))
                n_link += 1
            except Exception:
                pass
    db.commit()
    print("  写入技能关联 %d 条" % n_link)

    left = db.execute(
        "SELECT COUNT(*) FROM species s WHERE NOT EXISTS "
        "(SELECT 1 FROM species_skills x WHERE x.species_id=s.id)"
    ).fetchone()[0]
    print("  仍无技能: %d 只" % left)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
