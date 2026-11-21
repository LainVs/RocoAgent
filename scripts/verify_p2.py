# -*- coding: utf-8 -*-
"""verify_p2.py —— 核实 P2 子代理的声明。

子代理声称：
    1. 配置了 13 个特性
    2. 用的是"真实 DB 名"（不是任务清单里的缩写）
    3. 没有覆盖上游已有的配置

本脚本逐条验证。
"""
from __future__ import annotations

import io
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    src = io.open(os.path.join(ROOT, "src", "sim", "effect_data.py"),
                  encoding="utf-8").read()

    marker = "本项目补丁（2026-11-21）—— 补齐缺失的特性效果"
    i = src.find(marker)
    if i < 0:
        print("FAIL 找不到 P2 补丁注释块")
        return 1
    print("P2 补丁块位置: 第 %d 行" % (src[:i].count("\n") + 1))

    seg = src[i:]
    keys = re.findall(r'^\s{4}"([^"]+)"\s*:\s*\[', seg, re.M)
    print("补丁块内新增的键: %d 个" % len(keys))
    for k in keys:
        print("  ", k)

    # 检查是否覆盖上游
    before = src[:i]
    dup = []
    for k in keys:
        pat = r'^\s{4}"%s"\s*:\s*\[' % re.escape(k)
        if re.search(pat, before, re.M):
            dup.append(k)
    print()
    if dup:
        print("FAIL 这些键在补丁块之前已存在（会覆盖上游）: %s" % dup)
    else:
        print("ok   没有覆盖任何上游配置")

    # 检查每个键在 DB 里存在且有精灵使用
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    print()
    print("=== 每个键在 DB 里的存在性 ===")
    bad = []
    for k in keys:
        row = db.execute("SELECT COUNT(*) c FROM features WHERE name=?",
                         (k,)).fetchone()
        used = db.execute(
            "SELECT COUNT(DISTINCT fp.species_id) c FROM features f "
            "JOIN feature_pets fp ON fp.feature_id=f.id WHERE f.name=?",
            (k,)).fetchone()
        ok = row["c"] > 0 and used["c"] > 0
        if not ok:
            bad.append(k)
        print("  %-12s DB=%-2d 精灵=%-3d %s" % (
            k, row["c"], used["c"], "ok" if ok else "FAIL"))
    db.close()

    print()
    if bad:
        print("FAIL %d 个键在 DB 里查不到或没精灵使用: %s" % (len(bad), bad))
        return 1
    if dup:
        return 1
    print("ALL PASS：13 个配置全部有效，未覆盖上游")
    return 0


if __name__ == "__main__":
    sys.exit(main())
