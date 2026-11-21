# -*- coding: utf-8 -*-
"""calibrate_damage.py —— 用实盘卡面伤害校准模拟器的伤害公式。

为什么用卡面伤害当真理
----------------------
游戏在技能卡上显示的伤害数字，是它**自己算出来的**。这是最可靠的真值。

**关键（用户 2026-11-21 纠正）**：
卡面数字含**双方当前的 buff**，不是裸伤害。
    我方有攻击 buff      -> 卡面变高
    对方有防御 buff      -> 卡面变低
所以拿"无 buff 模拟"去比全场记录，误差必然很大（实测中位 46%）。

正确做法：**只统计开局、buff 还没叠起来的记录**（turn <= 2），
或者从日志里反推当时的 buff 状态（难，暂不做）。

数据来源
--------
`logs/turns.jsonl` 里每帧记录四个槽位的卡面读数：
    {"i":1, "v":285, "c":"green", "t":"285", "lit":true}

校准方法
--------
对每条记录：
    1. 用 my_team 找到我方精灵的装备技能（按槽位顺序）
    2. 用 sim.adapter 构造 Pokemon
    3. 用 sim 的 DamageCalculator 算预测伤害
    4. 误差 = (预测 - 卡面) / 卡面

统计中位误差、P90 误差、>15% 的比例，以及相对排序一致率。

跑法：
    python scripts\\calibrate_damage.py
    python scripts\\calibrate_damage.py --max-turn 2      # 只看开局
    python scripts\\calibrate_damage.py --by-pet
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

TURNS = os.path.join(ROOT, "logs", "turns.jsonl")


def load_records(limit=None, max_turn=None):
    """收集 (我方精灵, 对手精灵, [(槽位, 卡面伤害)], turn) 记录。"""
    out = []
    for line in open(TURNS, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        me = r.get("our_pet")
        foe = r.get("foe_pet")
        if not me or not foe:
            continue
        turn = r.get("turn")
        if max_turn is not None:
            if turn is None or turn > max_turn:
                continue
        skills = [(s.get("i"), s.get("v"))
                  for s in (r.get("skills") or [])
                  if s and s.get("v")]
        if not skills:
            continue
        out.append({
            "ts": r.get("ts"),
            "turn": turn,
            "me": me,
            "foe": foe,
            "my_pct": r.get("my_pct"),
            "foe_pct": r.get("foe_pct"),
            "skills": skills,
        })
        if limit and len(out) >= limit:
            break
    return out


def base_name(name):
    """'音速犬（看守麦田的样子）' -> '音速犬'"""
    if not name:
        return name
    for sep in ("（", "("):
        if sep in name:
            return name.split(sep)[0]
    return name


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-turn", type=int, default=0,
                    help="只看 turn <= N 的记录（buff 还没叠起来）")
    ap.add_argument("--by-pet", action="store_true", help="按精灵分组统计")
    ap.add_argument("--verbose", action="store_true", help="打印误差最大的 10 条")
    args = ap.parse_args()

    if not os.path.exists(TURNS):
        print("没有 logs/turns.jsonl")
        return 1

    import sim.adapter as A
    import sim.battle as B

    recs = load_records(args.limit or None,
                        args.max_turn or None)
    print("可校准记录: %d%s" % (
        len(recs),
        "（只取 turn<=%d）" % args.max_turn if args.max_turn else ""))
    if not recs:
        return 1

    errors = []
    by_pet = defaultdict(list)
    rank_ok = rank_total = 0
    skipped = defaultdict(int)
    worst = []          # (err, me, foe, slot, pred, card)

    for r in recs:
        me = base_name(r["me"])
        foe = base_name(r["foe"])
        try:
            p_me = A.build_pokemon(me)
            p_foe = A.build_pokemon(foe)
        except Exception as e:
            skipped["构造失败:%s" % type(e).__name__] += 1
            continue

        card = dict(r["skills"])
        preds = {}
        for i, sk in enumerate(p_me.skills, start=1):
            if i not in card:
                continue
            if sk.power <= 0:
                continue
            try:
                d = B.DamageCalculator.calculate(p_me, p_foe, sk)
            except Exception:
                skipped["计算失败"] += 1
                continue
            preds[i] = d

        if len(preds) < 1:
            skipped["无可用槽位"] += 1
            continue

        for i, pred in preds.items():
            actual = card[i]
            if actual <= 0:
                continue
            err = abs(pred - actual) / float(actual)
            errors.append(err)
            by_pet[me].append(err)
            worst.append((err, me, foe, i, pred, actual))

        if len(preds) >= 2:
            pred_best = max(preds, key=preds.get)
            card_best = max(preds.keys(), key=lambda k: card[k])
            rank_total += 1
            if pred_best == card_best:
                rank_ok += 1

    if not errors:
        print("没有可用的校准点")
        for k, v in skipped.items():
            print("  跳过 %s: %d" % (k, v))
        return 1

    errors.sort()
    med = statistics.median(errors)
    p90 = errors[int(len(errors) * 0.9)] if len(errors) > 1 else errors[0]
    over15 = sum(1 for e in errors if e > 0.15)

    print()
    print("=== 绝对误差 ===")
    print("  样本点      %d" % len(errors))
    print("  中位误差    %.1f%%" % (med * 100))
    print("  P90 误差    %.1f%%" % (p90 * 100))
    print("  >15%% 的比例 %.1f%% (%d/%d)" % (
        100.0 * over15 / len(errors), over15, len(errors)))
    print()
    print("=== 相对排序（预测最高的槽位 == 卡面最高？）===")
    if rank_total:
        print("  一致率      %.1f%% (%d/%d)" % (
            100.0 * rank_ok / rank_total, rank_ok, rank_total))
    else:
        print("  （可用样本不足）")

    if args.by_pet:
        print()
        print("=== 按我方精灵 ===")
        for pet, es in sorted(by_pet.items(), key=lambda kv: -len(kv[1])):
            if len(es) < 3:
                continue
            print("  %-14s n=%-4d 中位 %.1f%%" % (
                pet, len(es), statistics.median(es) * 100))

    if skipped:
        print()
        print("=== 跳过统计 ===")
        for k, v in skipped.items():
            print("  %-20s %d" % (k, v))

    if args.verbose:
        print()
        print("=== 误差最大的 10 条 ===")
        worst.sort(reverse=True)
        for e, me, foe, slot, pred, card in worst[:10]:
            print("  %5.0f%%  %-10s vs %-10s slot%d 预测=%-4d 卡面=%-4d" % (
                e * 100, me, foe, slot, pred, card))

    print()
    if med < 0.10:
        print("结论: 中位误差 %.1f%% < 10%%，校准通过" % (med * 100))
        return 0
    print("结论: 中位误差 %.1f%% >= 10%%" % (med * 100))
    print("      注意：卡面含双方 buff，全场统计必然偏高。")
    print("      用 --max-turn 2 只看开局（buff 未叠）会更准。")
    return 2


if __name__ == "__main__":
    sys.exit(main())
