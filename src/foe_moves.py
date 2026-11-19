# -*- coding: utf-8 -*-
"""对面可能带什么技能：先验（可学技能池）× 实证（历史观测）的混合。

为什么要它：
    "它打我多少"取决于它技能的属性和威力，而技能是玩家自选的。
    只有精灵属性不够 —— 恶系精灵完全可能带火技。

两条证据来源（互补）：
    ① 先验：pet_skill_map 里这只精灵能学的全部技能（覆盖面大，但都很虚）
    ② 实证：日志里见过的 —— 特性面板的 1 个技能 + 播报 OCR 到的技能
       （样本少，但真实）

混合方式：Dirichlet 后验（先验α + 观测计数），观测越多先验权重越小。
    P(skill) = (α_prior(skill) + n_obs(skill)) / (Σα + N)
"""
from __future__ import annotations

import collections
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "roco.sqlite3")
LOG = os.path.join(ROOT, "logs", "bot_stdout.log")

# 先验强度：每个可学技能给 α0，观测一条 +1
ALPHA0 = 0.35


def _db():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db


def learnable(species_name):
    """这只精灵能学的技能（名字, 威力, 属性, 耗能）。"""
    db = _db()
    try:
        return [dict(r) for r in db.execute(
            """SELECT s.name, s.power, s.cost, s.type_id, s.type_name
               FROM species sp
               JOIN species_skills ss ON ss.species_id = sp.id
               JOIN skills s ON s.id = ss.skill_id
               WHERE sp.name = ? AND s.power > 0
               ORDER BY s.power DESC""", (species_name,))]
    finally:
        db.close()


# ---------------------------------------------------------------- 持久化
SCHEMA = """
CREATE TABLE IF NOT EXISTS foe_skill_obs (
    species TEXT, skill TEXT, n INTEGER DEFAULT 0,
    first_seen TEXT, last_seen TEXT,
    PRIMARY KEY (species, skill)
);
"""


def _obs_db():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def note_observation(species, skill, n=1):
    """记一次"这只精灵用过这个技能"。跨重启累积。"""
    if not species or not skill or species == "None" or skill == "None":
        return
    import time
    db = _obs_db()
    try:
        now = time.strftime("%Y-%m-%d %H:%M")
        row = db.execute("SELECT n FROM foe_skill_obs WHERE species=? AND skill=?",
                         (species, skill)).fetchone()
        if row:
            db.execute("UPDATE foe_skill_obs SET n=n+?, last_seen=? "
                       "WHERE species=? AND skill=?", (n, now, species, skill))
        else:
            db.execute("INSERT INTO foe_skill_obs VALUES(?,?,?,?,?)",
                       (species, skill, n, now, now))
        db.commit()
    finally:
        db.close()


def backfill_from_log(log_path=LOG):
    """把日志里 FOE SCAN 到的技能一次性回填进库。返回新增条数。"""
    import time
    pat = re.compile(r"FOE SCAN -> (\S+) lv\S* 特性=\S+ 技能=(\S+)")
    seen = collections.Counter()
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.search(line)
                if not m:
                    continue
                sp, sk = m.group(1), m.group(2)
                if sp in ("None", "") or sk in ("None", ""):
                    continue
                seen[(sp, sk)] += 1
    except OSError:
        return 0
    db = _obs_db()
    added = 0
    try:
        now = time.strftime("%Y-%m-%d %H:%M")
        for (sp, sk), n in seen.items():
            row = db.execute("SELECT n FROM foe_skill_obs WHERE species=? AND skill=?",
                             (sp, sk)).fetchone()
            if row:
                db.execute("UPDATE foe_skill_obs SET last_seen=? "
                           "WHERE species=? AND skill=?", (now, sp, sk))
            else:
                db.execute("INSERT INTO foe_skill_obs VALUES(?,?,?,?,?)",
                           (sp, sk, n, now, now))
                added += 1
        db.commit()
    finally:
        db.close()
    return added


