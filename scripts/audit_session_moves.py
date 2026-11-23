# -*- coding: utf-8 -*-
"""audit_session_moves.py —— 体检 session_moves.json 的数据卫生。

用法：
    & $py scripts\\audit_session_moves.py

## 为什么要查这个

`session_moves` 以 (pet, slot) 为键**永久落盘**。BAND 写入时用的是当时的
`my_name` —— 而实测身份会抖（旧模板库曾把恶魔狼认成音速犬#4）。
一旦在错身份下写入，数据就永久挂在错的精灵名下，会污染
`_skill_meta`（perceive 三层降级会用它覆盖 team_db）、让 team_db
被无端标 stale、让策略拿到错误的技能伤害/耗能。

## 判据（2026-11-23 修正过：原来过严）
也不能直接判脏 —— 因为该表**数据不全**（2026-11-23 实证：卡瓦重 /
冰钻布鲁斯的配置里有「扬沙」，而 species_skills 两处都没有这条）。

正确的判据是三选一，任一成立即可信：
  1. 在 species_skills 可学池内
  2. 在该精灵 my_team 配置的 4 个技能内（用户亲手配的，权威性最高）
  3. 该技能有 >= 50 个属主（通用技能，大量精灵都能带）

判脏的充分条件：上面三条**全部**不成立。
"""
import sqlite3, json, io, os

ROOT = r"D:\Projects\RocoAgent"
db = sqlite3.connect(os.path.join(ROOT, "data", "roco.sqlite3"))
db.row_factory = sqlite3.Row

# 每只精灵的 my_team 配置（按 species_id 反查名字）
_config = {}
for row in db.execute("SELECT slot,species_id,moves FROM my_team"):
    sp = db.execute("SELECT name FROM species WHERE id=?",
                    (row["species_id"],)).fetchone()
    if not sp or not row["moves"]:
        continue
    names = []
    for m in json.loads(row["moves"]):
        r = db.execute("SELECT name FROM skills WHERE id=?", (m,)).fetchone()
        if r and r[0]:
            names.append(r[0])
    _config[sp["name"]] = names


def pool(pet):
    return {r["name"] for r in db.execute(
        """SELECT DISTINCT s.name FROM species_skills ss
           JOIN species sp ON sp.id=ss.species_id
           JOIN skills s ON s.id=ss.skill_id
           WHERE sp.name=? AND s.name IS NOT NULL""", (pet,))}


def is_species(pet):
    return db.execute("SELECT 1 FROM species WHERE name=?", (pet,)).fetchone() is not None


def owners(n):
    return [r["name"] for r in db.execute(
        """SELECT DISTINCT sp.name FROM species_skills ss
           JOIN species sp ON sp.id=ss.species_id
           JOIN skills s ON s.id=ss.skill_id
           WHERE s.name=?""", (n,))]


def verify(pet, name):
    """返回 (可信?, 依据)。三条任一成立即为可信。"""
    P = pool(pet)
    if name in P:
        return True, "species_skills 池内"
    if name in _config.get(pet, []):
        return True, "my_team 配置内"
    n = len(owners(name))
    if n >= 50:
        return True, "通用技能(%d 属主)" % n
    return False, "不在池/不在配置/属主仅 %d" % n


d = json.load(io.open(os.path.join(ROOT, "data", "session_moves.json"),
                      encoding="utf-8"))
print("=" * 72)
BAD = []
for pet, slots in sorted(d.get("pets", {}).items()):
    sp_ok = is_species(pet)
    P = pool(pet) if sp_ok else set()
    print("\n【%s】%s  可学池 %d 个"
          % (pet, "" if sp_ok else "  *** 不是物种名 ***", len(P)))
    for s in sorted(slots, key=int):
        e = slots[s]
        name = e.get("name")
        if not name:
            continue
        ok, why = verify(pet, name) if sp_ok else (False, "非物种名")
        print("   槽%-2s %-10s %s  src=%s  (%s)"
              % (s, name, "OK  " if ok else "BAD ", e.get("source"), why))
        if not ok:
            BAD.append((pet, s, name, why))

print("\n" + "=" * 72)
print("不可信记录 %d 条" % len(BAD))
for pet, s, name, why in BAD:
    print("   %s 槽%s = %s   (%s)" % (pet, s, name, why))

print("\n=== 跨精灵重复的技能名（同一技能出现在多个精灵名下）===")
from collections import defaultdict
by_name = defaultdict(list)
for pet, slots in d.get("pets", {}).items():
    for s, e in slots.items():
        if e.get("name"):
            by_name[e["name"]].append("%s槽%s" % (pet, s))
for name, where in sorted(by_name.items()):
    if len(where) > 1:
        print("   %-10s 出现在 %s   属主数=%d"
              % (name, where, len(owners(name))))
db.close()