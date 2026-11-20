# -*- coding: utf-8 -*-
"""opponent_model.py —— 对手行为概率模型（从对战数据里学）。

要解决的问题（用户口径）
------------------------
"音速犬 vs 对面某只精灵时，对面放技能的概率 / 换人的概率各是多少？
 如果换人，换上来的前三只最可能是谁？"

有了这些概率就能做**预判决策**：
    · 对面防御概率高 -> 我们叠 buff 更划算
    · 对面换人概率高 -> 我们换成能压制"他可能换上来的那只"的对位精灵
    · 这样每一手都是优势

数据来源
--------
logs/turns.jsonl 里每个回合记录：
    our_pet / foe_pet / foe_pct / my_pct / action / skills / foe_types

**关键限制**：turns.jsonl 记录的是**我们的**动作，不是对面的。
要学对面的行为，必须从"回合之间的状态变化"反推：

    T_n  我方行动后，foe_pct 从 A 变到 B
    T_n+1 对面行动后，my_pct 从 C 变到 D

反推规则
--------
    · 我掉血（my_pct 下降）-> 对面放了攻击技能
    · 对面血量不变 + 对面精灵换了 -> 对面换人
    · 都没变 -> 对面放了增益/防御类技能（或聚能）
    · 对面血量下降 -> 我打中了（这是我们的输出，不是对面行为）

当前数据量（2026-11-21）
------------------------
    2745 条记录，154 组对位，最多 33 回合
    => 单组对位样本太少，概率还不能用。先建骨架持续积累。

用法
----
    python src\\opponent_model.py --stats      # 看当前数据够不够
    python src\\opponent_model.py --build      # 重建模型
"""
from __future__ import annotations

import io
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TURNS = os.path.join(ROOT, "logs", "turns.jsonl")
MODEL = os.path.join(ROOT, "data", "opponent_model.json")

# 样本量门槛：低于这个数不给概率（避免噪声当规律）
MIN_SAMPLES = 12


def load_turns():
    out = []
    for line in io.open(TURNS, encoding="utf-8"):
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def dedupe(rows):
    """turns.jsonl 是轮询日志，同一回合会重复很多次。

    按 (turn, my_pct, foe_pct, action) 去重，保留每个回合的第一次。
    """
    seen = set()
    out = []
    for r in rows:
        key = (r.get("turn"), r.get("my_pct"), r.get("foe_pct"),
               tuple(r.get("action") or []))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def opponent_action(prev, cur):
    """从相邻两条记录反推对面这一手做了什么。

    返回 'attack' / 'switch' / 'buff' / None（无法判断）
    """
    if not prev or not cur:
        return None
    # 对面换人：foe_pet 变了
    if prev.get("foe_pet") and cur.get("foe_pet"):
        if prev["foe_pet"] != cur["foe_pet"]:
            return "switch"
    # 我掉血 -> 对面攻击
    mp, mc = prev.get("my_pct"), cur.get("my_pct")
    if mp is not None and mc is not None and mc < mp - 0.5:
        return "attack"
    # 双方血量都没变 -> 增益/防御/聚能
    fp, fc = prev.get("foe_pct"), cur.get("foe_pct")
    if mp is not None and mc is not None and abs(mc - mp) < 0.5:
        if fp is not None and fc is not None and abs(fc - fp) < 0.5:
            return "buff"
    return None


def build(rows):
    """按 (our_pet, foe_pet) 建行为分布。

    结构：
    {
      "音速犬|水桶模板": {
         "n": 33,
         "actions": {"attack": 20, "switch": 8, "buff": 5},
         "switch_to": {"白发路路": 5, "寂灭骨龙": 3},   # 换上来的前三
         "next_foe_types": {"[\"水\"]": 5},
         "reliable": true/false
      }
    }
    """
    rows = dedupe(rows)
    # 按场次切分（用 match 分隔：turn 回到 1 就是新的一场）
    matches = []
    cur = []
    for r in rows:
        if r.get("turn") == 1 and cur:
            matches.append(cur)
            cur = []
        cur.append(r)
    if cur:
        matches.append(cur)

    pairs = defaultdict(lambda: {"n": 0, "actions": Counter(),
                                 "switch_to": Counter(),
                                 "next_types": Counter()})
    for m in matches:
        for i in range(len(m) - 1):
            a, b = m[i], m[i + 1]
            me, foe = a.get("our_pet"), a.get("foe_pet")
            if not me or not foe:
                continue
            # 只统计"对面这一手"：a 是我们行动后，b 是对面行动后
            act = opponent_action(a, b)
            if not act:
                continue
            key = "%s|%s" % (me, foe)
            e = pairs[key]
            e["n"] += 1
            e["actions"][act] += 1
            if act == "switch" and b.get("foe_pet"):
                e["switch_to"][b["foe_pet"]] += 1
            if b.get("foe_types"):
                e["next_types"][json.dumps(b["foe_types"],
                                           ensure_ascii=False)] += 1

    out = {}
    for key, e in pairs.items():
        n = e["n"]
        acts = e["actions"]
        total = sum(acts.values()) or 1
        entry = {
            "n": n,
            "reliable": n >= MIN_SAMPLES,
            "p_attack": round(acts.get("attack", 0) / total, 3),
            "p_switch": round(acts.get("switch", 0) / total, 3),
            "p_buff": round(acts.get("buff", 0) / total, 3),
            "switch_top": e["switch_to"].most_common(3),
            "next_types_top": e["next_types"].most_common(3),
        }
        out[key] = entry
    return out, len(matches)


def main():
    rows = load_turns()
    print("原始记录: %d" % len(rows))
    rows2 = dedupe(rows)
    print("去重后: %d" % len(rows2))

    model, n_matches = build(rows)
    print("识别出 %d 场对局" % n_matches)
    print("对位组合: %d" % len(model))

    reliable = {k: v for k, v in model.items() if v["reliable"]}
    print("样本充足(>=%d): %d 组" % (MIN_SAMPLES, len(reliable)))

    if "--stats" in sys.argv:
        print("\n=== 样本最多的对位 ===")
        for k, v in sorted(model.items(), key=lambda kv: -kv[1]["n"])[:10]:
            mark = "✓" if v["reliable"] else " "
            print("  %s %-30s n=%-3d 攻%.0f%% 换%.0f%% 增%.0f%%"
                  % (mark, k, v["n"], v["p_attack"] * 100,
                     v["p_switch"] * 100, v["p_buff"] * 100))
            if v["switch_top"]:
                print("      换人前三: %s" % v["switch_top"])
        return 0

    if "--build" in sys.argv:
        os.makedirs(os.path.dirname(MODEL), exist_ok=True)
        io.open(MODEL, "w", encoding="utf-8", newline="\n").write(
            json.dumps(model, ensure_ascii=False, indent=1))
        print("\n已写入 %s" % MODEL)
        return 0

    print("\n（加 --stats 看分布，--build 写模型）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
