# -*- coding: utf-8 -*-
"""clean_gamedb_from_rocal.py —— 用 roco-cal 的 S5 数据清洗我们库。

优先级（用户口径）
------------------
    roco-cal(S5) > RokePvP > 游戏快照

处理策略
--------
特性  : roco-cal 有 -> 覆盖（我们错 109 处）
属性  : roco-cal 有 -> 覆盖（我们错 23 处）
种族值: 分三类
    a) 占位值（全 0 / 全 5 / 全 100 / 非数字） -> 直接覆盖
    b) 我们与 roco-cal 不同，且 RokePvP 支持 roco-cal -> 覆盖
    c) 其余差异 -> 只记录到 logs/stats_conflicts.json，不改

缺失精灵: 只记录，不在本脚本插入（形态变体需单独判断）

用法
----
    python scripts\\clean_gamedb_from_rocal.py            # 预览
    python scripts\\clean_gamedb_from_rocal.py --apply    # 执行
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
CAL_TRAITS = os.path.join(ROOT, "data", "external", "roco-cal", "traits")
PVP_JS = r"D:\GameData\洛克王国世界\RokePvP-Calculator\data\spirits.js"
LOGS = os.path.join(ROOT, "logs")


# ---------------------------------------------------------------- 数据加载

def load_rocal():
    """{name: {trait, types, stats, total, id}}"""
    out = {}
    for f in glob.glob(os.path.join(CAL_PETS, "*.json")):
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
    if not os.path.exists(PVP_JS):
        return {}
    t = io.open(PVP_JS, encoding="utf-8", errors="replace").read()
    arr = json.loads(t[t.index("["):t.rindex("]") + 1])
    out = {}
    for e in arr:
        n = e.get("n")
        if n:
            out[n] = {
                "trait": (e.get("tr") or "").strip(),
                "types": [x for x in (e.get("a1"), e.get("a2")) if x],
                "stats": [e.get(k) for k in ("hp", "pa", "ma", "pd", "md", "sp")],
            }
    return out


def load_trait_ids():
    """roco-cal 特性名 -> 是否有机器可读定义（用于统计）"""
    names = set()
    for root, _d, files in os.walk(CAL_TRAITS):
        for f in files:
            if f.endswith(".json") and f not in ("weather.json", "marks.json"):
                names.add(f[:-5])
    return names


def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def is_placeholder(stats):
    """全 0 / 全 5 / 全 100 / 含非数字 -> 视为占位"""
    vals = [num(x) for x in stats]
    if any(v is None or v <= 0 for v in vals):
        return True
    if len(set(vals)) == 1:
        return True
    return False


def pick_best(rows):
    """同名多条记录时，选"最像真身"的那条。

    实测同名会有 2-3 条（不同赛季/形态/占位）：
        圣光迪莫  id=3048(最好伙伴) / 5025(伙伴的力量) / 103005(裁决)
        千棘海针  id=4020(hp=0 占位) / 5012(hp=118) / 5029(hp=118)

    评分优先级：
      1. 种族值不是占位（hp>0 且不全同）
      2. implemented=1
      3. total 更大（数据更全）
    """
    if not rows:
        return None
    def score(r):
        st = [r["hp"], r["atk"], r["spa"], r["def"], r["spd"], r["spe"]]
        vals = [num(x) for x in st]
        real = 0 if (any(v is None or v <= 0 for v in vals)
                     or len(set(vals)) == 1) else 1
        return (real, r["implemented"] or 0, r["total"] or 0)
    return max(rows, key=score)


# ---------------------------------------------------------------- 主流程

def main():
    apply = "--apply" in sys.argv
    cal = load_rocal()
    pvp = load_pvp()
    trait_defs = load_trait_ids()
    print("roco-cal: %d 只（其中 %d 个特性有机器可读定义）"
          % (len(cal), len(trait_defs)))
    print("RokePvP : %d 只" % len(pvp))

    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row

    # 当前状态：同名多条时按 pick_best 选真身
    grouped = {}
    for r in db.execute("SELECT id,name,type_names,hp,atk,spa,def,spd,spe,"
                        "implemented,total FROM species"):
        grouped.setdefault(r["name"], []).append(dict(r))
    ours = {}
    for name, rows in grouped.items():
        r = pick_best(rows)
        ours[name] = {
            "id": r["id"],
            "types": json.loads(r["type_names"] or "[]"),
            "stats": [r["hp"], r["atk"], r["spa"], r["def"], r["spd"], r["spe"]],
            "dup": len(rows),
        }
    # 当前特性：只取"真身"那一条（pick_best 选出的 id）
    real_ids = {v["id"]: k for k, v in ours.items()}
    cur_trait = {}
    for r in db.execute(
            "SELECT sp.id AS sid, f.name AS f, fp.is_primary AS p "
            "FROM feature_pets fp JOIN species sp ON sp.id=fp.species_id "
            "JOIN features f ON f.id=fp.feature_id "
            "ORDER BY fp.is_primary DESC"):
        if r["sid"] not in real_ids:
            continue
        n = real_ids[r["sid"]]
        cur_trait.setdefault(n, r["f"])

    both = sorted(set(cal) & set(ours))
    print("\n可比精灵: %d" % len(both))

    # ---------- 1. 特性
    trait_fix, trait_conflict = [], []
    for n in both:
        want = cal[n]["trait"]
        have = cur_trait.get(n) or ""
        if not want or want == have:
            continue
        pv = (pvp.get(n) or {}).get("trait") or ""
        if pv and pv != want:
            trait_conflict.append((n, have, want, pv))
        else:
            trait_fix.append((n, have, want))
    print("\n=== 特性 ===")
    print("  可修: %d   三方冲突: %d" % (len(trait_fix), len(trait_conflict)))

    # ---------- 2. 属性
    type_fix = []
    for n in both:
        want = sorted(cal[n]["types"])
        have = sorted(ours[n]["types"])
        if want and want != have:
            type_fix.append((n, have, want))
    print("=== 属性 ===\n  可修: %d" % len(type_fix))

    # ---------- 3. 种族值
    stat_placeholder, stat_confirmed, stat_conflict = [], [], []
    for n in both:
        want = [num(x) for x in cal[n]["stats"]]
        if any(v is None or v <= 0 for v in want):
            continue
        have = [num(x) for x in ours[n]["stats"]]
        if have == want:
            continue
        if is_placeholder(ours[n]["stats"]):
            stat_placeholder.append((n, ours[n]["stats"], want))
            continue
        pv = [num(x) for x in (pvp.get(n) or {}).get("stats") or []]
        if pv and pv == want:
            stat_confirmed.append((n, have, want))
        else:
            stat_conflict.append((n, have, want, pv))
    print("=== 种族值 ===")
    print("  占位值可覆盖: %d" % len(stat_placeholder))
    print("  三方支持可覆盖: %d" % len(stat_confirmed))
    print("  存疑（只记录）: %d" % len(stat_conflict))

    # ---------- 写报告
    os.makedirs(LOGS, exist_ok=True)
    rep = {
        "generated": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "trait_fix": [{"name": n, "old": o, "new": w} for n, o, w in trait_fix],
        "trait_conflict": [{"name": n, "ours": o, "rocal": w, "pvp": p}
                           for n, o, w, p in trait_conflict],
        "type_fix": [{"name": n, "old": o, "new": w} for n, o, w in type_fix],
        "stat_placeholder": [{"name": n, "old": o, "new": w}
                             for n, o, w in stat_placeholder],
        "stat_confirmed": [{"name": n, "old": o, "new": w}
                           for n, o, w in stat_confirmed],
        "stat_conflict": [{"name": n, "ours": o, "rocal": w, "pvp": p}
                          for n, o, w, p in stat_conflict],
    }
    p = os.path.join(LOGS, "data_clean_report.json")
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        json.dumps(rep, ensure_ascii=False, indent=1))
    print("\n报告 -> %s" % p)

    if not apply:
        print("\n（预览模式，加 --apply 执行）")
        # 抽样展示
        print("\n特性修正样例:")
        for n, o, w in trait_fix[:8]:
            print("  %-14s %-14s -> %-14s" % (n, o or "(无)", w))
        print("\n属性修正样例:")
        for n, o, w in type_fix[:6]:
            print("  %-14s %s -> %s" % (n, o, w))
        print("\n种族值占位覆盖样例:")
        for n, o, w in stat_placeholder[:6]:
            print("  %-14s %s -> %s" % (n, o, w))
        db.close()
        return 0

    # ---------- 执行
    print("\n=== 执行 ===")
    # 特性：先删掉旧的 primary 关联，再插入新的
    # （feature_pets 主键是 (species_id, feature_id)，直接 UPDATE 会撞键）
    n_trait = 0
    for n, old, new in trait_fix:
        sid = ours[n]["id"]
        # 找新特性在 features 里的 id；没有就插入
        row = db.execute("SELECT id FROM features WHERE name=? LIMIT 1",
                         (new,)).fetchone()
        if row:
            fid = row[0]
        else:
            tf = os.path.join(CAL_TRAITS, new + ".json")
            desc = ""
            if os.path.exists(tf):
                try:
                    j = json.load(io.open(tf, encoding="utf-8"))
                    desc = ((j.get("特性") or {}).get("效果") or "")
                except Exception:
                    pass
            cur = db.execute(
                "INSERT INTO features(name,desc,effect_type) "
                "VALUES(?,?,'pet_feature')", (new, desc))
            fid = cur.lastrowid
        # 如果新特性已经关联过这只（非 primary），先删掉那条避免撞键
        db.execute("DELETE FROM feature_pets WHERE species_id=? AND feature_id=?",
                   (sid, fid))
        db.execute("UPDATE feature_pets SET feature_id=? "
                   "WHERE species_id=? AND is_primary=1", (fid, sid))
        n_trait += 1
    print("  特性修正 %d 条" % n_trait)

    # 属性
    n_type = 0
    for n, _old, new in type_fix:
        sid = ours[n]["id"]
        db.execute("UPDATE species SET type_names=?, types=? WHERE id=?",
                   (json.dumps(new, ensure_ascii=False),
                    json.dumps([0] * len(new)), sid))
        n_type += 1
    print("  属性修正 %d 条" % n_type)

    # 种族值
    n_stat = 0
    for n, _old, new in stat_placeholder + stat_confirmed:
        sid = ours[n]["id"]
        db.execute("UPDATE species SET hp=?,atk=?,spa=?,def=?,spd=?,spe=? "
                   "WHERE id=?", (new[0], new[1], new[2], new[3], new[4],
                                  new[5], sid))
        n_stat += 1
    print("  种族值修正 %d 条" % n_stat)

    db.commit()
    print("\n已提交")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
