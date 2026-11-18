# -*- coding: utf-8 -*-
"""头像库 + 特性补录：把"看到一只没见过的精灵"变成数据库里的一行。

为什么要单独一张表：
    一只精灵可能有多张头像（普通 / 异色 / 炫彩 / 首领形态），同一个头像
    也可能出现在我方和对面两侧（两边 HUD 的框不一样大，模板不能混用）。
    所以 file -> species 是多对一，必须落库，不能靠文件名猜。

    文件名仍然是人能看懂的：``影子剪纸.png``、``影子剪纸#异色.png``。
    ``#`` 后面是变体，前面是精灵名；库里再记 species_id / side。

补录（enrich）：
    游戏数据导出里有些新精灵没有特性（实测 197 只没有，其中 22 只是已实现的），
    但战斗里点开对面头像就能看到特性名和描述。看到一次就写进库一次，
    下次就能按特性反查身份。
"""
from __future__ import annotations

import os
import zlib
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "roco.sqlite3")
SELF_DIR = os.path.join(ROOT, "assets", "portraits")
FOE_DIR = os.path.join(ROOT, "assets", "portraits_foe")

SCHEMA = """
CREATE TABLE IF NOT EXISTS portrait (
    id INTEGER PRIMARY KEY,
    file TEXT NOT NULL,
    species_id INTEGER,
    variant TEXT DEFAULT '普通',
    side TEXT DEFAULT 'foe',        -- foe / self
    source TEXT,                    -- hud / panel / manual
    score REAL,                     -- 入库时与同族模板的相似度（用于发现重复）
    created_at TEXT,
    note TEXT,
    UNIQUE(file, side)
);
CREATE INDEX IF NOT EXISTS ix_portrait_species ON portrait(species_id);
"""


