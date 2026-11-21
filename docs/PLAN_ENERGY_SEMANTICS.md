# 计划：修正 energy 语义（点亮数 vs 能量值）

## 问题（2026-11-21 08:50 实测确认）

`energy.read_energy()` 返回的是**点亮的能量点数（0~6）**：

```python
PIP_COUNT = 6
def read_energy(rgb, band=None):
    """Number of lit pips (0..6), or None when the row cannot be read."""
```

但下游把它当成了**能量数值**在用：

```python
# robot.py L1067
"energy": st.get("energy") if st.get("energy") is not None else 10,

# policy_v2.py L121
+ w.get("w_energy", 0.03) * _c01(energy_after / 10.0)   # 假设满值 10
```

游戏里技能耗能是 3/5/6，能量上限 10。**点亮数 1 ≠ 能量 1**。

## 影响评估

### 已确认不影响决策的部分

伤害技能循环**只检查 `lit`，不检查 `cost > energy`**：

```python
for s in me.get("skills") or []:
    if s.get("kind") != "num" or not s.get("lit", True):
        continue
```

而 `lit` 是可靠的（实测 `lit` 与 `plate>220` 完全一致，3061 on / 775 off，
0 处矛盾）。所以"能不能放"的判断是对的。

### 受影响的部分

只有 `_outcome` 的 `w_energy` 项（权重 0.03，影响很小）：

- `energy_after = max(0, energy - cost)` 里 energy 是点亮数（0~6），
  cost 是真实耗能（0~6）—— 量纲不同，相减无意义
- `_c01(energy_after / 10.0)` 假设满值 10，实际最大 6

实测：`energy=0` 时策略仍然选 `cost=6` 的技能（因为只看 lit）。
这在"卡面亮着但能量不够"的情况下会打空。

### 但也有正向作用

点亮数虽然量纲不对，但它**单调反映了剩余能量**：
点亮越多 = 能量越多。所以 `energy_after` 作为"相对排序"仍有信息量。

## 修复方案

### 方案 A（推荐）：把点亮数换算成能量估算

```python
# config.py
ENERGY_PER_PIP = 10.0 / 6.0     # 6 个点对应 10 点能量

# robot._policy_state
raw = st.get("energy")
if raw is None:
    energy = 10                      # 读不到时按满能量（保守）
else:
    energy = int(round(raw * ENERGY_PER_PIP))
```

这样 `energy_after / 10.0` 的语义就对了。

**风险**：点亮数→能量的换算是线性的吗？需要验证。
如果游戏里是"每点 1.67 能量"，线性成立；如果能量点是分段表示，则不成立。

### 方案 B（保守）：明确标注这是"相对值"，不改换算

```python
# policy_v2._outcome
# energy_after 是"点亮的能量点数"，不是能量值。
# 用 pip 上限归一化，保持相对排序的语义。
+ w.get("w_energy", 0.03) * _c01(energy_after / 6.0)
```

只把分母从 10 改成 6，不改数据源。改动最小，但 `energy - cost` 的相减
仍然量纲不一致。

### 方案 C（最稳）：两者都传

```python
"energy": energy_value,        # 换算后的能量值（用于 cost 比较）
"energy_pips": raw_pips,       # 原始点亮数（用于诊断）
```

## 先验证再改

**关键问题**：点亮数和能量的对应关系是什么？

验证方法：
1. 从 `turns.jsonl` 找连续回合，看点亮数变化与已知耗能是否吻合
   （例如放了 cost=5 的技能后，点亮数应该减少 5/1.67 ≈ 3 个点）
2. 如果吻合 -> 线性换算成立，用方案 A
3. 如果不吻合 -> 用方案 B，只修分母

## 验收标准

1. 验证脚本给出点亮数↔耗能的对应关系
2. 修复后 `energy=0` 时不会选 `cost>0` 的技能（除非所有卡都 lit）
3. 不改变现有正确决策（对比修复前后的决策序列）

## 不做的事

- 不依赖 energy 做"能不能放"的判断（继续用 lit，它更可靠）
- 不调 `w_energy` 权重（那是策略问题，不是 bug）
