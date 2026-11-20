# -*- coding: utf-8 -*-
"""校验 roco-cal 的 18x18 克制表与我们库里的是否等价（按属性名对齐）。

roco-cal 的 Type 枚举顺序和我们不同，直接按 id 对比会全错。
必须按名字对齐后再比矩阵。
"""
from __future__ import annotations

import io
import os
import re
import sqlite3
import sys

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402

CAL_ELE = r"D:\GameData\洛克王国世界\roco-cal-latest\src-tauri\resources\rocodatebase\core\ele_advantage.py"


def parse_cal():
    """从 ele_advantage.py 解析 Type 枚举顺序和 _CHART 矩阵。"""
    t = io.open(CAL_ELE, encoding="utf-8", errors="replace").read()
    # Type 枚举：普通 = 0;  草 = 1; ...
    order = {}
    for m in re.finditer(r"(\S+?)\s*=\s*(\d+)", t.split("_CHART")[0]):
        name, idx = m.group(1), int(m.group(2))
        if re.match(r"^[\u4e00-\u9fff]+$", name):
            order[idx] = name
    # _CHART：找到第一个 [ 到匹配的 ]
    i = t.index("_CHART")
    start = t.index("[", i)
    depth = 0
    for j in range(start, len(t)):
        if t[j] == "[":
            depth += 1
        elif t[j] == "]":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    body = t[start:end]
    rows = re.findall(r"\[([^\[\]]+)\]", body)
    chart = []
    for r in rows:
        vals = [float(x.strip()) for x in r.split(",") if x.strip()]
        if vals:
            chart.append(vals)
    return order, chart


def main():
    order, chart = parse_cal()
    print("roco-cal Type 顺序 (%d 项):" % len(order))
    for i in sorted(order):
        print("  %2d %s" % (i, order[i]))
    print("\n克制矩阵: %d x %d" % (len(chart), len(chart[0]) if chart else 0))

    # 我们库
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    ours_order = {r["id"]: r["name"] for r in db.execute("SELECT id,name FROM types")}
    ours = {}
    for r in db.execute("SELECT atk,def,mult FROM type_eff"):
        ours[(r["atk"], r["def"])] = r["mult"]

    print("\n我们库 Type 顺序 (%d 项):" % len(ours_order))
    for i in sorted(ours_order):
        print("  %2d %s" % (i, ours_order[i]))

    # 按名字对齐后比对
    print("\n=== 按名字对齐后比对克制矩阵 ===")
    same = diff = 0
    diffs = []
    for ai, aname in order.items():
        for di, dname in order.items():
            cal_v = chart[ai][di]
            # 找我们库里的 id
            our_a = next((k for k, v in ours_order.items() if v == aname), None)
            our_d = next((k for k, v in ours_order.items() if v == dname), None)
            if our_a is None or our_d is None:
                continue
            our_v = ours.get((our_a, our_d))
            if our_v is None:
                continue
            if abs(cal_v - our_v) < 1e-6:
                same += 1
            else:
                diff += 1
                diffs.append((aname, dname, cal_v, our_v))

    print("  一致 %d / 不一致 %d" % (same, diff))
    if diffs:
        print("\n  差异（前 20）:")
        for a, d, cv, ov in diffs[:20]:
            print("    %-4s -> %-4s  roco-cal=%.2f  我们=%.2f" % (a, d, cv, ov))
    db.close()


if __name__ == "__main__":
    main()
