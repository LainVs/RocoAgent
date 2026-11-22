# 决策层承伤估计改为概率加权（2026-11-22 实测定案）

> 代码：`pvp_engine.weighted_incoming` + `policy_v2._incoming`
> 触发：机器人对局复盘发现"明明能站桩输出却频繁换人"。

---

## 一、实测依据（12 组真实对位）

用 `logs/opponent_actions.jsonl` 的实际掉血（my_pct 前后差 × my_team
实测血量）对照两种估计器：

```
me           foe          n  obs_avg   worst  expected  exp/obs
音速犬        白发路路      8     116      179       81    0.70
音速犬        海豹船长      5     170      275      136    0.80
音速犬        画间沉铁兽    4     168      225       75    0.45
音速犬        寂灭骨龙      4     131      242      119    0.91
恶魔狼        海豹船长      4      82      355      128    1.56
恶魔狼        火羽          3      84      154       45    0.53
音速犬        叮叮卯        3     204      126       59    0.29
音速犬        雪影冰灵      2     187      199       71    0.38
```

结论：

- `worst_incoming`（旧实现）高估实伤 **1.5~3 倍**：它对对面整个可学
  技能池取 max，而池里大半技能对面实际不带。EV 模型因此把承伤项
  算得过大 → 换人/防御被过度鼓励。
- 纯期望会**漏掉致命技能**：叮叮卯 exp/obs=0.29 —— 学习分布没收敛
  时，期望把对面偶尔的大招平均掉了，机器人会站在必死范围里。

## 二、设计：混合估计

```
incoming = max( 期望承伤, blend × 最坏承伤 )      blend 默认 0.75
```

- **地板**（`0.75×worst`）保留"最坏情况也扛得住"的安全语义——
  绝不在必死范围里站桩；
- **期望**把常见对位从 3 倍高估拉回现实（白发路路 179→134，
  寂灭骨龙 242→182，海豹船长 355→266）；
- `blend` 外置在 `config/policy_weights.json`（键 `incoming_blend`），
  默认 0.75，后续可用日志拟合。

返回 `(dmg, source)`，source ∈ {"weighted", "worst", "assumed"}，
进 `policy_v2` 的 detail，turns.jsonl 可追踪。

## 三、安全边界

| 场景 | 行为 |
|---|---|
| 对面身份未知 | `worst=0` → 返回 (0,"worst") → 上层走 `unknown_incoming_pct` 兜底（不变） |
| 对面不在数据库 | 同上 |
| 学习分布为空（新对手） | exp=0 → 返回最坏值（保守方向） |
| 学习分布噪声大 | 有 0.75×worst 地板，期望再离谱也不会低于地板 |

必死保护/换人生存判定仍用**这一条承伤数字**，没有任何分支绕过地板——
安全语义没有放松，只是把"常态高估"修掉了。

## 四、验证

- `test_policy`（8 用例）ALL PASS——包括"未识别对手走 assumed 兜底"
- `test_decide` / `test_priority` / `test_pvp_meta` / `test_imports` ALL PASS
- API 抽查：已知对位出 weighted 值，未知物种回落 worst（见上表）

## 五、后续

1. 跑一晚后用 turns.jsonl 复盘：`incoming` 字段（weighted 值）vs
   当回合实际掉血，若 `weighted/observed` 系统性 <0.8，把 blend 降到 0.6
2. `switch_margin` / `buff_survive_floor` 这些依赖承伤的阈值可能要
   随新口径重新标定（它们原来是在 worst 口径下调出来的）
