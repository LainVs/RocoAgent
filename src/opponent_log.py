# -*- coding: utf-8 -*-
"""opponent_log.py —— 运行时记录"对面这一手做了什么"。

为什么需要它
------------
turns.jsonl 记的是**我们的**动作。要学对面行为，必须每回合额外记录：

    · 对面精灵是谁（名字 + 属性 + 属性置信度）
    · 我方是谁
    · 我方血量 / 对面血量（前后各一次，才能算出对面做了什么）
    · 对面换人时换上来的那只

这份数据是 opponent_model 的训练集。当前只有 2238 条去重回合、
93 组对位、仅 1 组样本达标 —— **远不够**。所以要专门积累。

输出
----
logs/opponent_actions.jsonl  每行一条"对面动作观测"：

    {"t": "01:30:12", "me": "音速犬", "foe": "白发路路",
     "foe_types": ["恶"], "foe_conf": "high",
     "my_pct_before": 100.0, "my_pct_after": 84.2,
     "foe_pct_before": 99.6, "foe_pct_after": 99.6,
     "act": "attack",            # attack / switch / buff / unknown
     "switched_to": null,
     "turn": 3, "match": 12}
"""
from __future__ import annotations

import io
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "logs", "opponent_actions.jsonl")

# 血量变化阈值（百分点）：低于这个数视为"没变"
EPS = 0.5


def classify(my_before, my_after, foe_before, foe_after, foe_switched):
    """对面这一手做了什么。

    判定顺序很重要：
      1. 换人（foe 变了）优先，因为换人回合对面通常不攻击
      2. 我掉血 -> 攻击
      3. 双方都不变 -> 增益/防御/聚能
    """
    if foe_switched:
        return "switch"
    if (my_before is not None and my_after is not None
            and my_after < my_before - EPS):
        return "attack"
    if (my_before is not None and my_after is not None
            and abs(my_after - my_before) < EPS):
        if (foe_before is not None and foe_after is not None
                and abs(foe_after - foe_before) < EPS):
            return "buff"
    return "unknown"


class Recorder:
    """在 Bot 里挂一个实例，每回合喂一次状态。"""

    def __init__(self, path=OUT):
        self.path = path
        self.prev = None
        self.match = 0

    def new_match(self):
        self.match += 1
        self.prev = None

    def observe(self, st, turn, foe_name, foe_types, foe_conf,
                our_name=None, foe_cast_skill=None):
        """喂一次状态。st 是 Perc.observe() 的结果。

        返回 (act, switched_to) 供上层决策参考；数据不足时返回 (None, None)。

        foe_cast_skill（2026-11-22）：robot._cast_ocr 从出招横幅读到的
        「对面刚刚用了什么技能」。有了它 + 官方效果链分类，
        opponent_model 才能把对面行为细分 a1攻击/a2防御/a3状态。
        """
        cur = {
            "my_pct": st.get("my_pct"),
            "foe_pct": st.get("foe_pct"),
            "foe": foe_name,
            "types": foe_types,
            "conf": foe_conf,
            "me": our_name,
            "turn": turn,
            # 对面出招横幅（下一帧 classify 时才用得上）
            "foe_cast_skill": foe_cast_skill,
        }
        if self.prev is None:
            self.prev = cur
            return None, None

        p = self.prev
        # 同一回合的重复轮询不算新观测
        if p.get("turn") == turn and p.get("foe") == foe_name:
            self.prev = cur
            return None, None

        switched = bool(p.get("foe") and foe_name
                        and p["foe"] != foe_name)
        act = classify(p.get("my_pct"), cur.get("my_pct"),
                       p.get("foe_pct"), cur.get("foe_pct"), switched)
        if act == "unknown":
            self.prev = cur
            return None, None

        # ---- 对面这一手的技能类别（2026-11-22，官方效果链）
        #
        # 用户要的 a1攻击/a2防御/a3状态 细分，靠光标（cast banner）读到的
        # 技能名 + 官方效果链分类。机器人对面对我们时通常只读得到
        # 「XX 使用了 YY」的横幅，那一刻 _cast_nick/skill 记录在哪，
        # 由 robot._cast_ocr 喂进来（self.last_foe_cast）。
        act_kind = None
        act_skill = cur.get("foe_cast_skill")
        if act_skill:
            try:
                import skill_kind_official as SKO
                import skill_kind as SK
                act_kind = SKO.classify_official_first(
                    act_skill, SK.classify(act_skill))
            except Exception:
                act_kind = None

        rec = {
            "t": time.strftime("%H:%M:%S"),
            "match": self.match,
            "turn": turn,
            "me": p.get("me"),
            "foe": p.get("foe"),
            "foe_types": p.get("types"),
            "foe_conf": p.get("conf"),
            "my_pct_before": p.get("my_pct"),
            "my_pct_after": cur.get("my_pct"),
            "foe_pct_before": p.get("foe_pct"),
            "foe_pct_after": cur.get("foe_pct"),
            "act": act,
            "act_kind": act_kind,
            "act_skill": act_skill,
            "switched_to": foe_name if switched else None,
        }
        try:
            with io.open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

        self.prev = cur
        return act, rec["switched_to"]


if __name__ == "__main__":
    # 自检：用构造数据验证分类逻辑
    cases = [
        ("换人", 100, 100, 100, 100, True, "switch"),
        ("攻击", 100, 84, 100, 100, False, "attack"),
        ("增益", 100, 100, 100, 100, False, "buff"),
        ("无法判断", 100, None, 100, 100, False, "unknown"),
    ]
    ok = 0
    for name, mb, ma, fb, fa, sw, want in cases:
        got = classify(mb, ma, fb, fa, sw)
        hit = got == want
        ok += 1 if hit else 0
        print("  %-8s -> %-8s want %-8s %s"
              % (name, got, want, "OK" if hit else "FAIL"))
    print("\n%d/%d" % (ok, len(cases)))
