# -*- coding: utf-8 -*-
"""prune_portrait_db.py —— 清掉 portrait 表里指向不存在文件的记录。

背景
----
`portrait` 表有 169 条，但磁盘上只有 89 个文件。剩下 80 条是历史遗留：
文件早被删了（或从未提交），记录还在。识别时会反复尝试加载不存在的文件。

用法
----
    python scripts\\prune_portrait_db.py            # 只列出
    python scripts\\prune_portrait_db.py --apply    # 删除
"""
from __future__ import annotations

import os
import sqlite3
import sys

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402

DIRS = {"self": os.path.join(ROOT, "assets", "portraits"),
        "foe": os.path.join(ROOT, "assets", "portraits_foe")}


def main():
    apply = "--apply" in sys.argv
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row

    rows = db.execute("SELECT id, file, side FROM portrait").fetchall()
    missing = []
    for r in rows:
        d = DIRS.get(r["side"], DIRS["self"])
        if not os.path.exists(os.path.join(d, r["file"])):
            missing.append(r)

    print("portrait 表共 %d 条，其中 %d 条文件不存在" % (len(rows), len(missing)))
    for r in missing[:20]:
        print("  id=%-4d %-28s side=%s" % (r["id"], r["file"], r["side"]))
    if len(missing) > 20:
        print("  ... 共 %d 条" % len(missing))

    if apply and missing:
        ids = [r["id"] for r in missing]
        db.executemany("DELETE FROM portrait WHERE id=?", [(i,) for i in ids])
        db.commit()
        print("\n已删除 %d 条记录" % len(ids))
        left = db.execute("SELECT COUNT(*) FROM portrait").fetchone()[0]
        print("剩余 %d 条" % left)
    elif not apply:
        print("\n（dry run，加 --apply 执行）")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
