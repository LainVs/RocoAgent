# -*- coding: utf-8 -*-
"""验证"特性 -> 身份/属性"的完整链路。

覆盖三类场景：
  1. 特性唯一 -> 身份和属性都确定
  2. 多候选同名 -> 身份确定（库里重复条目）
  3. 多候选不同名 -> 取默认值 + 标记低置信 + 写待修正清单
"""
import io
import json
import os
import sys

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))

import trait_db          # noqa: E402
import sqlite3           # noqa: E402
import paths             # noqa: E402


class FakeBot:
    """只借用 robot 的两个方法，不实例化整个 Bot（会连硬件）。"""

    def __init__(self):
        import robot
        self._name_from_trait = robot.Bot._name_from_trait.__get__(self)
        self._log_trait_guess = robot.Bot._log_trait_guess.__get__(self)


def resolve_all(trait):
    r = trait_db.resolve(trait)
    return {
        "trait": trait,
        "strict": r["types"],
        "best": r["best_types"],
        "conf": r["best_conf"],
        "how": r["best_how"],
        "species": r["best_species"],
        "cands": r["candidates"],
    }


def main():
    db = sqlite3.connect(str(paths.DB))
    bot = FakeBot()

    cases = ["溶解腐蚀", "电流刺激", "做噩梦", "共鸣", "恶魔的晚宴",
             "悲悯", "上锁", "加个雪球"]
    print("%-10s %-18s %-18s %-8s %-11s %s"
          % ("特性", "严格属性", "默认属性", "置信", "来源", "身份"))
    print("-" * 88)
    for t in cases:
        d = resolve_all(t)
        data = {"ability": t, "trait_candidates": d["cands"],
                "best_species": d["species"], "best_conf": d["conf"]}
        name = bot._name_from_trait(data)
        print("%-10s %-18s %-18s %-8s %-11s %s"
              % (t, str(d["strict"]), str(d["best"]), d["conf"],
                 d["how"] or "-", name or "(未定)"))

    print("\n=== 统计：283 个特性的覆盖 ===")
    feats = [r[0] for r in db.execute(
        "SELECT DISTINCT name FROM features WHERE name IS NOT NULL")]
    stat = {"strict": 0, "best_only": 0, "none": 0}
    confs = {}
    for f in feats:
        r = trait_db.resolve(f)
        if r["types"]:
            stat["strict"] += 1
        elif r["best_types"]:
            stat["best_only"] += 1
            confs[r["best_conf"]] = confs.get(r["best_conf"], 0) + 1
        else:
            stat["none"] += 1
    print("  严格可定属性: %d" % stat["strict"])
    print("  仅默认猜测  : %d  (置信分布 %s)" % (stat["best_only"], confs))
    print("  完全无候选  : %d" % stat["none"])
    print("  => 能算出属性的比例: %.1f%%"
          % (100.0 * (stat["strict"] + stat["best_only"]) / len(feats)))
    db.close()


if __name__ == "__main__":
    main()
