# -*- coding: utf-8 -*-
"""audit_incoming.py —— 承伤估计器的偏差审计（每晚跑一次，自动报告）。

对比三个量，按 (me, foe) 对位分组：

    observed   实际掉血（opponent_actions.jsonl 的 my_pct 前后差
               × my_team 实测 max_hp）
    worst      pvp_engine.worst_incoming（整池取 max，旧口径）
    weighted   pvp_engine.weighted_incoming（新口径，当前线上用）

报告两个关键比值：
    weighted/observed   <0.8 -> 建议把 incoming_blend 降到 0.6
                         >1.3 -> 期望侧被噪声抬高，检查 foe_moves 分布
    worst/observed      用于确认"旧口径高估"仍然成立（回归护栏）

用法
----
    & $py scripts\\audit_incoming.py                # 全部对位
    & $py scripts\\audit_incoming.py --min-n 3      # 只看样本 >=3 的
    & $py scripts\\audit_incoming.py --json out.json
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, r"D:\Tools")

import paths  # noqa: E402
paths.use_tools()

import pvp_engine as E  # noqa: E402
import foe_moves  # noqa: E402
import statmodel as S  # noqa: E402
import damage as D  # noqa: E402


# ---------------------------------------------------------------- 估计器
def _single_skill_dmg(skill, foe_name, my_name):
    sk = D.skill(skill)
    if not sk:
        return None
    try:
        power = int(sk.get("power") or 0)
    except (TypeError, ValueError):
        return None
    if power <= 0:
        return None
    p_me = E.pet(my_name)
    p_foe = E.pet(foe_name)
    if not p_me or not p_foe:
        return None
    atk_type = sk.get("attribute")
    stab = 1.25 if atk_type in (p_foe.get("attributes_list") or []) else 1.0
    try:
        return int(S.damage(
            S.calc_real_stat(int(p_foe["stats"]["attack"]), S.IV_PVP, 1.0),
            S.calc_real_stat(int(p_me["stats"]["defense"]), S.IV_PVP, 1.0),
            power,
            type_eff=S.type_effect(atk_type, p_me.get("attributes_list") or []),
            stab=stab))
    except Exception:
        return None


def worst_in(foe, me):
    rows = E.worst_incoming(foe, me, top=1)
    return rows[0]["dmg"] if rows else None


def exp_in(foe, me, top=6):
    tot = 0.0
    try:
        dist = foe_moves.distribution(foe, top=top)
    except Exception:
        return None
    for name, p, _pw, _tid in dist:
        d = _single_skill_dmg(name, foe, me)
        if d:
            tot += p * d
    return int(tot) if tot else None


def weighted_in(foe, me, blend=0.75):
    worst = worst_in(foe, me) or 0
    exp = exp_in(foe, me) or 0
    if worst <= 0:
        return None
    if exp <= 0:
        return worst
    return int(max(exp, round(worst * blend)))


# ---------------------------------------------------------------- 观测
def team_max_hp():
    db = sqlite3.connect(paths.DB)
    db.row_factory = sqlite3.Row
    out = {}
    try:
        for r in db.execute("SELECT slot, species_id, max_hp FROM my_team"):
            sid = r["species_id"]
            row = db.execute("SELECT name FROM species WHERE id=?",
                             (sid,)).fetchone()
            if row:
                out[row["name"]] = r["max_hp"] or None
    finally:
        db.close()
    # my_team 缺 max_hp 时用 statmodel 兜底
    import foe_hp as FH
    for name in list(out):
        if not out.get(name):
            out[name] = FH.max_hp(name)
    return out


def observed_by_pair(min_pct_delta=1.0):
    rows = []
    p = os.path.join(ROOT, "logs", "opponent_actions.jsonl")
    try:
        with io.open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        return {}
    hp = team_max_hp()
    agg = collections.defaultdict(list)
    for r in rows:
        me, foe = r.get("me"), r.get("foe")
        b, a = r.get("my_pct_before"), r.get("my_pct_after")
        if not me or not foe or b is None or a is None:
            continue
        mx = hp.get(me)
        if not mx:
            continue
        dmg = (b - a) / 100.0 * mx
        if dmg < min_pct_delta:
            continue          # buff / switch 回合
        agg[(me, foe)].append(dmg)
    return agg


# ---------------------------------------------------------------- 报告
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-n", type=int, default=2)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    agg = observed_by_pair()
    rows = []
    for (me, foe), ds in sorted(agg.items(), key=lambda kv: -len(kv[1])):
        n = len(ds)
        if n < args.min_n:
            continue
        obs = sum(ds) / n
        worst = worst_in(foe, me)
        wt = weighted_in(foe, me)
        rows.append({"me": me, "foe": foe, "n": n,
                     "obs_avg": round(obs, 1),
                     "worst": worst, "weighted": wt,
                     "wt_over_obs": round(wt / obs, 2) if wt and obs else None,
                     "worst_over_obs": round(worst / obs, 2)
                     if worst and obs else None})

    print("== incoming estimator audit (%d pairs, min n=%d) =="
          % (len(rows), args.min_n))
    print("%-12s %-14s %3s %8s %7s %9s %9s %9s"
          % ("me", "foe", "n", "obs_avg", "worst", "weighted",
             "wt/obs", "worst/obs"))
    for r in rows:
        print("%-12s %-14s %3d %8.1f %7s %9s %9s %9s"
              % (r["me"], r["foe"], r["n"], r["obs_avg"],
                 r["worst"], r["weighted"], r["wt_over_obs"],
                 r["worst_over_obs"]))

    # 聚合建议
    ratios = [r["wt_over_obs"] for r in rows
              if r["wt_over_obs"] is not None and r["n"] >= 3]
    if ratios:
        med = sorted(ratios)[len(ratios) // 2]
        print("\nmedian weighted/observed = %.2f over %d pairs (n>=3)"
              % (med, len(ratios)))
        if med < 0.8:
            print("  -> weighted 系统性偏低：建议 incoming_blend 0.75 -> 0.6")
        elif med > 1.3:
            print("  -> weighted 偏高：检查 foe_moves 分布是否被观测噪声抬高")
        else:
            print("  -> 在合理区间，保持现状")
    else:
        print("\n样本不足（n>=3 的对位 0 组），先跑几晚积累数据")

    if args.json:
        with io.open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print("wrote", args.json)


if __name__ == "__main__":
    main()
