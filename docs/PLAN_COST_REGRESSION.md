# 计划：修复新版策略丢失能耗信息（cost 永远为 0）

## 用户的问题

"初版机器人都知道用字面伤害更高的，现在的却做不到的时候了"

## 根因（已确认，2026-11-21 09:00）

### 初版怎么做（`policy_legacy.decide`）

```python
costs = ctx.get("card_costs") or {}    # 来自 my_card_costs()，查表
lam = 3.0

def _net(p):
    c = costs.get(p[0]["index"], 0) or 0
    return p[1] - lam * c              # 每点能耗扣 3 点等效伤害

pool = sorted(damaged, key=lambda p: (p[0].get("value") is not None,
                                      _net(p),
                                      p[0]["color"] != "red"),
              reverse=True)
```

`card_costs` 来自 `my_card_costs()`：从 `team_db` 的队伍配置
（moves 的 id 顺序 = 卡片顺序）查 `skills.cost`，**真实耗能**。

### 新版怎么做（`robot._policy_state`）

```python
cost = 0
try:
    meta = E.D.skill(s.get("name") or "")     # <== 这里
    if meta:
        cost = int(meta.get("energy_consumption") or 0)
except Exception:
    pass
```

**机器人不读技能名**。`turns.jsonl` 里 skills 的字段是：

```
['c', 'i', 'lit', 'plate', 't', 'v']
```

没有 `name`。所以 `s.get("name")` 永远是 `None`，
`E.D.skill(None)` 返回 `None`，**`cost` 永远是 0**。

### 后果

新版 `policy_v2` 里：

```python
cost = s.get("cost") or 0              # 恒为 0
energy_after = max(0, energy - cost)   # 恒等于 energy
```

`_outcome` 的 `w_energy` 项对所有技能**完全相同**，失去区分度。
也就是说：**新版完全不知道技能耗能**。

### 但这不足以解释"选了低伤害"

即使 cost 全是 0，伤害项仍然起作用：

```python
w_damage_dealt * _c01(our_dmg)      # 0.25 * dmg/foe_max
```

360 伤害 vs 150 伤害，前者得分明显更高。所以纯决策层面新版也会选高的。

**实测验证**（`scripts/compare_policy_versions.py`）：
6 个场景下两版选择**完全一致**，包括 360 vs 150。

### 真正的退化点

`cost` 恒为 0 导致：
1. 高耗技能不再被惩罚 -> 倾向打大技能，能量耗尽
2. 能量耗尽后所有卡 `lit=False` -> 只能聚能 -> 空转一回合

这是**间接**影响：不是"选了低伤害"，而是"打了高耗技能后能量不够，
下一回合只能聚能"。

## 修复方案

### 1. 用 `my_card_costs()` 拿真实耗能（与初版一致）

```python
slot_costs = my_card_costs(my_name)     # 已经在函数开头调用了

skills.append({
    "i": s["index"], "dmg": dmg,
    "cost": slot_costs.get(s["index"], 0),   # <== 改这里
    "kind": kind, "lit": bool(s.get("lit", True)),
})
```

注意：`slot_costs` 在 L1020 已经算了（给 dash 分支用），
但伤害技能分支又用了另一条错误路径。

### 2. 加回归测试

`src/test_policy_cost.py`：
- 构造 300/cost6 vs 150/cost2，确认策略考虑能耗
- 构造 cost 全为 0 的情况，确认不会选错

### 3. 加断言：cost 不应全为 0

在 `_policy_state` 里，如果所有技能的 cost 都是 0 而 `slot_costs` 非空，
记一条警告日志。这样下次退化能立刻发现。

## 验收标准

1. `_policy_state` 产出的 skills 里 `cost` 来自 `my_card_costs`，非 0
2. 单元测试覆盖"高耗 vs 低耗"场景
3. 实盘日志里 `cost` 字段正确

## 不做的事

- 不改 `policy_v2` 的评分公式（它本身没问题）
- 不读技能名（OCR 成本高，且查表更可靠）
