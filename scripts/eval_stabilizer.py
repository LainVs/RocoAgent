# -*- coding: utf-8 -*-
"""用实盘真实抖动序列评估稳定化效果。

输入：logs/turns.jsonl 里同一 pet+foe 的连续轮询读数。
对比：原始稳定率 vs 经过 SkillStabilizer 后的稳定率。
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, "src")
from skill_reader import SkillStabilizer

rows = []
for line in open(os.path.join("logs", "turns.jsonl"),
                 encoding="utf-8", errors="ignore"):
    line = line.strip()
    if not line:
        continue
    try:
        rows.append(json.loads(line))
    except Exception:
        pass

# 取 07:10 之后（稳定化生效期间）的真实数据
sel = [r for r in rows if (r.get("ts") or "") >= "07:10"]
print("样本 %d 条" % len(sel))


def stability(seqs):
    """连续两次轮询里，槽位值不变的比率。"""
    same = chg = 0
    for a, b in zip(seqs, seqs[1:]):
        for va, vb in zip(a, b):
            if va is None or vb is None:
                continue
            if va == vb:
                same += 1
            else:
                chg += 1
    return 100.0 * same / max(1, same + chg), same, chg


# 按 (pet, foe) 分组，组内按时间顺序
groups = {}
for r in sel:
    key = (r.get("our_pet"), r.get("foe_pet"))
    if key == (None, None):
        continue
    groups.setdefault(key, []).append(r)

print()
print("=== 原始 vs 稳定化后（按 pet+foe 分组）===")
tot_raw = [0, 0]
tot_stab = [0, 0]
for key, rs in groups.items():
    if len(rs) < 5:
        continue
    raw_seq = []
    for r in rs:
        raw_seq.append([(s.get("v") if s else None)
                        for s in (r.get("skills") or [])])

    # 重放稳定化
    st = SkillStabilizer()
    stab_seq = []
    for r in rs:
        frame = []
        for s in (r.get("skills") or []):
            if not s:
                frame.append(None)
            else:
                frame.append({"value": s.get("v"),
                              "kind": "num" if s.get("v") is not None
                              else "dash",
                              "index": s.get("i")})
        out = st.update(frame, pet=key[0])
        stab_seq.append([(o.get("value") if o else None) for o in out])

    r_rate, r_s, r_c = stability(raw_seq)
    s_rate, s_s, s_c = stability(stab_seq)
    tot_raw[0] += r_s
    tot_raw[1] += r_c
    tot_stab[0] += s_s
    tot_stab[1] += s_c
    print("  %-14s n=%-3d 原始 %3.0f%% -> 稳定化 %3.0f%%  (%+.0f)" % (
        "%s/%s" % (key[0], key[1]), len(rs), r_rate, s_rate, s_rate - r_rate))

print()
raw_all = 100.0 * tot_raw[0] / max(1, sum(tot_raw))
stab_all = 100.0 * tot_stab[0] / max(1, sum(tot_stab))
print("=== 总体 ===")
print("  原始稳定率   %5.1f%%  (未变 %d / 变化 %d)" % (
    raw_all, tot_raw[0], tot_raw[1]))
print("  稳定化后     %5.1f%%  (未变 %d / 变化 %d)" % (
    stab_all, tot_stab[0], tot_stab[1]))
print("  提升         %+.1f 个百分点" % (stab_all - raw_all))
