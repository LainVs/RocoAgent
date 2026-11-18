# -*- coding: utf-8 -*-
"""我方队伍配置：面板里点选录入，机器人直接读。

为什么单独一张表：
    种族值只是"这个物种的上限"，实战伤害要用的是**这只精灵自己的六维**
    （等级 60 + 性格 + 天赋之后的值）。只有这张表里的数字能让伤害计算器
    从"全局常数 k"变成"按实际攻防算"，也才能算 TKO。

字段说明：
    species_id  种族 id（gamedb.species.id），决定属性、特性、可学技能
    level       等级，目前都是 60
    nature_id   性格（natures.id），只影响 +10%/-10% 两项
    hp/atk/spa/def/spd/spe   **实际面板值**，不是种族值
    max_hp      最大血量（面板上的 417/417 里的 417）
    moves       4 个技能 id 的 json 数组，按技能栏 1-4 顺序

没有填的字段留 NULL，不会瞎猜。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "roco.sqlite3")

SCHEMA = """
CREATE TABLE IF NOT EXISTS my_team (
    slot INTEGER PRIMARY KEY,          -- 1..6，和队伍面板顺序一致
    species_id INTEGER,
    level INTEGER DEFAULT 60,
    nature_id INTEGER,
    hp INTEGER, atk INTEGER, spa INTEGER,
    def INTEGER, spd INTEGER, spe INTEGER,
    max_hp INTEGER,
    moves TEXT,                        -- json [id, id, id, id]
    note TEXT,
    updated_at TEXT
);
"""

STATS = ("hp", "atk", "spa", "def", "spd", "spe")


def connect(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


# ---------------------------------------------------------------- 读
def load_team(path=DB_PATH):
    db = connect(path)
    try:
        rows = db.execute("SELECT * FROM my_team ORDER BY slot").fetchall()
    finally:
        db.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["moves"] = json.loads(d["moves"]) if d["moves"] else []
        except ValueError:
            d["moves"] = []
        out.append(d)
    return out


def species_options(path=DB_PATH, query=""):
    """下拉框用：只保留实现的、有种族值的，按总族值排序。"""
    db = connect(path)
    try:
        sql = ("SELECT id, name, type_names, total, hp, atk, spa, def, spd, spe "
               "FROM species WHERE implemented=1 AND total>0")
        args = ()
        if query:
            sql += " AND name LIKE ?"
            args = ("%" + query + "%",)
        sql += " ORDER BY total DESC, name"
        return [dict(r) for r in db.execute(sql, args).fetchall()]
    finally:
        db.close()


def moves_for(species_id, path=DB_PATH):
    """该精灵能学的技能（等级/学习机/血脉），带威力属性耗能。"""
    db = connect(path)
    try:
        rows = db.execute(
            "SELECT s.id, s.name, s.power, s.cost, s.type_name, s.desc, "
            "       ss.source, ss.level "
            "FROM species_skills ss JOIN skills s ON s.id = ss.skill_id "
            "WHERE ss.species_id=? ORDER BY ss.source, ss.level, s.name",
            (species_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def natures(path=DB_PATH):
    db = connect(path)
    try:
        return [dict(r) for r in db.execute(
            "SELECT id, name, plus_stat, minus_stat FROM natures ORDER BY id")]
    finally:
        db.close()


def species_row(species_id, path=DB_PATH):
    db = connect(path)
    try:
        r = db.execute("SELECT * FROM species WHERE id=?", (species_id,)).fetchone()
        return dict(r) if r else None
    finally:
        db.close()


def feature_of(species_id, path=DB_PATH):
    """主特性名（游戏里点头像看到的那个）。"""
    db = connect(path)
    try:
        r = db.execute(
            "SELECT f.name, f.desc FROM feature_pets fp JOIN features f "
            "ON f.id = fp.feature_id WHERE fp.species_id=? AND fp.is_primary=1",
            (species_id,)).fetchone()
        return dict(r) if r else None
    finally:
        db.close()


def species_by_feature(feature_name, path=DB_PATH):
    """特性名 -> 可能的种族。对手识别靠这个：点头像读特性，反查身份。"""
    db = connect(path)
    try:
        rows = db.execute(
            "SELECT sp.id, sp.name, sp.type_names, sp.total, fp.is_primary "
            "FROM features f JOIN feature_pets fp ON fp.feature_id = f.id "
            "JOIN species sp ON sp.id = fp.species_id "
            "WHERE f.name = ? ORDER BY fp.is_primary DESC, sp.total DESC",
            (feature_name,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


# ---------------------------------------------------------------- 写
def save_pet(slot, data, path=DB_PATH):
    """slot 1..6。data 里没给的字段保持原值。"""
    db = connect(path)
    try:
        cur = db.execute("SELECT * FROM my_team WHERE slot=?", (slot,)).fetchone()
        row = dict(cur) if cur else {
            "slot": slot, "species_id": None, "level": 60, "nature_id": None,
            **{s: None for s in STATS}, "max_hp": None, "moves": None, "note": None,
        }
        for k in ("species_id", "level", "nature_id", "max_hp", "note"):
            if k in data:
                row[k] = data[k]
        for s in STATS:
            if s in data:
                row[s] = data[s]
        if "moves" in data:
            row["moves"] = json.dumps(list(data["moves"] or []))
        row["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "INSERT OR REPLACE INTO my_team(slot,species_id,level,nature_id,hp,atk,"
            "spa,def,spd,spe,max_hp,moves,note,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (slot, row["species_id"], row["level"], row["nature_id"], row["hp"],
             row["atk"], row["spa"], row["def"], row["spd"], row["spe"],
             row["max_hp"], row["moves"], row["note"], row["updated_at"]))
        db.commit()
        return dict(db.execute("SELECT * FROM my_team WHERE slot=?", (slot,)).fetchone())
    finally:
        db.close()


def clear_slot(slot, path=DB_PATH):
    db = connect(path)
    try:
        db.execute("DELETE FROM my_team WHERE slot=?", (slot,))
        db.commit()
    finally:
        db.close()


def dump_team(path=DB_PATH):
    """给机器人/计算器用的紧凑结构。"""
    out = []
    for p in load_team(path):
        if not p.get("species_id"):
            continue
        sp = species_row(p["species_id"], path) or {}
        feat = feature_of(p["species_id"], path) or {}
        out.append({
            "slot": p["slot"],
            "species_id": p["species_id"],
            "name": sp.get("name"),
            "types": json.loads(sp.get("types") or "[]"),
            "type_names": json.loads(sp.get("type_names") or "[]"),
            "feature": feat.get("name"),
            "level": p.get("level") or 60,
            "nature_id": p.get("nature_id"),
            "stats": {s: p.get(s) for s in STATS},
            "max_hp": p.get("max_hp"),
            "moves": p.get("moves") or [],
            "base": {s: sp.get(s) for s in STATS},
        })
    return out


if __name__ == "__main__":
    import sys
    if "--dump" in sys.argv:
        print(json.dumps(dump_team(), ensure_ascii=False, indent=1))
    else:
        db = connect()
        n = db.execute("SELECT count(*) FROM my_team").fetchone()[0]
        print("db ok, %d/6 slots filled" % n)
        print("species options: %d" % len(species_options()))
        print("natures: %d" % len(natures()))
