# -*- coding: utf-8 -*-
"""分析 59 个特性不一致的具体模式，判断是"我们错"还是"外部错"。"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402

EXT = r"D:\GameData\洛克王国世界\RokePvP-Calculator\data\spirits.js"
GAME = str(paths.GAME_DATA)


def main():
    t = io.open(EXT, encoding="utf-8", errors="replace").read()
    arr = json.loads(t[t.index("["):t.rindex("]") + 1])
    ext = {e["n"]: e for e in arr if e.get("n")}

    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    ours = defaultdict(list)
    for r in db.execute(
            "SELECT sp.name AS s, f.name AS f, f.desc AS d, fp.is_primary AS p "
            "FROM feature_pets fp JOIN species sp ON sp.id=fp.species_id "
            "JOIN features f ON f.id=fp.feature_id"):
        ours[r["s"]].append((r["f"], r["d"] or "", r["p"]))

    skill_map = json.load(io.open(os.path.join(GAME, "skill_map.json"),
                                  encoding="utf-8"))
    species = json.load(io.open(os.path.join(GAME, "pet_species.json"),
                                encoding="utf-8"))

    both = sorted(set(ext) & set(ours))
    diffs = []
    for n in both:
        e_tr = ext[n].get("tr") or ""
        o_trs = [x[0] for x in ours[n]]
        if e_tr and e_tr not in o_trs:
            diffs.append((n, o_trs, e_tr, ext[n].get("tr_desc") or "",
                          ext[n].get("a1"), ext[n].get("a2")))

    print("不一致 %d 只\n" % len(diffs))

    # 分类：我们的特性是不是"通用占位"
    generic = Counter()
    for n, o, e, d, a1, a2 in diffs:
        for x in o:
            generic[x] += 1
    print("=== 我们这边被重复使用的特性（可能是占位/错误）===")
    for k, v in generic.most_common(12):
        print("  %-16s 出现在 %d 只不一致的精灵上" % (k, v))

    print("\n=== 逐个（前 45）===")
    print("%-13s %-16s %-16s %s" % ("精灵", "我们的", "外部", "外部描述"))
    print("-" * 90)
    for n, o, e, d, a1, a2 in diffs[:45]:
        print("%-13s %-16s %-16s %s"
              % (n, "/".join(o)[:16], e[:16], d[:38]))

    # 外部描述里有没有"我们那个特性"的字样 -> 判断谁对
    print("\n=== 交叉判断：外部描述里是否提到我们的特性名 ===")
    agree_ext = 0
    for n, o, e, d, a1, a2 in diffs:
        if any(x and x in d for x in o):
            agree_ext += 1
    print("  外部描述含我们特性名的: %d / %d" % (agree_ext, len(diffs)))

    # 游戏原始 desc 对比
    print("\n=== 用游戏 skill_map 的 desc 校验外部描述 ===")
    hit = 0
    for n, o, e, d, a1, a2 in diffs[:15]:
        # 找外部特性名在 skill_map 里的 desc
        gd = None
        for k, v in skill_map.items():
            if v.get("name") == e:
                gd = v.get("desc") or ""
                break
        ok = "?" if gd is None else ("一致" if gd[:12] in d or d[:12] in gd else "不同")
        if ok == "一致":
            hit += 1
        print("  %-12s 外部=%-12s 游戏desc=%s" % (n, e, (gd or "(无此特性)")[:40]))
    print("  => 外部描述与游戏 desc 一致: %d / 15" % hit)
    db.close()


if __name__ == "__main__":
    main()
