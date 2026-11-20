# -*- coding: utf-8 -*-
"""trait_db.py —— "特性 → 物种 → 属性"的反查层

为什么需要它
------------
`ability_read.py` 能读到特性名，`foe_panel.learn_from_panel()` 却只用**名字**反查
`species_id`（`SELECT id FROM species WHERE name=?`）。而对手经常改名，
于是 species_id 常常是 None，属性只能退到"卡面颜色"去猜。

实际上特性是比名字稳定得多的身份键：`features.name -> feature_pets -> species`。

实测（1024 只物种 / 840 只有 primary 特性）

    · 特性 → 唯一物种                  583 只可以只靠特性确定属性
    · 特性 → 多物种但属性全同            （同上，算确定）
    · 特性 → 多物种且属性不同            194 只，集中在 35 个特性
    · 特性未识别（'？？？'）              63 只

所以这一层的职责是：**能确定就给出属性，确定不了就明确说"不确定"**，
绝不用默认值污染后面的伤害计算。

用法
----
    import trait_db
    r = trait_db.resolve("共鸣")
    r["types"]      # '草,毒' 或 None
    r["how"]        # unique / same_types / portrait / ambiguous / unknown
    r["ambiguous"]  # True 时上层必须 fail-closed

    r = trait_db.resolve("共鸣", portrait_id=3017)   # 头像已识别出物种时
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paths  # noqa: E402

DB_PATH = str(paths.DB)
EVO_JSON = os.path.join(str(paths.GAME_DATA), "evolution_map.json")

_IDX = None
_EVO = None


def connect(db_path=None):
    db = sqlite3.connect(db_path or DB_PATH)
    db.row_factory = sqlite3.Row
    return db


# ---------------------------------------------------------------- 进化家族

def load_evolution(path=None):
    """{species_id: family_id}。文件不在就返回空表（不影响主流程）。"""
    global _EVO
    if _EVO is not None:
        return _EVO
    out = {}
    p = path or EVO_JSON
    try:
        with open(p, encoding="utf-8") as fh:
            j = json.load(fh)
        for fam_id, ent in j.items():
            for node in (ent.get("evolution_chain") or []):
                sid = node.get("petbase_id")
                if sid is not None:
                    out[int(sid)] = int(fam_id)
    except Exception:
        out = {}
    _EVO = out
    return out


# ---------------------------------------------------------------- 索引

def index(db_path=None):
    """特性名 -> {ids, types, families}。进程内缓存一次。"""
    global _IDX
    if _IDX is not None:
        return _IDX
    evo = load_evolution()
    db = connect(db_path)
    try:
        rows = db.execute(
            """SELECT f.name AS trait, sp.id AS sid, sp.types, sp.type_names
               FROM feature_pets fp
               JOIN features f ON f.id = fp.feature_id
               JOIN species sp ON sp.id = fp.species_id
               WHERE fp.is_primary = 1 AND f.name IS NOT NULL""").fetchall()
        sp_types = {}
        for r in db.execute("SELECT id, type_names FROM species"):
            sp_types[int(r["id"])] = (r["type_names"] or "").strip()
    finally:
        db.close()

    idx = {}
    for r in rows:
        t = (r["trait"] or "").strip()
        if not t:
            continue
        sid = int(r["sid"])
        e = idx.setdefault(t, {"ids": [], "types": set(), "families": set()})
        e["ids"].append(sid)
        e["types"].add(sp_types.get(sid, ""))
        fam = evo.get(sid)
        if fam is not None:
            e["families"].add(fam)
    for e in idx.values():
        e["ids"] = sorted(set(e["ids"]))
        # '[]' / 'null' 是"库里没录属性"，不是一种属性，不能进类型集合
        e["types"] = sorted(x for x in e["types"]
                            if x and x.strip() not in ("[]", "null"))
    _IDX = idx
    return idx


UNKNOWN_TRAITS = {"？？？", "?", "", "未知"}


# ---------------------------------------------------------------- 反查

def candidates(trait):
    """特性 -> 候选物种 id 列表（可能为空）。"""
    if not trait:
        return []
    return list(index().get(trait.strip(), {}).get("ids", []))


def resolve(trait, portrait_id=None, species_id=None, db_path=None):
    """特性 -> 属性。返回 dict，`types` 为 None 时上层必须 fail-closed。

    how 的取值：
      unique      特性只对应一个物种
      same_types  多个物种但属性完全相同（不需要知道是哪只）
      portrait    靠头像/已知物种从候选里挑出来
      ambiguous   候选属性不同且无法收窄
      unknown     特性为空/未识别/不在库里
    """
    t = (trait or "").strip()
    out = {"trait": t, "candidates": [], "types": None,
           "how": "unknown", "ambiguous": True, "families": [],
           "best_types": None, "best_species": None,
           "best_how": None, "best_conf": "none"}

    # 特性读不到（空 / '？？？'）**不等于属性未知**：
    # '？？？' 是 PvE 专属首领条目的占位特性（desc："看不出特性"），
    # PvP 里首领化后特性照常显示。但只要真的读到了 '？？？'，
    # 手上若已有头像/名字定出的物种，就该直接用它的属性，不要丢信息。
    if not t or t in UNKNOWN_TRAITS:
        known = species_id if species_id is not None else portrait_id
        if known is not None:
            tn = types_of(known, db_path)
            if tn:
                out.update({"types": tn, "how": "portrait",
                            "ambiguous": False,
                            "best_types": tn,
                            "best_species": int(known),
                            "best_how": "portrait", "best_conf": "high"})
        return out

    e = index(db_path).get(t)
    if not e or not e["ids"]:
        return out

    out["candidates"] = list(e["ids"])
    out["families"] = sorted(e["families"])
    types = e["types"]
    if len(types) == 1:
        out["types"] = types[0]
        out["how"] = "unique" if len(e["ids"]) == 1 else "same_types"
        out["ambiguous"] = False
        out["best_types"] = types[0]
        out["best_species"] = e["ids"][0]
        out["best_how"] = out["how"]
        out["best_conf"] = "high"
        return out

    # 候选属性不同：用已知物种收窄
    for known in (species_id, portrait_id):
        if known is not None and int(known) in e["ids"]:
            db = connect(db_path)
            try:
                r = db.execute("SELECT type_names FROM species WHERE id=?",
                               (int(known),)).fetchone()
            finally:
                db.close()
            if r and r["type_names"]:
                out["types"] = r["type_names"].strip()
                out["how"] = "portrait"
                out["ambiguous"] = False
                out["best_types"] = out["types"]
                out["best_species"] = int(known)
                out["best_how"] = "portrait"
                out["best_conf"] = "high"
                return out

    out["how"] = "ambiguous"
    _fill_best(out, e, db_path)
    return out


def _fill_best(out, e, db_path=None):
    """候选属性不一致时，给出一个"最佳猜测"填进 out。

    用户口径（2026-11-20）：宁可按默认算，也不要完全不算；
    算错了后面可以人工二次修正。

    优先级：
      1. 候选名字全部相同 -> 名字确定，只是库里重复条目（如 千棘海针）
      2. 属性组里数量最多的那个（平票取第一个）
      3. 都没有 -> 取第一个候选
    """
    ids = e.get("ids") or []
    if not ids:
        return
    db = connect(db_path)
    try:
        rows = []
        for cid in ids:
            r = db.execute("SELECT id,name,type_names FROM species WHERE id=?",
                           (int(cid),)).fetchone()
            if r:
                rows.append((r["id"], r["name"] or "",
                             (r["type_names"] or "").strip()))
    finally:
        db.close()
    if not rows:
        return

    # 1) 名字全部相同
    names = {n for _i, n, _t in rows}
    if len(names) == 1:
        tn = rows[0][2]
        if tn and tn not in ("[]", "null"):
            out.update({"best_types": tn, "best_species": rows[0][0],
                        "best_how": "same_name", "best_conf": "high"})
            return

    # 2) 属性组按出现次数排序（稳定排序 -> 平票时保留第一个）
    order = []
    counts = {}
    first_id = {}
    for cid, _n, tn in rows:
        if not tn or tn in ("[]", "null"):
            continue
        if tn not in counts:
            order.append(tn)
            counts[tn] = 0
            first_id[tn] = cid
        counts[tn] += 1
    if not counts:
        return
    best_tn = max(order, key=lambda x: (counts[x], -order.index(x)))
    conf = "low" if len(counts) > 1 else "high"
    out.update({"best_types": best_tn, "best_species": first_id[best_tn],
                "best_how": "majority" if len(counts) > 1 else "same_types",
                "best_conf": conf})
    return


def types_of(species_id, db_path=None):
    db = connect(db_path)
    try:
        r = db.execute("SELECT type_names FROM species WHERE id=?",
                       (int(species_id),)).fetchone()
        tn = (r["type_names"] or "").strip() if r else ""
        # '[]' / '' 表示库里这只的属性没录 —— 绝不能当成"有属性"返回，
        # 否则 trait_db 会给出一个空属性却被判成"已确定"。
        if not tn or tn in ("[]", "null"):
            return None
        return tn
    finally:
        db.close()


# ---------------------------------------------------------------- 自检

def report(db_path=None):
    idx = index(db_path)
    db = connect(db_path)
    try:
        total = db.execute("SELECT COUNT(*) FROM species").fetchone()[0]
        with_primary = db.execute(
            "SELECT COUNT(DISTINCT species_id) FROM feature_pets "
            "WHERE is_primary=1").fetchone()[0]
    finally:
        db.close()

    uniq = same = amb = unk = 0
    amb_feats = []
    for t, e in idx.items():
        if t in UNKNOWN_TRAITS:
            unk += len(e["ids"])
            continue
        if len(e["types"]) == 1:
            if len(e["ids"]) == 1:
                uniq += len(e["ids"])
            else:
                same += len(e["ids"])
        else:
            amb += len(e["ids"])
            amb_feats.append((t, len(e["ids"]), len(e["types"])))
    named = db_label = total - with_primary
    print("物种总数 %d（有 primary 特性 %d，无 %d）"
          % (total, with_primary, named))
    print("  unique      特性唯一->物种      %4d 只" % uniq)
    print("  same_types  多物种但属性一致    %4d 只" % same)
    print("  ambiguous   多物种且属性不同    %4d 只  (%d 个特性)"
          % (amb, len(amb_feats)))
    print("  unknown     特性未识别          %4d 只" % unk)
    ok = uniq + same
    print("  => 全库可靠覆盖率 %.1f%%（%d/%d）"
          % (100.0 * ok / max(1, total), ok, total))

    # 但"全库"里混着未实装/占位/NPC/首领化，会把覆盖率严重稀释。
    # 这里再算一次"正常已实装"的口径 —— 这个才是决策要看的数。
    db = connect(db_path)
    try:
        leaders = {int(r[0]) for r in db.execute(
            "SELECT DISTINCT species_id FROM feature_pets WHERE feature_id=299002")}
        norm = []
        for r in db.execute("SELECT id,name,total,implemented FROM species"):
            sid = int(r["id"])
            n = r["name"] or ""
            if not r["implemented"] or sid in leaders:
                continue
            if (r["total"] or 0) <= 5:
                continue
            if "测试" in n or "老师" in n:
                continue
            if 8000 <= sid < 9000 or 440000 <= sid < 450000:
                continue
            norm.append(sid)
    finally:
        db.close()

    prim = {}
    for t, e in idx.items():
        if t in UNKNOWN_TRAITS:
            continue
        for sid in e["ids"]:
            prim.setdefault(sid, []).append(t)
    ok2 = amb2 = none2 = 0
    for sid in norm:
        ts = [t for t in prim.get(sid, [])]
        if not ts:
            none2 += 1
            continue
        allt = set()
        for t in ts:
            allt |= set(idx[t]["types"])
        if len(allt) == 1:
            ok2 += 1
        else:
            amb2 += 1
    tot2 = len(norm)
    print("  => 正常已实装覆盖率 %.1f%%（%d/%d，歧义 %d，无特性 %d）"
          % (100.0 * ok2 / max(1, tot2), ok2, tot2, amb2, none2))
    amb_feats.sort(key=lambda x: -x[1])
    print("\n歧义特性前 10：")
    for t, n, nt in amb_feats[:10]:
        fam = idx[t]["families"]
        print("   %-12s %2d 只  %d 种属性  家族数 %d" % (t, n, nt, len(fam)))
    return {"total": total, "unique": uniq, "same": same,
            "ambiguous": amb, "unknown": unk}


if __name__ == "__main__":
    report()
