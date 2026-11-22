# -*- coding: utf-8 -*-
"""S19 实战伤害校准表：per-foe 承伤系数（live/卡牌预览）。

方法（docs/PLAN_S19_CALIB.md）：
  live_dmg = (foe_pct[t] - foe_pct[t+1])/100 × foe_max_hp_sim
  k = live_dmg / 卡牌预览 V
  按 foe_pet 聚合中位数。

输出：data/foe_calibration.json + 控制台报告。
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


def foe_max_hp(dex, name):
    sp = dex.species(name)
    if not sp:
        return None
    return S.calc_real_stat(sp["base"][0], 60, 1.0, is_hp=True)


def main():
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    dex = SIM.Dex()
    team = {r["species_id"] for r in db.execute(
        "SELECT species_id FROM my_team")}

    recs = []
    for line in io.open("logs/turns.jsonl", encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
    mids = defaultdict(list)
    for r in recs:
        mids[r.get("mid")].append(r)

    # card 预览缓存：turns 里 skills[i].v（team_db 源才可信）
    obs = []          # (mid, foe_pet, card_v, live_dmg)
    for mid, rs in mids.items():
        for i in range(len(rs) - 1):
            r, r2 = rs[i], rs[i + 1]
            src = r.get("skill_source")
            if not src or not isinstance(src, list) or \
                    "team_db" not in src:
                continue
            act = r.get("action") or []
            if act[0] != "skill":
                continue
            slot = act[1] if len(act) > 1 else None
            if not slot or slot > len(src):
                continue
            card_v = None
            for sk in (r.get("skills") or []):
                if sk.get("i") == slot:
                    try:
                        card_v = int(sk.get("v"))
                    except (TypeError, ValueError):
                        card_v = None
            if not card_v:
                continue
            foe1, foe2 = r.get("foe_pet"), r2.get("foe_pet")
            p1, p2 = r.get("foe_pct"), r2.get("foe_pct")
            if not (foe1 and foe2 and foe1 == foe2):
                continue          # 对方换人
            if p1 is None or p2 is None or p2 >= p1:
                continue          # 治疗/无伤害
            hp = foe_max_hp(dex, foe1)
            if not hp:
                continue
            live = (p1 - p2) / 100.0 * hp
            if live <= 0:
                continue
            obs.append((mid, foe1, card_v, live))

    print("observations: %d" % len(obs))
    by_foe = defaultdict(list)
    for mid, foe, v, live in obs:
        by_foe[foe].append((v, live))

    calib = {}
    rows = []
    for foe, items in sorted(by_foe.items(), key=lambda x: -len(x[1])):
        ks = [live / v for v, live in items if v > 0]
        if not ks:
            continue
        ks.sort()
        k_med = ks[len(ks) // 2]
        calib[foe] = {"k_median": round(k_med, 3), "n": len(ks)}
        # 校准残差
        resid = [abs(v * k_med - live) / max(1, live)
                 for v, live in items]
        resid.sort()
        r_med = resid[len(resid) // 2]
        rows.append((foe, len(ks), k_med, r_med))
        print("  %-12s n=%-3d k=%.2f  残差中位 %.0f%%"
              % (foe, len(ks), k_med, 100 * r_med))

    # 总体残差
    all_resid = []
    for foe, items in by_foe.items():
        if foe not in calib:
            continue
        k = calib[foe]["k_median"]
        for v, live in items:
            all_resid.append(abs(v * k - live) / max(1, live))
    if all_resid:
        all_resid.sort()
        print("overall: n=%d 残差中位 %.0f%%  p75 %.0f%%"
              % (len(all_resid), 100 * all_resid[len(all_resid) // 2],
                 100 * all_resid[3 * len(all_resid) // 4]))

    io.open("data/foe_calibration.json", "w", encoding="utf-8").write(
        json.dumps(calib, ensure_ascii=False, indent=1))
    print("written data/foe_calibration.json (%d foes)" % len(calib))


if __name__ == "__main__":
    main()
