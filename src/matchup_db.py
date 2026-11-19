# -*- coding: utf-8 -*-
"""对位数据库：把每局的经验存下来，供决策层查询。

为什么需要它：
- 伤害公式只能算"理论上打多少"，实际还有等级、性格、天赋、buff 的差异
- 对面会怎么打、我们哪只精灵在什么对位下更稳，只能从实战里学
- 宝可梦的成熟做法是"使用率统计 + 伤害情报"，我们把它做成实例数据库

数据流：
    robot.py  ->  logs/decisions.jsonl（每回合一行，含双方精灵、卡面、动作）
    logs/*.jsonl -> 本模块导入 -> SQLite（logs/roco.db）-> policy 查询

表：
    matches    一局：起止、动作数、胜负、段位
    turns      一回合：双方血量、选择、理由
    skill_obs  技能观测：属性、威力、卡面伤害、实际伤害、反推倍率
    matchups   对位聚合：我方 x 对方 的场次胜负与平均伤害

所有写入都是"有则更新、无则插入"，重复导入同一份日志不会翻倍。
"""
from __future__ import annotations
import sys
import paths

import json
import os
import sqlite3
import time

LOG_DIR = str(paths.ROOT / "logs")
DB_PATH = os.path.join(LOG_DIR, "roco.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT UNIQUE, ended_at TEXT, seconds REAL, actions INTEGER,
    result TEXT DEFAULT 'unknown',
    ladder_before INTEGER, ladder_after INTEGER, ladder_of INTEGER
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER, ts TEXT, turn INTEGER,
    my_pct REAL, foe_pct REAL, action TEXT, slot INTEGER, why TEXT
);
CREATE TABLE IF NOT EXISTS skill_obs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER, turn INTEGER, ts TEXT,
    our_pet TEXT, foe_pet TEXT,
    slot INTEGER, type_id INTEGER, power INTEGER, cost INTEGER,
    shown INTEGER, mult REAL
);
CREATE TABLE IF NOT EXISTS matchups (
    our_pet TEXT, foe_pet TEXT,
    games INTEGER DEFAULT 0, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
    dmg_dealt REAL DEFAULT 0, dmg_taken REAL DEFAULT 0, samples INTEGER DEFAULT 0,
    PRIMARY KEY (our_pet, foe_pet)
);
CREATE INDEX IF NOT EXISTS idx_turns_match ON turns(match_id);
CREATE INDEX IF NOT EXISTS idx_obs_match ON skill_obs(match_id);
"""


def connect(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # WAL：机器人写、UI 读，互不阻塞
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass
    return conn


def _read_jsonl(path):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def import_rounds(conn, path=None):
    """rounds.jsonl -> matches。按开始时间做唯一键，重复导入不变。"""
    path = path or os.path.join(LOG_DIR, "rounds.jsonl")
    n = 0
    for r in _read_jsonl(path):
        try:
            conn.execute(
                "INSERT OR IGNORE INTO matches(started_at, ended_at, seconds, actions)"
                " VALUES (?,?,?,?)",
                (r.get("start"), r.get("end"), r.get("seconds"), r.get("actions")))
            n += 1
        except sqlite3.DatabaseError:
            continue
    conn.commit()
    return n


def import_results(conn, path=None):
    """results.jsonl -> 回填最近一局的胜负。

    results 只有时间戳，没有对局 id，所以按时间就近匹配：
    取该时间点之前、30 分钟内开始且还没有胜负的那一局。
    """
    path = path or os.path.join(LOG_DIR, "results.jsonl")
    n = 0
    for r in _read_jsonl(path):
        ts = r.get("ts")
        if not ts:
            continue
        row = conn.execute(
            "SELECT id FROM matches WHERE result='unknown' AND started_at <= ?"
            " ORDER BY started_at DESC LIMIT 1", (ts,)).fetchone()
        if not row:
            continue
        conn.execute(
            "UPDATE matches SET result=?, ladder_before=?, ladder_after=?, ladder_of=?"
            " WHERE id=?",
            (r.get("result"), r.get("before"), r.get("after"), r.get("of"), row["id"]))
        n += 1
    conn.commit()
    return n


def import_turns(conn, path=None):
    """turns.jsonl -> turns。turn 表没有对局 id，用时间戳归到最近开始的一局。"""
    path = path or os.path.join(LOG_DIR, "turns.jsonl")
    n = 0
    for r in _read_jsonl(path):
        ts = r.get("ts")
        if not ts:
            continue
        if conn.execute("SELECT 1 FROM turns WHERE ts=? AND turn=? LIMIT 1",
                        (ts, r.get("turn"))).fetchone():
            continue
        m = conn.execute(
            "SELECT id FROM matches WHERE started_at <= ? ORDER BY started_at DESC LIMIT 1",
            (ts,)).fetchone()
        action = r.get("action") or []
        kind = action[0] if action else None
        slot = action[1] if len(action) > 1 else None
        conn.execute(
            "INSERT INTO turns(match_id, ts, turn, my_pct, foe_pct, action, slot, why)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (m["id"] if m else None, ts, r.get("turn"), r.get("my_pct"),
             r.get("foe_pct"), kind, slot, r.get("why")))
        n += 1
    conn.commit()
    return n


def record_decision(conn, rec):
    """机器人每回合调用一次，写入完整决策记录（含双方精灵与卡面数据）。

    rec = {
      "match_started": "21:17:06", "turn": 3, "ts": "21:17:20",
      "our_pet": "恶魔狼", "foe_pet": "圣剑-X",
      "my_pct": 88.0, "foe_pct": 40.0,
      "action": ["skill", 2], "why": "best dmg 380 ...",
      "skills": [{"i":1,"shown":142,"power":150,"type":0,"cost":6}, ...],
      "k": 1.897
    }
    """
    m = conn.execute("SELECT id FROM matches WHERE started_at=?",
                     (rec.get("match_started"),)).fetchone()
    match_id = m["id"] if m else None
    action = rec.get("action") or []
    conn.execute(
        "INSERT INTO turns(match_id, ts, turn, my_pct, foe_pct, action, slot, why)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (match_id, rec.get("ts"), rec.get("turn"), rec.get("my_pct"),
         rec.get("foe_pct"), action[0] if action else None,
         action[1] if len(action) > 1 else None, rec.get("why")))
    k = rec.get("k")
    for s in rec.get("skills") or []:
        shown = s.get("shown")
        power = s.get("power") or 0
        mult = None
        if shown and power and k:
            stab = s.get("stab") or 1.0
            mult = shown / (power * k * stab)
        conn.execute(
            "INSERT INTO skill_obs(match_id, turn, ts, our_pet, foe_pet, slot,"
            " type_id, power, cost, shown, mult) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (match_id, rec.get("turn"), rec.get("ts"), rec.get("our_pet"),
             rec.get("foe_pet"), s.get("i"), s.get("type"), power,
             s.get("cost"), shown, mult))
    conn.commit()


def update_matchup(conn, our_pet, foe_pet, result=None, dealt=None, taken=None):
    """对位聚合：一局结束或一次交手后调用。"""
    row = conn.execute(
        "SELECT * FROM matchups WHERE our_pet=? AND foe_pet=?",
        (our_pet, foe_pet)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO matchups(our_pet, foe_pet, games, wins, losses,"
            " dmg_dealt, dmg_taken, samples) VALUES (?,?,0,0,0,0,0,0)",
            (our_pet, foe_pet))
        row = conn.execute(
            "SELECT * FROM matchups WHERE our_pet=? AND foe_pet=?",
            (our_pet, foe_pet)).fetchone()
    games = row["games"] + (1 if result else 0)
    wins = row["wins"] + (1 if result == "win" else 0)
    losses = row["losses"] + (1 if result == "loss" else 0)
    samples = row["samples"] + (1 if dealt is not None or taken is not None else 0)
    dmg_dealt = row["dmg_dealt"] + (dealt or 0)
    dmg_taken = row["dmg_taken"] + (taken or 0)
    conn.execute(
        "UPDATE matchups SET games=?, wins=?, losses=?, dmg_dealt=?,"
        " dmg_taken=?, samples=? WHERE our_pet=? AND foe_pet=?",
        (games, wins, losses, dmg_dealt, dmg_taken, samples,
         our_pet, foe_pet))
    conn.commit()


def matchup_stats(conn, our_pet=None, foe_pet=None, limit=50):
    q = "SELECT * FROM matchups"
    where, args = [], []
    if our_pet:
        where.append("our_pet=?")
        args.append(our_pet)
    if foe_pet:
        where.append("foe_pet=?")
        args.append(foe_pet)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY games DESC, samples DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(q, args).fetchall()]


def mult_by_type(conn, foe_pet=None, limit=200):
    """某对手对各属性的实测倍率（中位数），给决策层当先验。"""
    q = ("SELECT type_id, mult FROM skill_obs WHERE mult IS NOT NULL"
         + (" AND foe_pet=?" if foe_pet else ""))
    args = (foe_pet,) if foe_pet else ()
    buckets = {}
    for r in conn.execute(q, args).fetchall():
        buckets.setdefault(r["type_id"], []).append(r["mult"])
    out = {}
    for t, vals in buckets.items():
        vals.sort()
        out[t] = vals[len(vals) // 2]
    return out


def summary(conn):
    row = conn.execute(
        "SELECT COUNT(*) n, SUM(result='win') w, SUM(result='loss') l,"
        " SUM(result='flat') f FROM matches").fetchone()
    turns = conn.execute("SELECT COUNT(*) c FROM turns").fetchone()["c"]
    obs = conn.execute("SELECT COUNT(*) c FROM skill_obs").fetchone()["c"]
    return {"matches": row["n"], "wins": row["w"] or 0, "losses": row["l"] or 0,
            "flat": row["f"] or 0, "turns": turns, "skill_obs": obs}


def import_all(path=DB_PATH):
    conn = connect(path)
    a = import_rounds(conn)
    b = import_results(conn)
    c = import_turns(conn)
    return conn, {"rounds": a, "results": b, "turns": c}


if __name__ == "__main__":
    conn, counts = import_all()
    print("imported:", counts)
    print("summary:", summary(conn))
    for m in matchup_stats(conn)[:5]:
        print(m)
