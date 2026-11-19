# -*- coding: utf-8 -*-
"""伤害公式标定：用截图里 4 张卡的伤害数字验证模型。

数据（用户截图，恶魔狼 417/417 vs 圣剑-X 84%）：
    吞噬      普通  威力150  卡面142
    火焰切割  火    威力100  卡面380
    蝙蝠      恶    威力 65  卡面154
    有效预防  普通  威力 0    卡面 —

battle_config 给出的倍率：
    单倍克制 1.00 / 双倍克制 2.00 / 单倍被克 0.50 / 双倍被克 0.75
"""
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), *([".."] * 0), "src"))
import paths
import json
import os

BASE = str(paths.GAME_DATA)
OUT = os.path.join(os.environ["TEMP"], "oc_fit.txt")

CARDS = [
    # name, type_id, power, shown
    ("吞噬", 0, 150, 142),
    ("火焰切割", 1, 100, 380),
    ("蝙蝠", 16, 65, 154),
]

MULT_SET = [0.5, 0.75, 1.0, 2.0]


def load(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return json.load(f)


def fit():
    out = []
    p = lambda *a: out.append(" ".join(str(x) for x in a))
    sk = load("skill_map.json")
    p("skill_type_id check:")
    for sid in ("7020590", "7040390", "7180090", "7021110"):
        s = sk[sid]
        p("  %-8s %-6s skill_type_id=%s dam_para=%s energy=%s" % (
            sid, s["name"], s.get("skill_type_id"), s.get("dam_para"), s.get("energy_cost")))

    # 假设：D = power * k * stab * mult，stab 待定
    p("\n== 用 (吞噬, 火焰切割) 解 k 与倍率，需要假设对手属性 ==")
    p("设 mult_normal = 0.5（对手抗普通）、mult_fire = 2.0（对手怕火）：")
    k1 = 142 / (150 * 0.5)
    k2 = 380 / (100 * 2.0)
    p("  k(吞噬) = %.4f   k(火焰切割) = %.4f" % (k1, k2))

    k = (k1 + k2) / 2
    p("  k 取 %.4f" % k)
    p("\n== 反推 蝙蝠 的 STAB/倍率 ==")
    for stab in (1.0, 1.2, 1.5):
        m = 154 / (65 * k * stab)
        p("  stab=%.1f -> 需要 mult=%.3f  (最接近 %s)" % (
            stab, m, min(MULT_SET, key=lambda x: abs(x - m))))
    p("\n== 反推 卡面 ==")
    for stab in (1.2,):
        for name, tid, power, shown in CARDS:
            mult = shown / (power * k * stab) if power else 0
            p("  %-8s power=%-4d stab=%.1f -> mult=%.3f shown=%d" % (
                name, power, stab, mult, shown))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


if __name__ == "__main__":
    fit()
    print("ok")
