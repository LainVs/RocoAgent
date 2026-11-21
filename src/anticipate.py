# -*- coding: utf-8 -*-
"""anticipate.py —— 用对手行为概率做预判决策。

用户口径
--------
"如果他防御概率高或者换人概率高，我们是不是可以叠 buff？
 如果他换上的那个精灵我们叠 buff 也不好打，是不是可以换某个对位精灵
 预判他，这样我们总是优势。"

核心思想
--------
把"对面下一步最可能做什么"变成可计算的期望值：

    对每个候选行动 a（放技能 / 换人 / 叠 buff）：
        EV(a) = Σ_p  P(对面做 p) × 收益(a, p)

其中 P 来自 opponent_model，收益来自伤害模型 + 属性克制。

当前限制
--------
样本不足时（model 里 reliable=false）**不给建议**，返回 None。
宁可不用，也不能用噪声决策。

用法
----
    import anticipate
    adv = anticipate.advice(me="音速犬", foe="白发路路",
                            my_pct=80, foe_pct=60,
                            my_hp=366, foe_hp=420,
                            my_cards=[...], bench=[...])
"""
from __future__ import annotations

import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "data", "opponent_model.json")

# 概率阈值：低于它视为"没有明显倾向"，不做预判
CLEAR_BIAS = 0.55


def load():
    try:
        return json.load(io.open(MODEL, encoding="utf-8"))
    except Exception:
        return {}


def lookup(model, me, foe):
    """取 (me vs foe) 的行为分布。没有就返回 None。"""
    if not me or not foe:
        return None
    # 名字可能带 #N 后缀（我方形态），去掉再查
    me0 = me.split("#")[0]
    # 旧模板名 -> 种族名（与 opponent_model.ALIASES 保持一致）
    import opponent_model as _OM
    me0 = _OM.ALIASES.get(me0, me0)
    return (model.get("%s|%s" % (me0, foe))
            or model.get("%s|%s" % (me, foe)))


def advice(me, foe, my_pct, foe_pct, my_hp, foe_hp,
           my_cards=None, bench=None, model=None):
    """给出预判建议。

    返回 dict 或 None：
    {
      "kind": "buff" | "switch" | "attack",
      "reason": "...",
      "p_attack": 0.2, "p_switch": 0.7, "p_buff": 0.1,
      "switch_to": [("白发路路", 5), ...],   # 他可能换上的前三
      "confidence": "high" | "low"
    }
    """
    m = model if model is not None else load()
    e = lookup(m, me, foe)
    if not e or not e.get("reliable"):
        return None

    pa = e.get("p_attack", 0)
    ps = e.get("p_switch", 0)
    pb = e.get("p_buff", 0)

    # 对面明显偏防御/增益 -> 我们可以叠 buff（它这回合不打断我们）
    if pb >= CLEAR_BIAS:
        return {
            "kind": "buff",
            "reason": "对面 %.0f%% 概率放增益/防御，这回合打不到我们，可以叠 buff"
                      % (pb * 100),
            "p_attack": pa, "p_switch": ps, "p_buff": pb,
            "switch_to": e.get("switch_top") or [],
            "confidence": "high" if pb >= 0.7 else "low",
        }

    # 对面明显偏换人 -> 预判他换上谁，我们换成能压制那只的
    if ps >= CLEAR_BIAS:
        tops = e.get("switch_top") or []
        if tops:
            target, cnt = tops[0]
            return {
                "kind": "switch",
                "reason": "对面 %.0f%% 概率换人，最可能换上 %s（%d 次），"
                          "建议换成能压制它的对位精灵"
                          % (ps * 100, target, cnt),
                "p_attack": pa, "p_switch": ps, "p_buff": pb,
                "switch_to": tops,
                "target": target,
                "confidence": "high" if ps >= 0.7 else "low",
            }
        return {
            "kind": "buff",
            "reason": "对面 %.0f%% 概率换人但没记录换上谁，先叠 buff"
                      % (ps * 100),
            "p_attack": pa, "p_switch": ps, "p_buff": pb,
            "switch_to": [],
            "confidence": "low",
        }

    # 对面偏攻击 -> 看我们扛不扛得住
    if pa >= CLEAR_BIAS:
        if my_hp and my_pct is not None:
            cur = my_hp * my_pct / 100.0
            if cur < my_hp * 0.35:
                return {
                    "kind": "switch",
                    "reason": "对面 %.0f%% 概率攻击，我血 %.0f%% 扛不住，"
                              "建议换成能吃的对位" % (pa * 100, my_pct),
                    "p_attack": pa, "p_switch": ps, "p_buff": pb,
                    "switch_to": e.get("switch_top") or [],
                    "confidence": "high",
                }
    return None


# ---------------------------------------------------------------- 自检

def _selftest():
    m = load()
    print("模型: %d 组对位" % len(m))
    reliable = [k for k, v in m.items() if v.get("reliable")]
    print("样本充足: %d 组" % len(reliable))
    if not reliable:
        print("\n样本不足，advice() 会全部返回 None（设计如此）")
        # 用假模型验证逻辑
        fake = {"A|B": {"n": 20, "reliable": True, "p_attack": 0.1,
                        "p_switch": 0.8, "p_buff": 0.1,
                        "switch_top": [["白发路路", 12], ["寂灭骨龙", 4]]}}
        r = advice("A", "B", 100, 100, 366, 400, model=fake)
        print("\n假模型（换人 80%%）:")
        print("  kind=%s target=%s" % (r["kind"], r.get("target")))
        print("  reason=%s" % r["reason"])
        return 0
    for k in reliable[:5]:
        e = m[k]
        me, foe = k.split("|", 1)
        r = advice(me, foe, 80, 60, 366, 400)
        print("\n%s vs %s (n=%d)" % (me, foe, e["n"]))
        print("  攻%.0f%% 换%.0f%% 增%.0f%%"
              % (e["p_attack"] * 100, e["p_switch"] * 100, e["p_buff"] * 100))
        print("  建议: %s" % (r["reason"] if r else "(无明显倾向)"))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
