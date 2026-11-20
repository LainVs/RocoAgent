# -*- coding: utf-8 -*-
"""诊断：技能卡伤害读数的稳定性。

现象（2026-11-21 实盘）：
    06:51:52 T8   SKILL 3 (skill 3: 420 dmg, kill)
    06:52:15 T9   SKILL 1 (skill 1: 153 dmg, slower)

需要判断：420 是误读，还是两只不同精灵的不同卡？

turns.jsonl 字段（实测）：
    skills: [{"i":1,"v":285,"c":"green","t":"285","lit":true,"plate":245}, ...]
    注意键名是 v（不是 value），t 是 OCR 原文。
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, "src")

TURNS = os.path.join("logs", "turns.jsonl")
rows = []
for line in open(TURNS, encoding="utf-8", errors="ignore"):
    line = line.strip()
    if not line:
        continue
    try:
        rows.append(json.loads(line))
    except Exception:
        continue

print("turns.jsonl 共 %d 条" % len(rows))

# 按精灵分组统计读数（同一只精灵的卡面伤害应该是固定的）
by_pet = defaultdict(lambda: defaultdict(list))
for r in rows:
    pet = r.get("our_pet") or "?"
    for s in (r.get("skills") or []):
        if not s:
            continue
        v = s.get("v")
        i = s.get("i")
        if v is None or i is None:
            continue
        by_pet[pet][i].append(v)

print()
print("=== 按精灵分组的读数分布（同一张卡应稳定）===")
for pet, slots in sorted(by_pet.items()):
    print()
    print("  [%s]" % pet)
    for i in sorted(slots):
        vals = slots[i]
        cnt = defaultdict(int)
        for v in vals:
            cnt[v] += 1
        top = sorted(cnt.items(), key=lambda x: -x[1])
        spread = ""
        if len(top) > 1:
            total = sum(cnt.values())
            main_n = top[0][1]
            if main_n / total < 0.8:
                spread = "  <== 抖动"
        print("    slot%d n=%-4d %s%s" % (
            i, len(vals),
            ", ".join("%s×%d" % (k, n) for k, n in top[:5]), spread))

# 找 OCR 原文与数值不一致的（说明解析有问题）
print()
print("=== OCR 原文 vs 解析值 不一致的记录 ===")
bad = 0
for r in rows:
    for s in (r.get("skills") or []):
        if not s:
            continue
        t = s.get("t")
        v = s.get("v")
        if t is None or v is None:
            continue
        digits = "".join(ch for ch in str(t) if ch.isdigit())
        if digits and str(v) != digits:
            bad += 1
            if bad <= 8:
                print("  %s %s slot%s: t=%r v=%r" % (
                    r.get("ts"), r.get("our_pet"), s.get("i"), t, v))
print("  共 %d 处" % bad)
