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
# 对面行为的**权威记录**（opponent_log.py 采集，带 match 和 act 标签）
ACTIONS = os.path.join(ROOT, "logs", "opponent_actions.jsonl")
MODEL = os.path.join(ROOT, "data", "opponent_model.json")

# 样本量门槛：低于这个数不给概率（避免噪声当规律）
MIN_SAMPLES = 12

# 训练假人：不是真实对手，行为固定（只会攻击），混进来会污染统计。
#
# 实测（2026-11-21）：`60级水桶模板` 占 13 组对位 / 47 条样本（19%），
# 而且它的 p_attack=1.00，会把整体概率拉向"对面一定攻击"。
#
# 注意名字是「模板」不是「模型」—— 第一次写错字，排除没生效，
# 数据里实际是 '60级水桶模板'。
DUMMY_FOES = ("水桶模板", "水桶模型")

# 立绘模板名 -> 种族名。
#
# 立绘识别输出的是模板文件名，同一只精灵可能有多个模板：
#     wolf / wolf_leader  -> 音速犬（旧模板名 + 首领化形态）
# 不归一化的话，同一只精灵的样本会被切到多个键上。
#
# 实测（2026-11-21）：我方名字带 #编号的有 62 组 / 131 条样本（54%），
# 全被切碎，导致 147 组对位里只有 1 组达到 MIN_SAMPLES。
ALIASES = {
    "wolf": "音速犬",
    "wolf_leader": "音速犬",
}


def _norm_me(name):
    """把立绘模板名归一成种族名。

    `银月狼王#2` / `银月狼王#7` -> `银月狼王`
    `wolf` -> `音速犬`
    """
    if not name:
        return name
    base = str(name).split("#")[0]
    return ALIASES.get(base, base)


def _is_dummy(name):
    """是不是训练假人。"""
    if not name:
        return False
    return any(d in str(name) for d in DUMMY_FOES)


