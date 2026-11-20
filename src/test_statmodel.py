# -*- coding: utf-8 -*-
"""test_statmodel.py —— 数值模型回归（不需要游戏）

用 ARCHITECTURE.md 里的**四只实测血量**做验收：
    音速犬 366 / 冰钻布鲁斯 345 / 寂灭骨龙 490 / 巨鼓象 510

这四只以前被写成"公式不可信，必须实测"，其实是：
    前两只是「点满 HP + 无性格」，后两只是「点满 HP + 生命性格(+20%)」。
其中巨鼓象还牵出我们库里一个错值（hp=66，应为 130）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import statmodel as S


def main():
    # (名字, 种族值, 期望血量, 性格)
    cases = [
        ("音速犬", 85, 366, "neutral"),
        ("冰钻布鲁斯", 73, 345, "neutral"),
        ("寂灭骨龙", 120, 490, "沉默"),      # +生命 -物攻
        ("巨鼓象", 130, 510, "沉默"),
    ]
    print("%-12s %5s %6s %8s %6s" % ("species", "sv", "期望", "模型", "性格"))
    bad = 0
    for name, sv, want, nat in cases:
        got = S.calc_real_stat(sv, S.IV_PVP, S.nature_mult(nat)["hp"], True)
        ok = got == want
        bad += 0 if ok else 1
        print("%-12s %5d %6d %8d %6s  %s"
              % (name, sv, want, got, nat, "OK" if ok else "**偏差**"))
    assert bad == 0, "%d 只对不上" % bad

    # 伤害公式：单一 floor
    assert S.damage(300, 175, 105, stab=1.25) == int(
        (300 / 175) * (37 / 41) * 105 * 1.25)
    # 星陨威力
    assert S.star_power(3) == 3 * 3 + 24 * 3 - 24
    assert S.star_power(0) == -24

    # 等级系数：+1 → 1.1，-1 → 1/1.1
    assert abs(S.level_mult(1) - 1.1) < 1e-9
    assert abs(S.level_mult(-1) - 1 / 1.1) < 1e-9

    # 性格表：31 项，且 +20/-10
    nat = S.natures()
    assert len(nat) == 31, len(nat)
    g = nat["固执"]
    assert g["patk"] == 1.2 and g["matk"] == 0.9

    # 区间：点满 vs 不点，血量差要能体现
    lo, hi = S.hp_range({"hp": 85})
    assert lo < 366 < hi, (lo, hi)

    print("\nALL OK  （性格表 %d 项）" % len(nat))


if __name__ == "__main__":
    main()
