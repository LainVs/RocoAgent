# -*- coding: utf-8 -*-
"""游戏数据全量入库：把 rocom-helper 导出的 json 归一到 SQLite。

为什么要这一步：
    rocodata.py 每次查都重新解析 3.5MB 的 pet_skill_map / 1.4MB 的 skill_map，
    而且没有"按属性查克制""按特性反查精灵"这种查询。伤害计算器和对手识别
    需要毫秒级、可联表的访问，所以先把数据落库。

库文件：data/roco.sqlite3（可以随时删掉重建，源数据在 D:\\GameData 只读）

用法：
    python gamedb.py            # 建库 + 打印体检报告
    python gamedb.py --report   # 只读库打印报告（不重建）
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("ROCO_DATA", r"D:\GameData\roco_data\game")
DB_PATH = os.path.join(ROOT, "data", "roco.sqlite3")

SCHEMA = """
CREATE TABLE IF NOT EXISTS species (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    types TEXT,            -- json [1, 4]
    type_names TEXT,       -- json ["火","电"]
    hp INTEGER, atk INTEGER, spa INTEGER,
    def INTEGER, spd INTEGER, spe INTEGER, total INTEGER,
    max_energy INTEGER,
    is_leader INTEGER, stage INTEGER, quality INTEGER,
    attack_style TEXT, portrait_key TEXT, implemented INTEGER
);
CREATE TABLE IF NOT EXISTS types (
    id INTEGER PRIMARY KEY, name TEXT, name_en TEXT, color TEXT
);
CREATE TABLE IF NOT EXISTS type_eff (
    atk INTEGER, def INTEGER, mult REAL, PRIMARY KEY (atk, def)
);
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY,
    name TEXT,
    type_id INTEGER,
    type_name TEXT,
    power INTEGER,          -- dam_para[0]，0 = 变化技
    cost INTEGER,           -- energy_cost[0]
    cd INTEGER,             -- cd_round[0]
    accuracy INTEGER,       -- hit_para，10000 = 必中
    priority INTEGER,
    target_type INTEGER,
    dam_class INTEGER,      -- skill_dam_type
    contact INTEGER,
    effects INTEGER,        -- skill_result 条数
    desc TEXT
);
CREATE TABLE IF NOT EXISTS species_skills (
    species_id INTEGER, skill_id INTEGER, level INTEGER, source TEXT,
    PRIMARY KEY (species_id, skill_id, source)
);
CREATE TABLE IF NOT EXISTS natures (
    id INTEGER PRIMARY KEY, name TEXT,
    plus_stat TEXT, minus_stat TEXT
);
CREATE TABLE IF NOT EXISTS features (
    id INTEGER PRIMARY KEY, name TEXT, desc TEXT,
    effect_type TEXT, category TEXT, params TEXT
);
CREATE TABLE IF NOT EXISTS feature_pets (
    species_id INTEGER, feature_id INTEGER, is_primary INTEGER,
    PRIMARY KEY (species_id, feature_id)
);
CREATE INDEX IF NOT EXISTS ix_skills_type ON skills(type_id);
CREATE INDEX IF NOT EXISTS ix_skills_name ON skills(name);
CREATE INDEX IF NOT EXISTS ix_species_name ON species(name);
CREATE INDEX IF NOT EXISTS ix_features_name ON features(name);
CREATE INDEX IF NOT EXISTS ix_ss_skill ON species_skills(skill_id);
CREATE INDEX IF NOT EXISTS ix_fp_feature ON feature_pets(feature_id);
"""


def _load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def _j(v):
    return json.dumps(v, ensure_ascii=False) if v is not None else None


def build(path=DB_PATH, verbose=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)

    tc = _load("type_chart.json")
    types = {int(t["id"]): t for t in tc["types"]}
    db.executemany("INSERT INTO types(id,name,name_en,color) VALUES(?,?,?,?)",
                   [(i, t["name"], t.get("name_en"), t.get("color"))
                    for i, t in types.items()])
    chart = tc.get("chart") or {}
    rows = []
    for a in types:
        for d in types:
            m = float((chart.get(str(a)) or {}).get(str(d), 1.0))
            rows.append((a, d, m))
    db.executemany("INSERT INTO type_eff(atk,def,mult) VALUES(?,?,?)", rows)

    sp = _load("pet_species.json")
    db.executemany(
        "INSERT INTO species(id,name,types,type_names,hp,atk,spa,def,spd,spe,"
        "total,max_energy,is_leader,stage,quality,attack_style,portrait_key,"
        "implemented) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(int(k), v.get("name"), _j(v.get("types")), _j(v.get("type_names")),
          *[int((v.get("stats") or {}).get(x) or 0)
            for x in ("hp", "atk", "spa", "def", "spd", "spe", "sum")],
          int(v.get("max_energy") or 0),
          int(bool(v.get("is_leader"))), int(v.get("stage") or 0),
          int(v.get("quality") or 0), v.get("attack_style"),
          v.get("portrait_key"), int(bool(v.get("implemented"))))
         for k, v in sp.items()])

    sk = _load("skill_map.json")
    db.executemany(
        "INSERT INTO skills VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(int(k), v.get("name"), v.get("skill_type_id"),
          (types.get(v.get("skill_type_id")) or {}).get("name"),
          int((v.get("dam_para") or [0])[0] or 0),
          int((v.get("energy_cost") or [0])[0] or 0),
          int((v.get("cd_round") or [0])[0] or 0),
          int(v.get("hit_para") or 0), int(v.get("skill_priority") or 0),
          int(v.get("target_type") or 0), int(v.get("skill_dam_type") or 0),
          int(v.get("contact_type") or 0), len(v.get("skill_result") or []),
          v.get("desc"))
         for k, v in sk.items()])

    psm = _load("pet_skill_map.json")
    rows = []
    for k, v in psm.items():
        sid = int(k)
        for item in v.get("level_skills") or []:
            if item.get("skill_id"):
                rows.append((sid, int(item["skill_id"]),
                             int(item.get("level_point") or 0), "level"))
        for item in v.get("machine_skills") or []:
            sid_ = item.get("machine_skill_id") if isinstance(item, dict) else item
            if sid_:
                rows.append((sid, int(sid_), 0, "machine"))
        for item in (v.get("blood_skills") or {}).values():
            if isinstance(item, dict) and item.get("skill_id"):
                rows.append((sid, int(item["skill_id"]),
                             int(item.get("level_point") or 0), "blood"))
    db.executemany("INSERT OR IGNORE INTO species_skills VALUES(?,?,?,?)", rows)

    nat = _load("nature_map.json")
    db.executemany("INSERT INTO natures VALUES(?,?,?,?)",
                   [(int(k), v.get("name"), v.get("positive_stat"),
                     v.get("negative_stat")) for k, v in nat.items()])

    inn = _load("innate_skills.json")
    # 两只表用的是两套 id：
    #   innate_skills.pets[*].feature_skill_ids -> skill_map 里的技能 id（200140 = 悲悯）
    #   innate_skills.skills                     -> buff 层的效果定义（20030370 = 免疫绝处逢生）
    # 游戏里点头像弹出的"特性"面板显示的是前者，所以反查必须以 skill_map 为准。
    frows, fids = [], set()
    for k, v in (inn.get("pets") or {}).items():
        sid = int(k)
        primary = v.get("primary_feature")
        for fid in v.get("feature_skill_ids") or []:
            frows.append((sid, int(fid), int(fid == primary)))
            fids.add(int(fid))
    db.executemany("INSERT OR REPLACE INTO feature_pets VALUES(?,?,?)", frows)

    feat_rows = []
    for fid in fids:
        v = sk.get(str(fid)) or sk.get(fid) or {}
        feat_rows.append((fid, v.get("name"), v.get("desc"), "pet_feature"))
    for k, v in (inn.get("skills") or {}).items():
        if int(k) in fids:
            continue
        feat_rows.append((int(k), v.get("name"), v.get("description"), "innate"))
    db.executemany("INSERT OR REPLACE INTO features(id,name,desc,effect_type,"
                   "category,params) VALUES(?,?,?,?,NULL,NULL)", feat_rows)

    db.commit()
    if verbose:
        print("built %s" % path)
    return db


def report(db=None):
    close = False
    if db is None:
        if not os.path.exists(DB_PATH):
            print("no db, run: python gamedb.py")
            return 1
        db = sqlite3.connect(DB_PATH)
        close = True
    q = lambda sql, *a: db.execute(sql, a).fetchall()

    print("== 规模 ==")
    for t in ("species", "skills", "species_skills", "types", "type_eff",
              "natures", "features", "feature_pets"):
        print("  %-14s %6d" % (t, q("SELECT count(*) FROM %s" % t)[0][0]))

    print("\n== 技能 ==")
    n = q("SELECT count(*) FROM skills WHERE power>0")[0][0]
    print("  伤害技 %d / 变化技 %d" % (n, q("SELECT count(*) FROM skills")[0][0] - n))
    miss = q("SELECT count(*) FROM skills WHERE power>0 AND type_id IS NULL")[0][0]
    print("  有威力但没属性 %d%s" % (miss, "   <-- 需要人工补" if miss else ""))
    for name, cnt in q("SELECT type_name, count(*) FROM skills WHERE power>0 "
                       "GROUP BY type_name ORDER BY 2 DESC"):
        print("    %-4s %3d" % (name or "(无)", cnt))

    print("\n== 属性克制表 ==")
    print("  %d 条关系，其中非中性 %d 条" % (
        q("SELECT count(*) FROM type_eff")[0][0],
        q("SELECT count(*) FROM type_eff WHERE mult<>1.0")[0][0]))

    print("\n== 精灵 ==")
    print("  双属性 %d 只" % q("SELECT count(*) FROM species "
                              "WHERE types LIKE '%,%'")[0][0])
    print("  没技能 %d 只" % q("SELECT count(*) FROM species s WHERE NOT EXISTS "
                              "(SELECT 1 FROM species_skills x WHERE x.species_id=s.id)")[0][0])
    print("  特性反查表：%d 只精灵 / %d 个特性" % (
        q("SELECT count(DISTINCT species_id) FROM feature_pets")[0][0],
        q("SELECT count(*) FROM features")[0][0]))

    print("\n== 我们队伍 ==")
    for n in ("音速犬", "恶魔狼", "波普鹿", "冰钻布鲁斯", "刨雪鹅", "高吹雪"):
        r = q("SELECT id,type_names,hp,atk,spa,def,spd,spe,max_energy FROM species "
              "WHERE name=? ORDER BY implemented DESC, total DESC LIMIT 1", n)
        if not r:
            print("  %-8s MISSING" % n)
            continue
        i, tn, hp, atk, spa, df, spd, spe, me = r[0]
        fs = q("SELECT f.name FROM feature_pets fp JOIN features f ON f.id=fp.feature_id "
               "WHERE fp.species_id=? AND fp.is_primary=1", i)
        dup = q("SELECT count(*) FROM species WHERE name=?", n)[0][0]
        print("  %-8s id=%-7s %-10s HP%d 攻%d 魔%d 防%d 抗%d 速%d E%d 特性=%s%s" % (
            n, i, tn, hp, atk, spa, df, spd, spe, me,
            fs[0][0] if fs else "-",
            "  (同名 %d 个变体)" % dup if dup > 1 else ""))
    if close:
        db.close()
    return 0


if __name__ == "__main__":
    if "--report" in sys.argv:
        sys.exit(report())
    build()
    report()