def load_observations():
    """从库里读全部观测 -> {species: Counter(skill)}。"""
    db = _obs_db()
    try:
        out = collections.defaultdict(collections.Counter)
        for r in db.execute("SELECT species, skill, n FROM foe_skill_obs"):
            out[r["species"]][r["skill"]] += int(r["n"] or 0)
        return out
    finally:
        db.close()

_obs_cache = {"at": 0.0, "data": None}


def observed(log_path=LOG, max_age=60.0):
    """从日志里抽"某只精灵用过某技能"。返回 {species: Counter(skill)}。"""
    import time
    if (time.time() - _obs_cache["at"] < max_age
            and _obs_cache["data"] is not None):
        return _obs_cache["data"]
    out = collections.defaultdict(collections.Counter)
    pat = re.compile(r"FOE SCAN -> (\S+) lv\S* 特性=\S+ 技能=(\S+)")
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.search(line)
                if not m:
                    continue
                sp, sk = m.group(1), m.group(2)
                if sp in ("None", "") or sk in ("None", ""):
                    continue
                out[sp][sk] += 1
    except OSError:
        pass
    _obs_cache.update({"at": time.time(), "data": out})
    return out


def distribution(species_name, top=8):
    """技能分布（混合先验与观测），返回 [(skill, p, power, type_id)] 按概率降序。"""
    pool = learnable(species_name)
    if not pool:
        return []
    obs = collections.Counter(load_observations().get(species_name) or {})
    for k, v in (observed().get(species_name) or {}).items():
        obs[k] = max(obs[k], v)
    by_name = {p["name"]: p for p in pool}
    # 先验不是均匀的：玩家更可能带高威力技能。用 (威力/100)^1.5 加权，
    # 200 威力的权重是 100 威力的 2.8 倍，接近实际配招习惯。
    weights = {}
    for p in pool:
        w0 = ALPHA0 * ((int(p["power"] or 0) / 100.0) ** 1.5)
        weights[p["name"]] = max(0.02, w0)
    for name, n in obs.items():
        if name in by_name:
            weights[name] = weights.get(name, ALPHA0) + n
    total = sum(weights.values()) or 1.0
    rows = []
    for name, w in weights.items():
        p = by_name[name]
        rows.append((name, w / total, int(p["power"] or 0), p["type_id"]))
    rows.sort(key=lambda r: -r[1])
    return rows[:top]


def type_mix(species_name):
    """按属性聚合的概率分布 {type_id: p}。"""
    mix = collections.defaultdict(float)
    for _n, p, _pw, t in distribution(species_name, top=32):
        mix[t] += p
    return dict(mix)


def evaluate(species_name, me_types, sample=8):
    """返回这个对手可能的技能（名字/属性/概率/威力），用于伤害区间。

    me_types: 我方属性 id 列表，用于标注"这招打我克制吗"。
    """
    import foe_damage as F
    out = []
    for name, p, power, tid in distribution(species_name, top=sample):
        mult = F.type_mult([tid] if tid is not None else [], me_types)
        out.append({"skill": name, "p": round(p, 3), "power": power,
                    "type_id": tid, "mult_vs_me": mult})
    return out


if __name__ == "__main__":
    sp = "岚鸟"
    print("可学技能 %d 个" % len(learnable(sp)))
    print("观测到:", dict(observed().get(sp) or {}))
    print("\n分布（前 8）：")
    for name, p, pw, tid in distribution(sp):
        print("  %-10s p=%.3f 威力%-4s 属性%s" % (name, p, pw, tid))
    print("\n属性分布:", type_mix(sp))
    print("\n对恶魔狼(恶,16)的可能伤害：")
    for r in evaluate(sp, [16]):
        print("  %-10s p=%.2f 威力%-4s 克制×%.1f" % (
            r["skill"], r["p"], r["power"], r["mult_vs_me"]))
