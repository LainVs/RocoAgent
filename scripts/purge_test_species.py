# -*- coding: utf-8 -*-
"""purge_test_species.py —— 清掉库里的测试/占位精灵。

为什么
------
实测（2026-11-21 04:50）：特性「氧循环」有 9 个候选，
其中 4 个是测试数据（测试喵喵1/2、被克制测试喵喵、抵抗测试喵喵），
它们的属性（草/冰/龙）与真实精灵（草）不同，把 trait_db 的
best_conf 从 high 拉成 low，导致：
    enough=False -> 面板扫描又跑整块 OCR -> 19.6 秒 -> 吃掉一个回合

清理范围（全部 implemented=0，从没在实盘出现过）：
    测试*（16 只）、大世界BUFF*（3 只）
    以及其他明显的占位模板

用法
----
    python scripts\\purge_test_species.py            # 预览
    python scripts\\purge_test_species.py --apply    # 执行
"""
from __future__ import annotations

import os
import sqlite3
import sys

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402

# 名字里含这些词且 implemented=0 的一律清掉
PATTERNS = ["测试", "大世界BUFF", "占位", "模板"]
# 额外白名单（精确匹配）
EXTRA = ["60级水桶模板"]


def main():
    apply = "--apply" in sys.argv
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row

    hits = []
    for pat in PATTERNS:
        for r in db.execute(
                "SELECT id,name,implemented FROM species "
                "WHERE name LIKE ? AND implemented=0", ("%" + pat + "%",)):
            hits.append((r["id"], r["name"], r["implemented"]))
    for nm in EXTRA:
        for r in db.execute(
                "SELECT id,name,implemented FROM species "
                "WHERE name=? AND implemented=0", (nm,)):
            hits.append((r["id"], r["name"], r["implemented"]))

    # 去重
    seen = set()
    uniq = []
    for i, n, im in hits:
        if i in seen:
            continue
        seen.add(i)
        uniq.append((i, n, im))

    print("待清理: %d 只" % len(uniq))
    for i, n, im in uniq:
        print("  id=%-8d %-22s implemented=%s" % (i, n, im))

    if not apply:
        print("\n（预览，加 --apply 执行）")
        db.close()
        return 0

    print("\n=== 执行 ===")
    n = 0
    for i, _n, _im in uniq:
        db.execute("DELETE FROM feature_pets WHERE species_id=?", (i,))
        db.execute("DELETE FROM species_skills WHERE species_id=?", (i,))
        db.execute("DELETE FROM portrait WHERE species_id=?", (i,))
        db.execute("DELETE FROM species WHERE id=?", (i,))
        n += 1
    db.commit()
    print("已清理 %d 只" % n)
    left = db.execute("SELECT COUNT(*) FROM species").fetchone()[0]
    print("库内精灵: %d 只" % left)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
