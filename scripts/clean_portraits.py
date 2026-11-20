# -*- coding: utf-8 -*-
"""clean_portraits.py —— 清理待标注的头像截图。

背景
----
`robot._check_active_pet` / `_check_foe` 里，认不出的精灵**每 10 秒存一张**
（头像 + 名字条各一份）。11-19 连续跑了 20 小时，于是：

    assets/portraits      4669 张待标注（真正标注好的只有 18 张）
    assets/portraits_foe  3430 张待标注（真正标注好的只有 71 张）

实测重复度：前 600 张只对应 176 只不同精灵，最多的一只被存了 55 次。

这些文件对识别**没有任何作用** —— 识别只用已标注的模板（`portrait` 表里
169 条 / 磁盘上 89 张）。待标注的只是"待办事项"，而自动标注
（`label_portraits.py`）已经覆盖了能自动做的部分。

用法
----
    python scripts\\clean_portraits.py            # 只统计，不删
    python scripts\\clean_portraits.py --apply    # 真删
    python scripts\\clean_portraits.py --apply --keep 3   # 每只保留 3 张
"""
from __future__ import annotations

import os
import sys

ROOT = r"D:\Projects\RocoAgent"
DIRS = [
    os.path.join(ROOT, "assets", "portraits"),
    os.path.join(ROOT, "assets", "portraits_foe"),
]


def scan(d):
    if not os.path.isdir(d):
        return None
    bust, name, named = [], [], []
    for f in os.listdir(d):
        if not f.endswith(".png"):
            continue
        p = os.path.join(d, f)
        if f.startswith("_unknown_"):
            (name if "_name" in f else bust).append(p)
        else:
            named.append(p)
    return bust, name, named


def main():
    apply = "--apply" in sys.argv
    keep = 3
    if "--keep" in sys.argv:
        try:
            keep = int(sys.argv[sys.argv.index("--keep") + 1])
        except (ValueError, IndexError):
            pass

    total_del = 0
    total_bytes = 0

    for d in DIRS:
        r = scan(d)
        if r is None:
            print("跳过（不存在）:", d)
            continue
        bust, name, named = r
        unk = bust + name
        size = sum(os.path.getsize(p) for p in unk) if unk else 0
        print("\n=== %s ===" % os.path.relpath(d, ROOT))
        print("  已标注（保留）: %d 张" % len(named))
        print("  待标注头像    : %d 张" % len(bust))
        print("  待标注名字条  : %d 张" % len(name))
        print("  待标注合计    : %d 张, %.0f MB"
              % (len(unk), size / 1024.0 / 1024.0))

        # 按感知哈希聚类，每类留 keep 张
        if unk and keep > 0:
            import cv2
            import numpy as np

            def dhash(p):
                im = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
                if im is None or im.size == 0:
                    return None
                im = cv2.resize(im, (9, 8))
                return (im[:, 1:] > im[:, :-1]).flatten()

            clusters = []
            for p in bust:
                h = dhash(p)
                if h is None:
                    continue
                for c in clusters:
                    if np.sum(c["h"] != h) < 8:
                        c["files"].append(p)
                        break
                else:
                    clusters.append({"h": h, "files": [p]})
            keep_set = set()
            for c in clusters:
                for p in c["files"][:keep]:
                    keep_set.add(p)
            drop_bust = [p for p in bust if p not in keep_set]
            # 名字条跟随头像：没有对应头像的名字条一律删
            keep_stems = {os.path.basename(p)[:-4] for p in keep_set}
            drop_name = [p for p in name
                         if os.path.basename(p)[:-9] not in keep_stems]
            drop = drop_bust + drop_name
            print("  聚类后不同精灵: %d 只 -> 保留 %d 张头像"
                  % (len(clusters), len(keep_set)))
        else:
            drop = unk

        dsize = sum(os.path.getsize(p) for p in drop) if drop else 0
        print("  将删除        : %d 张, %.0f MB"
              % (len(drop), dsize / 1024.0 / 1024.0))
        total_del += len(drop)
        total_bytes += dsize

        if apply and drop:
            n = 0
            for p in drop:
                try:
                    os.remove(p)
                    n += 1
                except OSError:
                    pass
            print("  已删除 %d 张" % n)

    print("\n%s: %d 张, %.0f MB"
          % ("已删除" if apply else "待删除", total_del,
             total_bytes / 1024.0 / 1024.0))
    if not apply:
        print("\n（dry run，加 --apply 执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
