# -*- coding: utf-8 -*-
"""诊断对手行为模型的样本分布。

问题（2026-11-21）：
    · 243 组对位，但没有一组达到 MIN_SAMPLES=12
    · 最多的只有 11 条（音速犬#5 | 60级水桶模型）
    · p_buff 高达 0.455 —— 疑似把复制技能误判成 buff

本脚本输出样本分布，判断瓶颈在哪。
"""
import sys
from collections import Counter

sys.path.insert(0, "src")
import opponent_model as OM

rows = OM.dedupe(OM.load_turns())
model, total = OM.build(rows)

print("总样本 %d，对位数 %d" % (total, len(model)))
print("MIN_SAMPLES = %d" % OM.MIN_SAMPLES)

# 样本数分布
cnt = Counter()
for key, v in model.items():
    n = v["n"]
    if n >= 12:
        cnt["12+"] += 1
    elif n >= 8:
        cnt["8-11"] += 1
    elif n >= 4:
        cnt["4-7"] += 1
    elif n >= 2:
        cnt["2-3"] += 1
    else:
        cnt["1"] += 1
print()
print("=== 每组样本数分布 ===")
for k in ("12+", "8-11", "4-7", "2-3", "1"):
    print("  %-6s %d 组" % (k, cnt.get(k, 0)))

print()
print("=== 样本最多的 12 组 ===")
top = sorted(model.items(), key=lambda kv: -kv[1]["n"])[:12]
for key, v in top:
    print("  n=%-3d %-34s attack=%.2f switch=%.2f buff=%.2f" % (
        v["n"], key, v["p_attack"], v["p_switch"], v["p_buff"]))

print()
print("=== 分解：对位键的构成 ===")
me_part = Counter()
foe_part = Counter()
for key in model:
    if "|" in key:
        a, b = key.split("|", 1)
        me_part[a] += 1
        foe_part[b] += 1
print("  我方（立绘名）种数: %d" % len(me_part))
for k, n in me_part.most_common(8):
    print("    %-20s %d 组" % (k, n))
print("  对手种数: %d" % len(foe_part))
for k, n in foe_part.most_common(8):
    print("    %-20s %d 组" % (k, n))

print()
print("=== 瓶颈判断 ===")
print("  我方名字带 #编号（同一精灵多个立绘模板），把样本切碎了：")
dup = [k for k in me_part if "#" in k]
print("    带 # 的我方名: %d 个，占 %d 组" % (
    len(dup), sum(me_part[k] for k in dup)))
print("  如果按种族名合并，组数会从 %d 降到约 %d" % (
    len(model), len({k.split("#")[0].split("|")[0] + "|"
                     + k.split("|", 1)[1] for k in model})))
