# -*- coding: utf-8 -*-
"""用实盘真实序列逐步重放 SkillStabilizer，定位为什么没有覆盖。

关键问题：回测说能提升 40 个百分点，但实盘 5 分钟一条 STAB 日志都没有。
本脚本用真实序列逐帧打印，看稳定化在每个槽位做了什么。
"""
import json
import os
import sys

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

# 取一段连续的同一精灵记录（07:31 之后，新代码期间）
sel = [r for r in rows
       if (r.get("ts") or "") >= "07:31" and r.get("our_pet")]
print("07:31 之后有 pet 的记录 %d 条" % len(sel))
if not sel:
    print("没有数据")
    sys.exit(0)

# 找最长的同 pet 连续段
best = []
cur = []
prev = None
for r in sel:
    if r.get("our_pet") == prev:
        cur.append(r)
    else:
        if len(cur) > len(best):
            best = cur
        cur = [r]
        prev = r.get("our_pet")
if len(cur) > len(best):
    best = cur

print()
print("=== 最长同精灵连续段：%s，%d 帧 ===" % (best[0].get("our_pet"), len(best)))

st = SkillStabilizer()
pet = best[0].get("our_pet")
for n, r in enumerate(best):
    frame = []
    for s in (r.get("skills") or []):
        if not s:
            frame.append(None)
        else:
            frame.append({"value": s.get("v"),
                          "kind": "num" if s.get("v") is not None else "dash",
                          "index": s.get("i")})
    before = [None if not f else f.get("value") for f in frame]
    out = st.update(frame, pet=pet)
    after = [None if not o else o.get("value") for o in out]
    mark = "  <== STAB" if before != after else ""
    print("  帧%-3d T%-3s raw=%-22s out=%-22s%s" % (
        n + 1, r.get("turn"), before, after, mark))

print()
print("stats:", st.stats())
