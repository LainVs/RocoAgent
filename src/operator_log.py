# -*- coding: utf-8 -*-
"""记录"操作者（人）的每一手"，用于和机器人的影子选择对比。

为什么从结果反推动作：
    读不到人按了哪个键，但伤害是确定值 ——
    对面掉了 300，四张卡是 [162,300,120,60]，那只能是第 2 张。

落盘：logs/operator_log.jsonl，一行一个决策窗口：
    {"ts":.., "open":{my,foe,cards,shadow},
     "outcome":{my,foe}, "delta":{my,foe},
     "guess":{"slot":2,"by":"foe_hp"} }
"""
from __future__ import annotations

import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "logs", "operator_log.jsonl")

_pending = None


def _cards(st):
    return [s["value"] if s else None for s in st.get("skills") or []]


def open_window(st, shadow=None):
    """决策窗口出现：记下局面。"""
    global _pending
    _pending = {
        "ts": time.strftime("%H:%M:%S"),
        "my": st.get("my_pct"), "foe": st.get("foe_pct"),
        "cards": _cards(st),
        "shadow": shadow,
        "turn": st.get("turn"),
        "my_pet": st.get("my_pet") or st.get("active_pet"),
        "foe_pet": st.get("foe_pet"),
    }


def close_window(st, acted_by="operator"):
    """窗口关闭：用新的血量反推这一手。"""
    global _pending
    if not _pending:
        return None
    op = _pending
    _pending = None
    my0, foe0 = op.get("my"), op.get("foe")
    my1, foe1 = st.get("my_pct"), st.get("foe_pct")
    d_foe = None if (foe0 is None or foe1 is None) else round(foe1 - foe0, 1)
    d_my = None if (my0 is None or my1 is None) else round(my1 - my0, 1)

    guess = None
    cards = [v for v in (op.get("cards") or []) if v]
    if d_foe is not None and d_foe < -0.5 and cards:
        # 粗猜：掉血明显时，最可能是"卡面最大的那一手"。
        # 精确反推要等有了对面最大血量（知道实际掉血数）再做。
        guess = {"slot": None, "by": "foe_hp_drop", "candidates": cards}
    rec = dict(op)
    rec.update({
        "outcome": {"my": my1, "foe": foe1},
        "delta": {"my": d_my, "foe": d_foe},
        "acted_by": acted_by,
        "guess": guess,
    })
    try:
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return rec
