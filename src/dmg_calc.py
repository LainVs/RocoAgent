# -*- coding: utf-8 -*-
"""伤害模型：用卡片数字 + 游戏数据，反推属性倍率。

标定结果（用户截图，恶魔狼 vs 圣剑-X）：

    伤害 = 威力 × k × 本系加成 × 属性倍率

    k         ≈ 1.897   与攻击方的实际攻击值/对方的防御值有关，同一对局内是常数
    本系加成  = 1.2     （不是宝可梦的 1.5，由"蝙蝠 154"这张卡反推）
    属性倍率  ∈ {0.5, 0.75, 1.0, 2.0}
              0.5  单倍被克
              0.75 双倍被克
              1.0  普通
              2.0  双倍克制
              （battle_config: restrained 5000 / double_restrained 7500 /
                restraint 10000 / double_restraint 20000）

验证：
    吞噬      普通 150  -> 150×1.897×0.5        = 142.3  vs 卡面 142  ✓
    火焰切割  火   100  -> 100×1.897×2.0        = 379.4  vs 卡面 380  ✓
    蝙蝠      恶    65  -> 65×1.897×1.2×1.0     = 148.0  vs 卡面 154  (+4%)

用法：
    卡面数字是最准的（游戏自己算的），所以决策时优先用卡面；
    本模块用于三件事：
      1. 反推对手属性倍率（k 可以同时拟合）
      2. 预测换上来之后、别的精灵打这个对手大概多少（按攻击值缩放 k）
      3. 给对位数据库提供"理论值"，实际值/理论值 = 学习到的系数
"""
from __future__ import annotations

import itertools
import math

import rocodata

STAB = 1.2
# 伤害倍率的**最终**档位（不是"每个属性的倍率"）：
#   克制相加：被 1 个属性克 → ×2，被 2 个 → ×3（不是 ×4）
#   抵抗相乘：被 1 个抵抗 → ×0.5，被 2 个 → ×0.25
#   一克一抗 → ×1
# 三份独立实现一致：9178 计算器（getTypeEff）、roco-cal（ele_advantage.py）、
# loyshowdown 公式 v3。早先按 battle_config 的字段名猜成 (0.5,0.75,1,2) 是错的
# —— 那 4 个字段不是这个用途。
MULT_SET = (0.25, 0.5, 1.0, 2.0, 3.0)
DEFAULT_K = 1.897


def predict(power, k=DEFAULT_K, stab=1.0, mult=1.0):
    """预测伤害（四舍五入到整数，和游戏卡面一致）。"""
    return int(round(power * k * stab * mult))


def stab_of(attacker_types, skill_type_id):
    """本系加成：技能属性在攻击方属性列表里就是本系。"""
    return STAB if skill_type_id in (attacker_types or ()) else 1.0


def residual_mult(shown, power, stab=1.0, k=DEFAULT_K):
    """卡面伤害反推属性倍率。"""
    if power <= 0 or k <= 0:
        return None
    return shown / (power * k * stab)


def snap_mult(m):
    """把连续倍率吸附到游戏允许的四个档位。"""
    if m is None:
        return None
    return min(MULT_SET, key=lambda x: abs(x - m))


def fit_k(cards):
    """用一组 (卡面, 威力, 本系) 拟合 k。

    对每个可能的倍率组合，算 k 的方差，取方差最小的组合。
    4 张卡、4 个倍率档位 = 256 种组合，直接穷举。
    返回 (k, [(card, mult), ...], score)。
    """
    usable = [c for c in cards if c.get("power", 0) > 0 and c.get("shown")]
    if not usable:
        return None, [], None
    best = None
    for combo in itertools.product(MULT_SET, repeat=len(usable)):
        ks = []
        for c, m in zip(usable, combo):
            denom = c["power"] * c.get("stab", 1.0) * m
            if denom <= 0:
                ks.append(None)
            else:
                ks.append(c["shown"] / denom)
        ks_v = [x for x in ks if x]
        if not ks_v:
            continue
        mean = sum(ks_v) / len(ks_v)
        # k = 攻击/防御，PvP 实际范围大约 0.6~3.0。
        # 不加这个约束会撞上"k×2、所有倍率÷2"的简并解（乘积不变，误差一样小）。
        if not (0.6 <= mean <= 3.0):
            continue
        var = sum((x - mean) ** 2 for x in ks_v) / len(ks_v)
        rel = math.sqrt(var) / mean if mean else 9e9
        if best is None or rel < best[2]:
            best = (mean, list(zip(usable, combo)), rel)
    if best is None:
        return None, [], None
    return best[0], best[1], best[2]


def normalize_cards(raw_cards, attacker_types=None):
    """把 robot 读到的技能卡整理成 fit_k 需要的格式。

    raw_cards: [{"i":1,"v":142,...}, ...]（robot 的 turns.jsonl 格式）
    需要外部提供每张卡对应的威力与属性（按槽位），这里只做字段转换。
    """
    out = []
    for c in raw_cards:
        power = c.get("power") or 0
        if not power or not c.get("shown"):
            continue
        st = stab_of(attacker_types, c.get("type"))
        out.append({"i": c.get("i"), "shown": c["shown"],
                    "power": power, "stab": st, "type": c.get("type")})
    return out


def predict_for_pet(base_k, base_atk, new_atk):
    """按攻击值缩放 k：k 正比于攻击方的攻击值（对方防御不变时成立）。

    base_k  是当前对局拟合出的 k
    base_atk 是当前出战精灵的实际攻击值
    new_atk  是可能换上来的精灵的实际攻击值

    这只是线性近似，用对位数据库里的实测值校正。
    """
    if not base_atk:
        return base_k
    return base_k * (new_atk / float(base_atk))


def best_typings(observed, candidates=None):
    """根据已观测的属性倍率，给对手属性组合打分。

    observed: {type_id: mult}，mult 来自卡片反推或对位库
    返回 [(score, [type_id, ...]), ...]，score 越小越像。

    注意：游戏数据里没有现成的攻防克制矩阵，所以这里用一个
    "常见属性相性"先验（config/type_hint.json），再用对位库学习到的
    实际倍率覆盖。没有先验时，退化为只按观测值排序。
    """
    hints = load_hints()
    result = []
    pool = candidates or range(len(rocodata.TYPE_NAMES))
    for t in pool:
        err = 0.0
        n = 0
        for atk_type, mult in observed.items():
            want = hints.get("%d,%d" % (atk_type, t))
            if want is None:
                continue
            err += (math.log(max(mult, 0.01)) - math.log(max(want, 0.01))) ** 2
            n += 1
        if n:
            result.append((err / n, [t]))
    result.sort(key=lambda x: x[0])
    return result


def load_hints(path=None):
    """读取属性相性提示表（可选，缺失时返回空）。"""
    import json
    import os
    path = path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "type_hint.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return {k: float(v) for k, v in raw.items()}
    except (OSError, ValueError):
        return {}


if __name__ == "__main__":
    # 用截图数据自检
    cards = [
        {"i": 1, "power": 150, "shown": 142, "stab": 1.0},
        {"i": 2, "power": 100, "shown": 380, "stab": 1.0},
        {"i": 3, "power": 65, "shown": 154, "stab": STAB},
    ]
    k, pairs, rel = fit_k(cards)
    print("拟合 k = %.4f  相对误差 = %.3f" % (k, rel))
    for c, m in pairs:
        print("  卡%s 威力%3d 卡面%3d -> 倍率 %.2f (预测 %d)" % (
            c["i"], c["power"], c["shown"], m,
            predict(c["power"], k, c["stab"], m)))
