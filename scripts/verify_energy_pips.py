# -*- coding: utf-8 -*-
"""验证：能量点亮数（0~6）与实际能量值（0~10）的对应关系。

背景
----
energy.read_energy() 返回的是**点亮数 0~6**，但 policy_v2 把它当能量值用：
    energy_after = max(0, energy - cost)      # cost 是真实耗能 0~6
    _c01(energy_after / 10.0)                 # 假设满值 10

量纲不一致。要修之前必须先确认换算关系。

方法
----
从 turns.jsonl 找连续回合：
    T_n  energy_pips = A，打出 cost=C 的技能
    T_n+1 energy_pips = B
如果线性换算成立，应该有 (A - B) ≈ C / (10/6) = C * 0.6

跑法：
    python scripts\\verify_energy_pips.py
"""
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "src")

TURNS = os.path.join("logs", "turns.jsonl")


def load():
    out = []
    for line in open(TURNS, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    rows = load()
    print("turns.jsonl %d 条" % len(rows))

    # 只取有 energy 和 action 的记录
    have = [r for r in rows if r.get("energy") is not None]
    print("有 energy 字段: %d 条" % len(have))
    if not have:
        print("没有 energy 数据，无法验证")
        return 0

    # energy 的取值分布（判断是 0~6 还是 0~10）
    print()
    print("=== energy 取值分布 ===")
    c = Counter(r.get("energy") for r in have)
    for k in sorted(c, key=lambda x: (x is None, x)):
        print("  %-5s %d 次" % (k, c[k]))
    vals = [v for v in c if isinstance(v, int)]
    if vals:
        print()
        print("  最小值 %d，最大值 %d" % (min(vals), max(vals)))
        if max(vals) <= 6:
            print("  -> 确认是【点亮数 0~6】，不是能量值 0~10")
        elif max(vals) >= 10:
            print("  -> 看起来是【能量值 0~10】")
        else:
            print("  -> 介于两者之间，需要更多数据")

    # 找连续回合：同一 pet，相邻 ts，看 pips 变化 vs 打出的技能耗能
    print()
    print("=== 连续回合的 pips 变化 vs 技能耗能 ===")

    # 需要技能耗能表
    import sqlite3
    import paths
    import team_db
    db = sqlite3.connect(str(paths.DB))
    costs = {}
    try:
        team = {t.get("name"): t for t in team_db.dump_team() if t.get("name")}
        for name, t in team.items():
            moves = t.get("moves") or []
            arr = []
            for sid in moves[:4]:
                r = db.execute("SELECT cost FROM skills WHERE id=?",
                               (int(sid),)).fetchone()
                arr.append(r[0] if r else None)
            costs[name] = arr
    finally:
        db.close()

    print("  队伍耗能表:")
    for name, arr in costs.items():
        print("    %-10s %s" % (name, arr))

    # 按 pet 分组，按 ts 排序，找相邻两条
    by_pet = defaultdict(list)
    for r in have:
        pet = r.get("our_pet")
        if pet:
            by_pet[pet].append(r)

    deltas = []
    for pet, rs in by_pet.items():
        rs.sort(key=lambda r: r.get("ts") or "")
        for a, b in zip(rs, rs[1:]):
            # 必须同回合连续（turn 差 1）且是同一种操作
            ta, tb = a.get("turn"), b.get("turn")
            if ta is None or tb is None or tb - ta != 1:
                continue
            ea, eb = a.get("energy"), b.get("energy")
            if ea is None or eb is None:
                continue
            act = a.get("action") or []
            if len(act) != 2 or act[0] != "skill":
                continue
            slot = act[1]
            arr = costs.get(pet)
            if not arr or slot > len(arr):
                continue
            cost = arr[slot - 1]
            if cost is None:
                continue
            deltas.append((ea, eb, ea - eb, cost, pet, ta))

    print()
    print("  找到 %d 组相邻回合" % len(deltas))
    if deltas:
        print("  pips变化  技能耗能   次数")
        agg = Counter()
        for ea, eb, d, cost, pet, turn in deltas:
            agg[(d, cost)] += 1
        for (d, cost), n in sorted(agg.items(), key=lambda x: -x[1])[:15]:
            ratio = ("%.2f" % (d / cost)) if cost else "-"
            print("    %-8s %-10s %-5d  (d/cost=%s)" % (d, cost, n, ratio))

        # 统计 d/cost 的分布
        ratios = [d / cost for ea, eb, d, cost, pet, turn in deltas
                  if cost and d > 0]
        if ratios:
            ratios.sort()
            mid = ratios[len(ratios) // 2]
            print()
            print("  d/cost 中位数 = %.2f" % mid)
            print("  如果线性换算（6 点 = 10 能量），应为 0.6")
            if 0.4 <= mid <= 0.8:
                print("  -> 接近 0.6，线性换算基本成立")
            else:
                print("  -> 偏离较大，不能简单线性换算")

    return 0


if __name__ == "__main__":
    sys.exit(main())
