# -*- coding: utf-8 -*-
"""purge_unknown_portraits.py —— 删掉全部待标注头像截图。

比 clean_portraits.py 更彻底：**不做聚类、不留样本**，只保留已标注的模板。

理由
----
- 识别只用已标注模板（`portrait` 表 169 条 / 磁盘 89 张）
- 待标注文件只是"待办"，自动标注（`label_portraits.py`）已覆盖能做的部分
- 聚类阈值敏感（4->697 组 / 8->303 / 12->204），留样本反而制造噪音
- 实测 8099 张待标注里，前 600 张只对应 176 只精灵，最多一只被存 55 次

用法
----
    python scripts\\purge_unknown_portraits.py            # 统计
    python scripts\\purge_unknown_portraits.py --apply    # 执行
"""
from __future__ import annotations

import os
import sys

ROOT = r"D:\Projects\RocoAgent"
DIRS = [
    os.path.join(ROOT, "assets", "portraits"),
    os.path.join(ROOT, "assets", "portraits_foe"),
]


def main():
    apply = "--apply" in sys.argv
    grand_n = grand_b = 0

    for d in DIRS:
        if not os.path.isdir(d):
            print("跳过（不存在）:", d)
            continue
        unk, named = [], []
        for f in os.listdir(d):
            if not f.endswith(".png"):
                continue
            p = os.path.join(d, f)
            (unk if f.startswith("_unknown_") else named).append(p)

        size = sum(os.path.getsize(p) for p in unk) if unk else 0
        print("\n=== %s ===" % os.path.relpath(d, ROOT))
        print("  已标注（保留）: %d 张" % len(named))
        print("  待标注（删除）: %d 张, %.0f MB"
              % (len(unk), size / 1024.0 / 1024.0))

        grand_n += len(unk)
        grand_b += size

        if apply and unk:
            n = 0
            for p in unk:
                try:
                    os.remove(p)
                    n += 1
                except OSError:
                    pass
            print("  已删除 %d 张" % n)

    print("\n%s: %d 张, %.0f MB"
          % ("已删除" if apply else "待删除", grand_n, grand_b / 1024.0 / 1024.0))
    if not apply:
        print("\n（dry run，加 --apply 执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
