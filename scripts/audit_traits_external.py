# -*- coding: utf-8 -*-
"""用外部数据源交叉校验我们库里的"特性 -> 精灵"映射。

数据源（本地，不需要联网）：
    D:\\GameData\\洛克王国世界\\RokePvP-Calculator\\data\\spirits.js
        每只精灵带 tr（特性名）+ tr_desc（特性描述），社区维护

对比我们库：
    features 表 + feature_pets 表（来自 innate_skills.json 的 feature_skill_ids）
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import sys

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402

EXT = r"D:\GameData\洛克王国世界\RokePvP-Calculator\data\spirits.js"


def load_external():
    """spirits.js -> {name: (trait, desc)}"""
    t = io.open(EXT, encoding="utf-8", errors="replace").read()
    i = t.index("[")
    j = t.rindex("]")
    arr = json.loads(t[i:j + 1])
    out = {}
    for e in arr:
        n = e.get("n")
        if n:
            out[n] = (e.get("tr") or "", e.get("tr_desc") or "")
    return out


def load_ours():
    """{name: [(trait, is_primary)]}"""
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    out = {}
    try:
        rows = db.execute(
            "SELECT sp.name AS sname, f.name AS fname, fp.is_primary "
            "FROM feature_pets fp "
            "JOIN species sp ON sp.id=fp.species_id "
            "JOIN features f ON f.id=fp.feature_id").fetchall()
        for r in rows:
            out.setdefault(r["sname"], []).append(
                (r["fname"], r["is_primary"]))
    finally:
        db.close()
    return out


def main():
    ext = load_external()
    ours = load_ours()
    print("外部 spirits.js: %d 只" % len(ext))
    print("我们库 feature_pets: %d 只" % len(ours))

    both = sorted(set(ext) & set(ours))
    print("两边都有: %d 只\n" % len(both))

    same = diff = 0
    diffs = []
    for name in both:
        e_tr = ext[name][0]
        o_trs = [t for t, p in ours[name] if p] or [t for t, _p in ours[name]]
        o_tr = o_trs[0] if o_trs else ""
        if e_tr == o_tr:
            same += 1
        else:
            diff += 1
            diffs.append((name, o_tr, e_tr, ext[name][1][:30]))

    print("=== 特性一致: %d / 不一致: %d ===" % (same, diff))
    print("\n不一致样例（前 40）:")
    print("%-14s %-14s %-14s %s" % ("精灵", "我们的", "外部的", "外部描述"))
    print("-" * 78)
    for name, o, e, d in diffs[:40]:
        print("%-14s %-14s %-14s %s" % (name, o or "(无)", e or "(无)", d))

    # 重点：共鸣
    print("\n=== 我们库里标成「共鸣」的精灵，外部实际是什么 ===")
    hit = 0
    for name in sorted(ours):
        trs = [t for t, _p in ours[name]]
        if "共鸣" in trs:
            hit += 1
            e = ext.get(name)
            print("  %-14s 外部=%-14s %s"
                  % (name, (e[0] if e else "(外部无这只)"),
                     (e[1][:40] if e else "")))
    print("  => 我们库里 %d 只标成共鸣" % hit)


if __name__ == "__main__":
    main()