def load_turns():
    out = []
    for line in io.open(TURNS, encoding="utf-8"):
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def load_actions():
    """读 opponent_actions.jsonl —— 对面行为的权威记录。

    为什么不用 turns.jsonl 反推（2026-11-21 实测）
    ----------------------------------------------
    turns.jsonl 是轮询日志，而且**多个机器人进程同时写**：

        原始 3110 条，按 ts 排序后 turn 倒退 806 次
        同一时间戳出现多条的: 54 个时刻
          03:04:24 x3: [(1, None), (2, None), (3, None)]

    靠 turn==1 切分场次遇到交错数据就完全错位，
    opponent_action(a, b) 会拿两个不同局的记录做比较 ——
    结果 p_buff 虚高到 0.50~0.57（相邻记录血量都没变 -> 判成 buff）。

    opponent_actions.jsonl 由 opponent_log.py 专门采集：
        · 有 match 字段，场次边界明确
        · act 已经是标签，不需要反推
        · me 已是种族名（不带 #编号）
        · 每条只记一次（不是轮询）
    """
    out = []
    if not os.path.exists(ACTIONS):
        return out
    for line in io.open(ACTIONS, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
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


def build_from_actions(rows):
    """从 opponent_actions.jsonl 建行为分布（主路径）。

    与 build() 的区别：
        · 数据已经有 act 标签，不需要 opponent_action() 反推
        · 按 match 分组，不靠 turn 序列猜场次
        · me 已是种族名，归一化只作兜底

    结构（与 build() 保持一致，下游不用改）：
    {
      "音速犬|白发路路": {
         "n": 19, "reliable": true,
         "p_attack": 0.37, "p_switch": 0.47, "p_buff": 0.16,
         "switch_top": [["冥古龙", 5], ...],
         "next_types_top": [...]
      }
    }
    """
    pairs = defaultdict(lambda: {"n": 0, "actions": Counter(),
                                 "switch_to": Counter(),
                                 "next_types": Counter()})
    matches = set()
    for r in rows:
        foe = r.get("foe")
        me = r.get("me")
        if not foe or not me:
            continue
        # 训练假人不计入（行为固定，会污染概率）
        if _is_dummy(foe):
            continue
        act = r.get("act")
        if act not in ("attack", "switch", "buff"):
            continue
        matches.add(r.get("match"))
        key = "%s|%s" % (_norm_me(me), foe)
        e = pairs[key]
        e["n"] += 1
        e["actions"][act] += 1
        if act == "switch" and r.get("switched_to"):
            e["switch_to"][r["switched_to"]] += 1
        if r.get("foe_types"):
            e["next_types"][json.dumps(r["foe_types"],
                                       ensure_ascii=False)] += 1

    # ---- 分层池化（2026-11-22）------------------------------------------
    #
    # 单看 (我|对面) 这一对，样本几乎永远不够：实测 318 条 / 53 组对位，
    # 只有 1 组达到 MIN_SAMPLES=12，于是 anticipate 永远返回 None。
    #
    # 再建两层先验：
    #     __foe__|<对面>      这只对面精灵对**所有**我方精灵的行为分布
    #     __global__|__all__  全体分布
    #
    # 对每一对做 Dirichlet 平滑：
    #     p = (count + K * prior) / (n + K)
    # 这样 1~2 条样本也能给出"偏向对面总体习惯"的合理概率。
    K = 8.0
    foe_all = defaultdict(Counter)
    glob = Counter()
    for key, e in pairs.items():
        foe = key.split("|", 1)[1]
        foe_all[foe].update(e["actions"])
        glob.update(e["actions"])

    def _pack(n, acts, extra=None):
        total = sum(acts.values()) or 1
        d = {
            "n": int(n),
            "p_attack": round(acts.get("attack", 0) / total, 3),
            "p_switch": round(acts.get("switch", 0) / total, 3),
            "p_buff": round(acts.get("buff", 0) / total, 3),
        }
        if extra:
            d.update(extra)
        return d

    out = {}
    g_n = sum(glob.values())
    out["__global__|__all__"] = _pack(g_n, glob, {
        "reliable": g_n >= MIN_SAMPLES, "level": "global"})
    for foe, c in foe_all.items():
        fn = sum(c.values())
        out["__foe__|%s" % foe] = _pack(fn, c, {
            "reliable": fn >= MIN_SAMPLES, "level": "foe"})

    for key, e in pairs.items():
        foe = key.split("|", 1)[1]
        prior = foe_all[foe]
        pt = sum(prior.values()) or 1
        n = e["n"]
        blended = Counter()
        for a in ("attack", "switch", "buff"):
            blended[a] = e["actions"].get(a, 0) + K * prior.get(a, 0) / pt
        out[key] = _pack(n + K, blended, {
            # n 保留**真实对位样本数**（调用方据此判断置信度）
            "n": n,
            "reliable": n >= MIN_SAMPLES,
            "level": "pair",
            "switch_top": e["switch_to"].most_common(3),
            "next_types_top": e["next_types"].most_common(3),
        })
    return out, len([m for m in matches if m is not None])


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
                                 "next_types": Counter(),
                                 "skipped_dummy": 0})
    for m in matches:
        for i in range(len(m) - 1):
            a, b = m[i], m[i + 1]
            me, foe = a.get("our_pet"), a.get("foe_pet")
            if not me or not foe:
                continue
            # 训练假人不计入（行为固定，会污染概率）
            if _is_dummy(foe):
                continue
            # 只统计"对面这一手"：a 是我们行动后，b 是对面行动后
            act = opponent_action(a, b)
            if not act:
                continue
            # 归一化我方名字：银月狼王#2 / 银月狼王#7 -> 银月狼王
            key = "%s|%s" % (_norm_me(me), foe)
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
    # 主路径：opponent_actions.jsonl（对面行为的权威记录）
    acts = load_actions()
    if acts:
        print("数据源: opponent_actions.jsonl")
        model, n_matches = build_from_actions(acts)
        print("原始记录: %d" % len(acts))
    else:
        # 降级：从 turns.jsonl 反推（旧数据 / 文件不存在）
        print("数据源: turns.jsonl（降级，反推对面行为）")
        print("!! 警告：turns.jsonl 是多进程轮询日志，场次可能交错，")
        print("!!       p_buff 会虚高。建议优先积累 opponent_actions.jsonl。")
        rows = load_turns()
        print("原始记录: %d" % len(rows))
        print("去重后: %d" % len(dedupe(rows)))
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
