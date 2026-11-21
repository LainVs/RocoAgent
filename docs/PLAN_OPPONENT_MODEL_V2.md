# 计划：对手行为模型改读 opponent_actions.jsonl

## 发现的根本问题

### 问题 1：数据源错了

`opponent_model.py` 从 `turns.jsonl` **反推**对面行为。但 `turns.jsonl` 是
轮询日志，而且**多个机器人进程同时写**：

```
原始 3110 条，按 ts 排序后 turn 倒退 806 次
同一时间戳出现多条的: 54 个时刻
  01:51:08 x2: [(7, None), (1, None)]        <- 两个进程
  03:04:24 x3: [(1, None), (2, None), (3, None)]
```

`build()` 只按 `turn==1` 切分场次，遇到交错数据就完全错位。
结果 `opponent_action(a, b)` 拿两个不同局、不同进程的记录做比较。

**直接后果**：`p_buff` 虚高到 0.50~0.57（真实 buff 技能只占很小比例）。
原因是相邻记录血量都没变 -> 判成 buff。

### 问题 2：已有更好的数据源没被用

`logs/opponent_actions.jsonl` 是 `opponent_log.py` **专门为这件事**采集的：

```
共 210 条
字段: act, foe, foe_conf, foe_pct_after, foe_pct_before,
      foe_types, match, me, my_pct_after, my_pct_before,
      switched_to, t, turn

act 分布: switch 92, attack 65, buff 53
match 分布: 3 局（105 + 95 + 10 条）
```

特点：
- **有 `match` 字段**，场次边界明确，不需要靠 turn 序列猜
- **`act` 已经是标签**，不需要从血量变化反推
- **`me` 已是种族名**（`音速犬`），不带 `#编号`
- **每条只记一次**（不是轮询），没有重复

## 修复方案

### 1. `opponent_model.py` 主数据源改为 `opponent_actions.jsonl`

```python
def load_actions():
    """读 opponent_actions.jsonl（对面行为的权威记录）。"""
```

保留 `turns.jsonl` 反推作为**降级路径**（文件不存在时），但主路径用前者。

### 2. 按 `match` 分组，不靠 turn 序列

```python
for r in rows:
    match = r.get("match")
    ...
```

`match` 缺失时（旧数据）才退回按 `turn==1` 切分。

### 3. 保留归一化和假人排除

即使新数据源已经干净，归一化仍然需要（旧数据 / 降级路径）。
`ALIASES` 和 `DUMMY_FOES` 保留。

### 4. 验证 `p_buff` 是否回到合理区间

改完后重跑，`p_buff` 应该在 0.15~0.30 之间（53/210 = 25% 是全局平均）。
如果仍 > 0.5，说明还有别的判定问题。

## 验收标准

1. `p_buff` 全局平均接近 53/210 = 25%
2. 达到 MIN_SAMPLES 的组数增加（210 条分到更少的组里）
3. 单测覆盖：match 分组、缺 match 时的降级、归一化、假人排除
4. `python src\opponent_model.py --stats` 输出合理

## 不做的事

- 不改 `opponent_log.py`（采集端已经正确）
- 不删除 `turns.jsonl` 反推逻辑（作为降级路径保留）
- 不降低 MIN_SAMPLES（等数据够了再说）