def connect(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def dir_for(side):
    d = FOE_DIR if side == "foe" else SELF_DIR
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- 头像
def add_portrait(file, species_id, variant="普通", side="foe",
                 source="hud", score=None, note=None, path=DB_PATH):
    """登记一张头像。file 是文件名（不含目录）。"""
    db = connect(path)
    try:
        db.execute(
            "INSERT OR REPLACE INTO portrait(file,species_id,variant,side,source,"
            "score,created_at,note) VALUES(?,?,?,?,?,?,?,?)",
            (file, species_id, variant, side, source, score,
             time.strftime("%Y-%m-%d %H:%M:%S"), note))
        db.commit()
    finally:
        db.close()


def list_portraits(side=None, species_id=None, path=DB_PATH):
    db = connect(path)
    try:
        sql = "SELECT * FROM portrait WHERE 1=1"
        args = []
        if side:
            sql += " AND side=?"
            args.append(side)
        if species_id:
            sql += " AND species_id=?"
            args.append(species_id)
        sql += " ORDER BY species_id, variant"
        return [dict(r) for r in db.execute(sql, args).fetchall()]
    finally:
        db.close()


def resolve(filename, side="foe", path=DB_PATH):
    """文件名 -> (species_id, variant, 名字)。查不到就返回 (None, variant, 名字)。"""
    stem = os.path.splitext(os.path.basename(filename))[0]
    name, _, variant = stem.partition("#")
    variant = variant or "普通"
    db = connect(path)
    try:
        r = db.execute("SELECT species_id FROM portrait WHERE file=? AND side=?",
                       (os.path.basename(filename), side)).fetchone()
        if r:
            return r["species_id"], variant, name
        r = db.execute("SELECT id FROM species WHERE name=? "
                       "ORDER BY implemented DESC, total DESC LIMIT 1",
                       (name,)).fetchone()
        return (r["id"] if r else None), variant, name
    finally:
        db.close()


def species_of(filename, side="foe", path=DB_PATH):
    return resolve(filename, side, path)[0]


def species_name(species_id, path=DB_PATH):
    if not species_id:
        return None
    db = connect(path)
    try:
        r = db.execute("SELECT name FROM species WHERE id=?", (species_id,)).fetchone()
        return r["name"] if r else None
    finally:
        db.close()


# ---------------------------------------------------------------- 属性对位
def species_types_by_name(name, path=DB_PATH):
    """种族名 -> 属性 id 列表。同名变体取总族值最高的那条。"""
    import json as _json
    if not name:
        return []
    db = connect(path)
    try:
        r = db.execute("SELECT types FROM species WHERE name=? "
                       "ORDER BY implemented DESC, total DESC LIMIT 1",
                       (name,)).fetchone()
        return _json.loads(r["types"]) if r and r["types"] else []
    finally:
        db.close()


def type_mult(atk, deff, path=DB_PATH):
    db = connect(path)
    try:
        r = db.execute("SELECT mult FROM type_eff WHERE atk=? AND def=?",
                       (atk, deff)).fetchone()
        return float(r["mult"]) if r else 1.0
    finally:
        db.close()


_mult_cache = None


def type_table(path=DB_PATH):
    """(atk, def) -> mult，一次读全表，换人评分每回合要用很多次。"""
    global _mult_cache
    if _mult_cache is not None:
        return _mult_cache
    db = connect(path)
    try:
        _mult_cache = {(r["atk"], r["def"]): float(r["mult"])
                       for r in db.execute("SELECT atk, def, mult FROM type_eff")}
        return _mult_cache
    finally:
        db.close()


def matchup(my_types, foe_types, path=DB_PATH):
    """正数=我方占优。攻防各取最好的一对，再相减。

    只看属性，不看技能威力 —— 不知道对面带了什么招，也没法知道我们后排
    带什么招，所以这里给的是"属性上打不打得动"的先验，真正的取舍留给
    伤害模型。
    """
    if not my_types or not foe_types:
        return 0.0
    tab = type_table(path)
    off = max(tab.get((t, f), 1.0) for t in my_types for f in foe_types)
    deff = max(tab.get((f, t), 1.0) for f in foe_types for t in my_types)
    return off - deff


# ---------------------------------------------------------------- 特性补录
PANEL_FEATURE_BASE = 990000000


def _feature_id(name):
    h = int(zlib.crc32(name.encode("utf-8")) & 0x7FFFFFFF)
    return PANEL_FEATURE_BASE + (h % 10000000)


def learn_feature(species_id, name, desc=None, path=DB_PATH):
    """把面板上读到的特性写进库（已有同名特性就复用）。

    返回 (feature_id, created)。
    """
    if not name:
        return None, False
    db = connect(path)
    try:
        r = db.execute("SELECT id FROM features WHERE name=?", (name,)).fetchone()
        if r:
            fid, created = r["id"], False
        else:
            fid = _feature_id(name)
            db.execute("INSERT OR REPLACE INTO features(id,name,desc,effect_type,"
                       "category,params) VALUES(?,?,?,'panel',NULL,NULL)",
                       (fid, name, desc))
            created = True
        db.execute("INSERT OR REPLACE INTO feature_pets(species_id,feature_id,"
                   "is_primary) VALUES(?,?,1)", (species_id, fid))
        db.commit()
        return fid, created
    finally:
        db.close()


def learn_skill(species_id, name, cost=None, desc=None, type_id=None,
                power=0, path=DB_PATH):
    """面板上看到的技能，补进 skills 并挂到该精灵身上。

    只有名字时也记：技能名是唯一键，威力/属性以后可以再补。
    """
    if not name:
        return None, False
    db = connect(path)
    try:
        r = db.execute("SELECT id FROM skills WHERE name=? LIMIT 1", (name,)).fetchone()
        if r:
            sid, created = r["id"], False
        else:
            sid = 900000000 + (int(zlib.crc32(name.encode("utf-8")) & 0x7FFFFFFF)
                               % 10000000)
            db.execute("INSERT INTO skills(id,name,type_id,type_name,power,cost,cd,"
                       "accuracy,priority,target_type,dam_class,contact,effects,desc) "
                       "VALUES(?,?,?,?,?,?,0,0,0,0,0,0,0,?)",
                       (sid, name, type_id, None, power or 0, cost, desc))
            created = True
        db.execute("INSERT OR IGNORE INTO species_skills(species_id,skill_id,level,"
                   "source) VALUES(?,?,0,'panel')", (species_id, sid))
        db.commit()
        return sid, created
    finally:
        db.close()


def known_feature(species_id, path=DB_PATH):
    db = connect(path)
    try:
        r = db.execute(
            "SELECT f.id, f.name, f.desc, f.effect_type FROM feature_pets fp "
            "JOIN features f ON f.id=fp.feature_id "
            "WHERE fp.species_id=? AND fp.is_primary=1", (species_id,)).fetchone()
        return dict(r) if r else None
    finally:
        db.close()


if __name__ == "__main__":
    db = connect()
    n = db.execute("SELECT count(*) FROM portrait").fetchone()[0]
    print("portraits in db: %d" % n)
    print("foe dir:", FOE_DIR)
    print("self dir:", SELF_DIR)
    for r in db.execute("SELECT side, count(*) c FROM portrait GROUP BY side"):
        print("  %s %d" % (r[0], r[1]))
