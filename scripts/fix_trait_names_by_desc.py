# -*- coding: utf-8 -*-
"""用 desc 反查特性的真实名字。

发现：游戏 skill_map 里部分特性的 name 字段填的是"精灵名"而不是"特性名"
（如 200273 name=烟花伯爵 desc=在场时，衰减的灼烧转化为中毒）。

但 desc 是准确的。外部数据源 spirits.js 有 (tr, tr_desc) 成对数据，
可以用 desc 匹配出真实特性名。
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import sys
from difflib import SequenceMatcher

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402

EXT = r"D:\GameData\洛克王国世界\RokePvP-Calculator\data\spirits.js"
GAME = str(paths.GAME_DATA)


def norm(s):
    """去掉标点/空白，只留中文和数字，便于比较。"""
    return re.sub(r"[^\u4e00-\u9fff0-9]", "", s or "")


def main():
    t = io.open(EXT, encoding="utf-8", errors="replace").read()
    arr = json.loads(t[t.index("["):t.rindex("]") + 1])
    # 外部：(trait 名, desc) 去重
    ext_pairs = {}
    for e in arr:
        tr, d = e.get("tr") or "", e.get("tr_desc") or ""
        if tr and d:
            ext_pairs.setdefault(norm(d), tr)

    skill_map = json.load(io.open(os.path.join(GAME, "skill_map.json"),
                                  encoding="utf-8"))
    species = json.load(io.open(os.path.join(GAME, "pet_species.json"),
                                encoding="utf-8"))
    innate = json.load(io.open(os.path.join(GAME, "innate_skills.json"),
                               encoding="utf-8"))

    # 我们库里所有特性 id -> (name, desc)
    feats = {}
    for r in sqlite3.connect(str(paths.DB)).execute(
            "SELECT id,name,desc FROM features"):
        feats[r[0]] = (r[1], r[2] or "")

    print("=== 用 desc 反查 name 可疑的特性 ===")
    fixed = []
    for fid, (name, desc) in sorted(feats.items()):
        if not desc:
            continue
        nd = norm(desc)
        # 精确 desc 匹配
        hit = ext_pairs.get(nd)
        if not hit:
            # 模糊匹配（描述可能有细微差异）
            best, bs = None, 0.0
            for ed, etr in ext_pairs.items():
                s = SequenceMatcher(None, nd, ed).ratio()
                if s > bs:
                    best, bs = etr, s
            if bs >= 0.85:
                hit = best
        if hit and hit != name:
            fixed.append((fid, name, hit, desc[:34]))

    print("  可修正: %d 个特性" % len(fixed))
    print("\n%-10s %-14s %-14s %s"
          % ("id", "当前name(错)", "按desc修正", "desc"))
    print("-" * 84)
    for fid, name, hit, desc in fixed[:40]:
        print("%-10s %-14s %-14s %s" % (fid, name[:14], hit[:14], desc))

    # 统计这些错误特性影响多少精灵
    print("\n=== 影响面 ===")
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    total = 0
    for fid, name, hit, desc in fixed:
        n = db.execute("SELECT COUNT(*) FROM feature_pets WHERE feature_id=?",
                       (fid,)).fetchone()[0]
        total += n
    print("  涉及 %d 个精灵条目" % total)

    # 保存结果
    out = os.path.join(ROOT, "logs", "trait_name_fix.json")
    io.open(out, "w", encoding="utf-8", newline="\n").write(
        json.dumps([{"id": f, "old": o, "new": n, "desc": d}
                    for f, o, n, d in fixed], ensure_ascii=False, indent=1))
    print("\n已写入 %s" % out)
    db.close()


if __name__ == "__main__":
    main()
