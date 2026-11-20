# -*- coding: utf-8 -*-
"""import_9178.py —— 把 9178 伤害计算器的精灵数值导入成我们的标准数据

来源：https://asdfghjkl0828.github.io/9178/data/spirits.js
该计算器是 1:1 PvP 调参验证过的；精灵库可能不全，但**机制与数值是可信的**。

产出
    data/spirits_9178.json   规范化后的 624 条（名字/六维/属性/特性/编号）
    logs/annot/stats_diff.json 与我们库的差异清单（冲突只标记，不覆盖）

用法
    python scripts\\import_9178.py --src <spirits.js>
    python scripts\\import_9178.py --src <spirits.js> --fill-placeholders
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import paths  # noqa: E402

OUT = os.path.join(str(paths.DATA), "spirits_9178.json")
DIFF = os.path.join(str(paths.LOGS), "annot", "stats_diff.json")
PLACEHOLDER_VECTORS = set()
COLS = [("hp", "hp"), ("pa", "atk"), ("ma", "spa"),
        ("pd", "def"), ("md", "spd"), ("sp", "spe")]


def parse(path):
    s = io.open(path, encoding="utf-8").read().strip()
    s = re.sub(r"^const\s+SPIRITS\s*=\s*", "", s)
    s = re.sub(r";\s*$", "", s)
    s = re.sub(r",(\s*[\]}])", r"\1", s)
    return json.loads(s)


def norm(d):
    return {
        "no": d.get("no"),
        "name": d.get("n"),
        "hp": d.get("hp"), "atk": d.get("pa"), "spa": d.get("ma"),
        "def": d.get("pd"), "spd": d.get("md"), "spe": d.get("sp"),
        "type1": d.get("a1") or "",
        "type2": d.get("a2") or "",
        "trait": d.get("tr") or "",
        "star": d.get("st") or "",
        "source": "9178",
    }


def is_placeholder(r):
    """判断一行是不是占位值。

    踩过的坑：库里有一组**共享占位模板** [66,74,36,58,44,63]，被几十只精灵复用
    （巨鼓象就是其中之一，真值 hp=130）。所以不能只看"total<=5 或全 5"，
    还要看"这个六维向量是否被多只共用"——真实精灵的六维是唯一的。
    """
    vec = tuple(int(r[b] or 0) for _, b in COLS)
    if vec in PLACEHOLDER_VECTORS:
        return True
    vals = list(vec)
    return (r["total"] or 0) <= 5 or all(v <= 5 for v in vals) or \
        sum(1 for v in vals if v == 50) >= 4


def build_placeholder_vectors(db):
    """出现 ≥3 次的六维向量视为占位模板。"""
    from collections import Counter
    c = Counter()
    for r in db.execute("SELECT hp, atk, spa, def, spd, spe FROM species"):
        c[tuple(int(r[b] or 0) for _, b in COLS)] += 1
    return {v for v, n in c.items() if n >= 3 and sum(v) > 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--fill-placeholders", action="store_true",
                    help="把我们库里是占位值的 45 只用他们的数值填上")
    a = ap.parse_args()

    items = [norm(d) for d in parse(a.src)]
    items = [x for x in items if x["name"]]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=1)
    print("导出 %d 条 -> %s" % (len(items), OUT))

    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    global PLACEHOLDER_VECTORS
    PLACEHOLDER_VECTORS = build_placeholder_vectors(db)
    print("识别到 %d 个占位模板" % len(PLACEHOLDER_VECTORS))
    rows = {}
    for r in db.execute("SELECT id, name, hp, atk, spa, def, spd, spe, total, "
                        "provisional FROM species"):
        rows.setdefault(r["name"], []).append(r)

    same = fill = missing = conflict = 0
    diffs = []
    filled = 0
    for x in items:
        theirs = [x[b] for _, b in COLS]
        cands = rows.get(x["name"])
        if not cands:
            missing += 1
            diffs.append({"name": x["name"], "kind": "missing", "theirs": theirs})
            continue
        if any([int(r[b] or -1) for _, b in COLS] == theirs for r in cands):
            same += 1
            continue
        if all(is_placeholder(r) for r in cands):
            fill += 1
            diffs.append({"name": x["name"], "kind": "placeholder",
                          "theirs": theirs})
            if a.fill_placeholders:
                r = cands[0]
                db.execute("UPDATE species SET hp=?, atk=?, spa=?, def=?, "
                           "spd=?, spe=?, total=?, provisional=0 WHERE id=?",
                           (x["hp"], x["atk"], x["spa"], x["def"],
                            x["spd"], x["spe"],
                            sum(int(v or 0) for v in theirs), r["id"]))
                filled += 1
            continue
        conflict += 1
        diffs.append({"name": x["name"], "kind": "conflict", "theirs": theirs,
                      "ours": [int(cands[0][b] or -1) for _, b in COLS]})

    db.commit()
    print("一致 %d / 可补占位 %d / 我们缺 %d / 冲突 %d"
          % (same, fill, missing, conflict))
    if a.fill_placeholders:
        print("已写入 %d 只（provisional 置 0）" % filled)

    os.makedirs(os.path.dirname(DIFF), exist_ok=True)
    with io.open(DIFF, "w", encoding="utf-8") as fh:
        json.dump(diffs, fh, ensure_ascii=False, indent=1)
    print("差异清单 -> %s" % DIFF)
    return 0


if __name__ == "__main__":
    sys.exit(main())
