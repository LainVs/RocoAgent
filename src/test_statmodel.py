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

    # ---- 四舍五入（2026-11-21 修）
    #
    # 旧代码用 Python 的 round()，那是银行家舍入：round(416.5)=416。
    # 游戏是四舍五入：416.5 -> 417。用 my_team 里的实测面板做断言，
    # 这 5 个值是从游戏界面上读下来的，不是算出来的。
    print("\n四舍五入（对照 my_team 实测面板）")
    half_up_cases = [
        # (说明, 种族值, iv, 性格倍率, 是否HP, 实测值)
        ("冥古龙 HP",   115, 60, 1.0, True,  417),
        ("恶魔狼 HP",   115, 60, 1.0, True,  417),
        ("卡瓦重 魔攻",  14,  0, 0.9, False,  73),
        ("音速犬 HP",    85, 60, 1.0, True,  366),
        ("冰钻布鲁斯 HP", 73, 60, 1.0, True,  345),
        ("卡瓦重 HP",    77, 60, 1.0, True,  352),
        ("银月狼王 HP", 115, 60, 1.0, True,  417),
    ]
    for label, sv, iv, nat, is_hp, want in half_up_cases:
        got = S.calc_real_stat(sv, iv, nat, is_hp)
        ok = got == want
        bad += 0 if ok else 1
        print("  %-14s 模型 %4d  实测 %4d  %s"
              % (label, got, want, "OK" if ok else "**差 %+d**" % (want - got)))
    assert bad == 0, "舍入或面板仍有 %d 项对不上" % bad

    # round_half_up 本身的行为（负数和 .5 边界）
    assert S.round_half_up(416.5) == 417
    assert S.round_half_up(417.5) == 418
    assert S.round_half_up(0.5) == 1
    assert S.round_half_up(1.5) == 2
    assert S.round_half_up(2.5) == 3        # 银行家舍入会给 2
    assert S.round_half_up(-0.5) == -1

    # 伤害公式：单一 floor
    assert S.damage(300, 175, 105, stab=1.25) == int(
        (300 / 175) * (37 / 41) * 105 * 1.25)
    # 星陨威力
    assert S.star_power(3) == 3 * 3 + 24 * 3 - 24
    assert S.star_power(0) == -24

    # 强化等级倍率：官方 (2+n)/2（+1 = 1.5，-1 = 2/3）。
    # 2026-11-22 修正：这里原来断言 1.1 / 1/1.1，那是移植时带进来的错公式；
    # 证据见 statmodel.level_mult 的 docstring（PVP助手 dl() + 官方百科）。
    assert abs(S.level_mult(1) - 1.5) < 1e-9
    assert abs(S.level_mult(-1) - 2 / 3.0) < 1e-9

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
