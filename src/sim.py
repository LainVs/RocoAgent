# -*- coding: utf-8 -*-
"""sim.py —— 最小战斗模拟器（第一步：伤害内核 + 标定）。

为什么先做这一块：
    模拟器的每个分支最终都是"这一手打多少血"。伤害算错，TKO、LP、自对弈
    全部建立在错的数上。所以先用真实卡面数字把公式标定到误差 0，再做引擎。

公式结构（来自 rocopvp 项目，我们用真实卡面数字重新标定系数）：

    等级系数 L      = (等级 × 45/100 + 10) / 41         60 级 = 0.9024
    基础伤害        = round(攻击 × 威力 × L) / 防御
    最终伤害        = 基础 × 本系 × 克制 × 随机 × 其他乘区

    本系 / 克制 / 其他 我们都能确定，
    唯一未知的是"攻击/防御"这两个值和 1.1/3×个体值 这类面板算法。

标定思路：
    同一场对局里，攻击方和防守方是固定的，所以
        k = 攻击 / 防御
    是一个常数。于是
        卡面伤害 = round(威力 × L × k) × 本系 × 克制
    用多张卡反解 k，如果所有卡解出的 k 一致 → 公式结构正确。
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LEVEL = 60


def level_coef(level=LEVEL):
    return (level * 45.0 / 100.0 + 10.0) / 41.0


def base_damage(power, k, level=LEVEL):
    """round(威力 × 等级系数 × k)，k = 攻击/防御。"""
    return int(round(power * level_coef(level) * k))


def damage(power, k, stab=1.0, mult=1.0, extra=1.0, level=LEVEL):
    """最终伤害（四舍五入到整数，与卡面一致）。"""
    return int(round(base_damage(power, k, level) * stab * mult * extra))


def solve_k(shown, power, stab=1.0, mult=1.0, extra=1.0, level=LEVEL):
    """由一张卡的伤害反解 k = 攻击/防御。"""
    denom = power * level_coef(level) * stab * mult * extra
    if denom <= 0:
        return None
    return shown / denom


def fit(samples, level=LEVEL):
    """多张卡一起解 k，返回 (k_mean, 相对离散度, 每张卡的 k)。

    samples: [(名称, 卡面伤害, 威力, 本系, 克制)]
    """
    ks = []
    for name, shown, power, stab, mult in samples:
        k = solve_k(shown, power, stab, mult, level=level)
        if k:
            ks.append((name, k))
    if not ks:
        return None, None, []
    vals = [k for _n, k in ks]
    mean = sum(vals) / len(vals)
    spread = (max(vals) - min(vals)) / mean if mean else 0.0
    return mean, spread, ks


def predict_all(samples, k, level=LEVEL):
    """用给定的 k 预测所有样本，返回 [(名称, 卡面, 预测, 误差%)]。"""
    out = []
    for name, shown, power, stab, mult in samples:
        pred = damage(power, k, stab, mult, level=level)
        err = (pred - shown) / float(shown) * 100.0 if shown else 0.0
        out.append((name, shown, pred, err))
    return out


# ---- 用户提供的真实样本（同一场对局：恶魔狼 vs 圣剑-X）-------------------
# (名称, 卡面伤害, 威力, 本系, 克制)
KNOWN = [
    ("吞噬", 142, 150, 1.0, 0.5),   # 普通技能，非本系，被恶系抵抗
    ("火焰切割", 380, 100, 1.0, 2.0),  # 火技能，非本系，克制恶系
    ("蝙蝠", 154, 65, 1.25, 1.0),   # 恶技能，本系，中性
]


if __name__ == "__main__":
    print("等级系数 L = %.4f" % level_coef())
    print("\n逐张反解 k：")
    mean, spread, ks = fit(KNOWN)
    for name, k in ks:
        print("  %-8s k = %.4f" % (name, k))
    print("\n均值 k = %.4f，相对离散 %.1f%%" % (mean, spread * 100))
    print("\n用均值预测：")
    bad = 0
    for name, shown, pred, err in predict_all(KNOWN, mean):
        ok = abs(err) < 1.0
        bad += 0 if ok else 1
        print("  %-8s 卡面 %3d  预测 %3d  误差 %+5.1f%%  %s"
              % (name, shown, pred, err, "OK" if ok else "**偏差**"))
    print("\n%s" % ("全部对上" if bad == 0 else
                    "%d 张对不上 —— 说明公式缺项（不是噪声，因为伤害是确定的）" % bad))
