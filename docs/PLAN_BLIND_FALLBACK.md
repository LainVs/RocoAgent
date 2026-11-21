# 计划：读不出数字时的兜底（不再硬编码 slot 2）

> 2026-11-21 · 代码：`src/policy_legacy.py`（`config_best_slot`）、`src/robot.py`
> 测试：`src/test_decide.py` 新增 4 项

---

## 0. 问题

`logs/turns.jsonl` 去重后 3178 条决策里，**91 条（2.9%）** 走的是
`blind fallback` —— 卡面亮着，但伤害数字一个都读不出来。

旧兜底逻辑（robot.py）：

```python
# Cards are lit but we cannot tell which one hurts. Slot 2 is
# the main attack on every pet in this team, so default to it;
# only fall back to another lit slot if 2 is known to be dark.
```

注释说"slot 2 是全队主攻"。**但队伍配置里不是这样**：

| 精灵 | slot1 | slot2 | slot3 | slot4 | slot2 是最强吗 |
|---|---|---|---|---|---|
| 音速犬 | 0（借用） | **140** | 80 | 100 | 是 |
| 银月狼王 | 120 | **130** | 120 | 80 | 是 |
| 恶魔狼 | **150** | 100 | 65 | 80 | 否 |
| 卡瓦重 | 80 | 60 | 90 | **105** | **否，最弱** |
| 冰钻布鲁斯 | **90** | 70 | 75 | 60 | 否 |

所以这个硬编码对 3/5 的精灵是错的：卡瓦重每回合少打 **45 点**，
恶魔狼少打 **70 点**。

---

## 1. 改法

新增 `policy_legacy.config_best_slot(cards, powers, costs, lam=3.0)`：
在**亮着的**卡里，按队伍配置挑 `power - λ×耗能` 最高的那个。

`robot._config_best_slot()` 负责取数据：
- `powers` 来自 `perceive._skill_meta(my_name)`（已按 `my_name` 缓存 30s）
- `costs` 来自 `my_card_costs(my_name)`（缓存 60s）

配置查不到时（换过队伍 / 名字还没认出来）退回旧规则，并保留"slot2 暗了
就换别的"的判断 —— 不改变原有兜底能力。

---

## 2. 实测影响

回放 91 条 `blind fallback`：

```
旧兜底实际打的槽位: {2: 15, 1: 25, 3: 5, 4: 39}
```

（不是全部打 slot 2 —— 因为还有 `cached` 分支和"slot2 暗了就换"分支。）

其中 5 条能对上当前队伍配置（其余记录 `our_pet` 是 None 或模板名
"wolf" 这类查不到配置的），**这 5 条全部会改**：

```
恶魔狼   slot4 -> slot1   power 80 -> 150   ×5
```

样本小是因为历史记录里我方身份经常没认出来，不是改动本身小。

---

## 3. 顺手发现但**故意没改**的一项

`policy_legacy.decide()` 里有这套算法：

```python
# 净收益 = 伤害 - λ×耗能；1 费打 168 不该输给 2 费打 160
def _net(p):
    return p[1] - lam * (costs.get(p[0]["index"], 0) or 0)
```

但 `ctx` 里**从来没有 `card_costs`**，所以 `costs.get()` 恒返回 0，
λ 项**实际一直是 0**。这段注释描述的行为从未生效。

### 为什么没顺手补上

回放 2990 条有可读数字的历史决策，补上 `card_costs` 会改变
**224 条（7.5%）的主路径选择**：

```
slot2 -> slot1  x48     slot1 -> slot2  x40
slot3 -> slot4  x35     slot4 -> slot3  x23
slot4 -> slot2  x23     ...
```

7.5% 是一个很大的行为改动，而：

- λ=3.0 只是经验值，没有实盘验证
- 队伍配置本身曾经长期错误（`docs/PLAN_SKILL_KIND.md` 记了这件事），
  耗能查表的可靠性存疑
- 一旦选错技能，代价是直接丢一回合

**结论**：这是需要用 A/B 验证的建模改动，不该和"盲打兜底"这种逻辑上
明确更优的修正混在一起上线。现在有 `matches.py` 的场次统计，具备做
A/B 的基础了，等有实盘数据再单独评估。

代码里在 `ctx` 构造处写了完整注释说明这一点，避免下次有人"顺手补上"。

---

## 4. 测试

`src/test_decide.py` 新增 4 项：

```
卡瓦重 80/60/90/105 -> slot 4（旧代码给 2）
音速犬 0/140/80/100 -> slot 2（借用不可用）
slot4 暗掉 -> 退回 slot 3
全暗       -> None（调用方走聚能）
```

22 个 python 脚本全过。

---

## 5. 还没做

- **`cached plan` 分支同样值得审视**：读不出数字时优先重放上一手的技能。
  如果上一手是刚才用过但效果不好的，会一直重放。目前没有证据说明它有害，
  但也没有证据说明它好。
- 盲打率本身（2.9%）能不能降下来，还是要靠感知层。
