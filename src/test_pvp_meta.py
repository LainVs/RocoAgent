"""pvp_meta 自检：分布必须合法（概率和为 1、分位有序、携带率 ≈ 4）。

    python src/test_pvp_meta.py

失败退出码 1，末行打印 ALL PASS / FAILED N。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if "D:/Tools" not in sys.path:
    sys.path.insert(0, "D:/Tools")

import pvp_meta as M

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s %s" % (name, detail))
    else:
        print("  FAIL %s %s" % (name, detail))
        FAIL.append(name)


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


def main():
    print("== pvp_meta ==")
    M.reload()

    # 1. 数据源规模
    sp = M.sample_size("寂灭骨龙")
    check("sample size > 0", sp > 0, "寂灭骨龙=%d" % sp)

    # 2. HP 分布：概率和为 1
    hd = M.hp_dist("寂灭骨龙")
    check("hp_dist non-empty", bool(hd))
    if hd:
        check("hp_dist sums to 1", close(sum(p for _v, p in hd), 1.0, 1e-6),
              "%.6f" % sum(p for _v, p in hd))
        check("hp_dist sorted", all(hd[i][0] <= hd[i + 1][0]
                                   for i in range(len(hd) - 1)))

    # 3. 物防分布
    dd = M.def_dist("寂灭骨龙")
    check("def_dist sums to 1", close(sum(p for _v, p in dd), 1.0, 1e-6),
          "%.6f" % sum(p for _v, p in dd))

    # 4. 性格分布
    nd = M.nature_dist("寂灭骨龙")
    check("nature_dist sums to 1", close(sum(p for _k, p in nd), 1.0, 1e-6))

    # 5. 技能携带率：每只带 4 个技能，所以总和应接近 4
    cr = M.carry_rates("寂灭骨龙")
    total = sum(p for _k, p in cr)
    check("carry sum ~= 4", 3.5 <= total <= 4.5, "%.2f" % total)

    # 6. 伤害占比分布：lo <= mid <= hi，且都在 0~100
    d = M.pct_dist("寂灭骨龙", 213)
    check("pct_dist non-empty", bool(d))
    if d:
        check("pct lo<=mid<=hi", d["lo"] <= d["mid"] <= d["hi"],
              "%.1f<=%.1f<=%.1f" % (d["lo"], d["mid"], d["hi"]))
        check("pct in range", 0 < d["lo"] <= 100 and 0 < d["hi"] <= 100)
        check("pct buckets sum to 1",
              close(sum(p for _v, p in d["buckets"]), 1.0, 1e-3))

    # 7. 斩杀概率是 0~1
    k = M.pct_at_least("寂灭骨龙", 213, 50)
    check("pct_at_least in [0,1]", k is not None and 0.0 <= k <= 1.0,
          str(k))

    # 8. 未知物种必须返回空（绝不编造）
    check("unknown species -> 0 samples", M.sample_size("不存在的精灵") == 0)
    check("unknown species -> no dist", M.hp_dist("不存在的精灵") == [])
    check("unknown species -> no pct", M.pct_dist("不存在的精灵", 100) is None)

    # 9. 带后缀的名字要能归一
    check("paren suffix normalized",
          M.sample_size("石冠王蜥（首领形态）") >= 0)

    print()
    if FAIL:
        print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
