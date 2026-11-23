# -*- coding: utf-8 -*-
"""S24 承伤侧校准：per-foe incoming 系数（live / 预测）。

方法（docs/PLAN_S24_INCOMING.md）：
  live_incoming = (my_pct_before - my_pct_after)/100 × my_max_hp_sim
  预测 = pvp_engine.weighted_incoming(foe, me)（policy_v2._incoming 同款）
  k_in = live / 预测，按 foe 聚合中位数。

输出：data/foe_incoming_calibration.json + 控制台报告。
"""
import io
import json
import sys
from collections import defaultdict

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

import paths  # noqa: E402
import sqlite3  # noqa: E402
import simulator as SIM  # noqa: E402
import statmodel as S  # noqa: E402


def my_max_hp(dex, name):
    sp = dex.species(name)
    if not sp:
        return None
    return S.calc_real_stat(sp["base"][0], 60, 1.0, is_hp=True)


def predict_incoming(foe, me):
    """policy_v2._incoming 同款预测（weighted_incoming）。"""
    try:
        import pvp_engine as E
        dmg, _src = E.weighted_incoming(foe, me,
                                        blend=0.75)
        return float(dmg) if dmg else None
    except Exception:
        return None


def main():
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    dex = SIM.Dex()
    team = {r["species_id"] for r in db.execute(
        "SELECT species_id FROM my_team")}

    recs = []
    for line in io.open("logs/opponent_actions.jsonl", encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
    print("opponent recs:", len(recs))

    by_foe = defaultdict(list)
    n_obs = 0
    skip = defaultdict(int)
    for r in recs:
        act = r.get("act")
        foe = r.get("foe")
        me_name = r.get("me")
        p_b, p_a = r.get("my_pct_before"), r.get("my_pct_after")
        if act != "attack" or not foe or not me_name:
            skip["no-act"] += 1
            continue
        if p_b is None or p_a is None or p_a >= p_b:
            skip["no-dmg"] += 1
            continue
        hp = my_max_hp(dex, me_name)
        if not hp:
            skip["no-panel"] += 1
            continue
        live = (p_b - p_a) / 100.0 * hp
        if live <= 0:
            skip["nonpositive"] += 1
            continue
        pred = predict_incoming(foe, me_name)
        if not pred:
            skip["no-pred"] += 1
            continue
        by_foe[foe].append((pred, live))
        n_obs += 1

    print("observations:", n_obs, " skip:", dict(skip))
    calib = {}
    for foe, items in sorted(by_foe.items(), key=lambda x: -len(x[1])):
        ks = [live / pred for pred, live in items if pred > 0]
        if not ks:
            continue
        ks.sort()
        k_med = ks[len(ks) // 2]
        calib[foe] = {"k_median": round(k_med, 3), "n": len(ks)}
        resid = [abs(pred * k_med - live) / max(1, live)
                 for pred, live in items]
        resid.sort()
        print("  %-12s n=%-3d k_in=%.2f  残差中位 %.0f%%"
              % (foe, len(ks), k_med, 100 * resid[len(resid) // 2]))
    all_resid = []
    raw_resid = []
    for foe, items in by_foe.items():
        if foe not in calib:
            continue
        k = calib[foe]["k_median"]
        for pred, live in items:
            all_resid.append(abs(pred * k - live) / max(1, live))
            raw_resid.append(abs(pred - live) / max(1, live))
    if all_resid:
        all_resid.sort()
        raw_resid.sort()
        print("overall: n=%d" % len(all_resid))
        print("  校准后残差中位 %.0f%%  p75 %.0f%%"
              % (100 * all_resid[len(all_resid) // 2],
                 100 * all_resid[3 * len(all_resid) // 4]))
        print("  未校准残差中位 %.0f%%  p75 %.0f%%"
              % (100 * raw_resid[len(raw_resid) // 2],
                 100 * raw_resid[3 * len(raw_resid) // 4]))
    io.open("data/foe_incoming_calibration.json", "w",
            encoding="utf-8").write(
        json.dumps(calib, ensure_ascii=False, indent=1))
    print("written data/foe_incoming_calibration.json (%d foes)"
          % len(calib))


if __name__ == "__main__":
    main()
