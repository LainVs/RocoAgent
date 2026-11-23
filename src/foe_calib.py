# -*- coding: utf-8 -*-
"""S19/S20 实战伤害校准：per-foe 承伤系数。

数据：data/foe_calibration.json（scripts/foe_calibration.py 生成，
live_dmg / 卡牌预览 的 per-foe 中位比值）。

用法：
    from foe_calib import k
    dmg_real = card_preview * k(foe_name)
"""
import io
import json
import os

_CACHE = None


def _load():
    global _CACHE
    if _CACHE is None:
        p = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "data", "foe_calibration.json")
        try:
            _CACHE = json.load(io.open(p, encoding="utf-8"))
        except Exception:
            _CACHE = {}
    return _CACHE


def k(foe_name):
    """对手有效承伤系数（我方伤害 × k ≈ 实盘）。未知对手 → 1.0。"""
    entry = _load().get(foe_name)
    if not entry:
        return 1.0
    v = entry.get("k_median")
    if not isinstance(v, (int, float)) or v <= 0:
        return 1.0
    return float(v)


def k_in(foe_name):
    """对手输出系数（对手打我方的伤害 × k_in ≈ 实盘）。

    数据：data/foe_incoming_calibration.json（S24 脚本，opponent_actions
    的 my_pct 差分 vs weighted_incoming 预测）。未知对手 → 1.0。
    """
    p = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data",
        "foe_incoming_calibration.json")
    try:
        with io.open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return 1.0
    entry = data.get(foe_name)
    if not entry:
        return 1.0
    v = entry.get("k_median")
    if not isinstance(v, (int, float)) or v <= 0:
        return 1.0
    return float(v)


def reload():
    global _CACHE
    _CACHE = None
    return _load()
