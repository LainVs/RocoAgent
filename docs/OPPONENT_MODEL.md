# 对手行为模型：概率分布 + 分层池化

> 代码：`src/opponent_log.py`（采集）→ `src/opponent_model.py`（建模）
> → `src/anticipate.py`（预判）→ `src/policy_v2.py`（用进决策）
> 测试：`src/test_anticipate.py`、`src/test_opponent_model.py`

用户口径：

> 我们这个精灵 对位对面这个精灵 对面行为的概率分布 我们可以统计下来
> A 放技能 a.1 攻击技能 a.2 防御技能 a.3 状态技能 B 换人 换哪些人 概率分布

---

## 1. 采集：`opponent_log.Recorder`

每回合记录"对面做了什么"到 `logs/opponent_actions.jsonl`：

```json
{"t":"01:30:12","match":12,"turn":3,
 "me":"音速犬","foe":"白发路路","foe_types":["恶"],
 "my_pct_before":100.0,"my_pct_after":84.2,
 "foe_pct_before":99.6,"foe_pct_after":99.6,
 "act":"attack","switched_to":null}
```

分类规则（`classify`）：

1. 对面换了精灵 → `switch`
2. 我掉血（>0.5 个百分点）→ `attack`
3. 双方血量都不变 → `buff`（增益/防御/聚能）
4. 其它 → `unknown`（**不写盘**，避免噪声）

训练假人（`60级水桶模板`）会被排除：它行为固定（只会攻击），
混进来会把整体概率拉向"对面一定攻击"。

---

## 2. 建模：三层概率 + Dirichlet 平滑

`opponent_model.build_from_actions(rows)` 输出：

```
"音速犬|白发路路"     对位层（具体这只 vs 这只）
"__foe__|白发路路"    对面层（这只对面精灵 vs 所有我方）
"__global__|__all__"  全局层
```

每条包含：

```
n            真实样本数（对位层是原始条数，不是平滑后的）
reliable     n >= MIN_SAMPLES(12)
p_attack / p_switch / p_buff
switch_top   对面换人时最常换上的前三 [[名字, 次数], ...]
next_types_top
level        pair / foe / global
```

### 为什么必须池化

实测（2026-11-22）：`opponent_actions.jsonl` 有 320 条 / 115 组对位，
**只有 6 组**达到 12 条。如果只看对位层，`anticipate` 几乎永远返回 None。

对位层用 Dirichlet 平滑：

```
p = (count + K * prior) / (n + K)     K = 8
prior = 同一只对面精灵的总体分布（对面层）
```

效果：对位只有 1 条样本时，概率会被拉向"这只精灵的总体习惯"，
而不是变成 100%/0%。

---

## 3. 预判：`anticipate.advice`

回退顺序：

```
对位层 n>=12  -> level="pair"
对面层 reliable -> level="foe"（confidence 强制 low）
对位层 n>=3   -> level="weak"（低置信）
全局层 reliable -> level="global"（low）
否则 None（不猜）
```

判定：

| 条件 | 建议 |
|---|---|
| `p_buff >= 0.55` | `buff`：对面这回合打不到我们，可以叠 buff |
| `p_switch >= 0.55` | `switch`：预判他换上谁（`target`），换成能压制的 |
| `p_attack >= 0.55` 且我血 < 35% | `switch`：扛不住 |
| 都不满足 | None（没有明显倾向，不预判） |

---

## 4. 用进决策：`policy_v2.decide`

1. **攻击候选**：`opp_dmg` 用 `inc * max(p_attack, 0.35)`（期望承伤），
   而不是固定吃满。斩杀/必死判定仍用最坏值 `inc`，不放松生存约束。
2. **叠 buff**：`p_attack >= 0.7` 时，要求多 0.5 回合的生存
   （`survive_turns >= need_turns + 0.5`），因为对面大概率在打我们。
3. 决策日志会带上预判：

```
T3 SKILL 2 (...) [v2 0.31] [对手 攻62% 换21% 增17% pair n=14]
```

4. 每局结束 `_end_match()` 自动重建模型（<50ms），不需要手动 `--build`。

---

## 5. 已知限制

1. **样本仍然少**：目前 6 组达标。随着整晚跑会持续增长。
2. **`buff` 包含"防御/聚能"**：目前合并成一类。用户要的
   `a2 防御 / a3 状态` 细分需要更细的观测（血量不变时无法区分
   防御和聚能）—— 需要读 buff 图标，见 `docs/` 里 buff 识别的 TODO。
3. **换人目标**用 `switch_top`（历史频率），不是"当前局面下最优换人"。
4. **对手改名**不影响：键是种族名（来自立绘/特性反查），不是 HUD 昵称。

---

## 6. 命令

```powershell
python src\opponent_model.py --stats     # 看当前模型
python src\opponent_model.py --build     # 手动重建（机器人每局会自动重建）
python src\anticipate.py                 # 自检 + 看几组对位的建议
python src\test_anticipate.py
```
