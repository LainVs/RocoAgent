# 模拟器实现状态（截至 2026-11-22）

> 这份文件是"我做到哪了"的唯一事实来源。每批改动后必须更新。
> 计划见 `docs/PLAN_FULL_SIM.md`，机制出处见那里。

## 已实现（能跑、有测试）

| 模块 | 内容 | 文件 |
|---|---|---|
| 伤害公式 | 官方 `effective_power × 攻 × 37 / (41 × 防)`；外部标定 114/91 已对上 | `simulator.calc_damage`、`damage_v2` |
| 属性克制 | 双克 3 倍（加算）、双抗 0.25（乘算） | `statmodel.type_effect` |
| 强化/增益 | 每层 +10% 线性叠加，10 层封顶 +100% | `simulator.add_buff` |
| 克制/等级项 | `level_mult` = 1+0.1n（用户实测） | `statmodel.level_mult` |
| 天气 | 5 种（雨天水系 ×1.75；其余为标记） | `battle_fx` |
| 印记 | 4 种（攻击/蓄势/风起/蓄电），手填层数 | `battle_fx` |
| 特性 | roco-cal 261 条里可用 18 条 + nrc-ai 169 条里可用的部分 | `trait_lib`、`nrc_fx` |
| 变化技能 | 攻击/防御/减伤百分比（53 个技能，官方数值） | `skill_buff_calc.json` |
| 异常状态 | 中毒/灼烧（数值原为占位） | `nrc_fx.status_tick` |
| 应对 | 类别判定 + 触发应对效果 | `nrc_fx.counter_fire` |
| 手操对战 | 双立绘 + 逐回合战报 + 字面伤害 + 记录分析 | `manual_battle`、`page-play` |
| 图形资源 | 639 头像 + 584 立绘 + 战斗背景 | `assets/pet_*`、`assets/battle` |

## 本轮（S2）要做

- [x] 异常状态权威数值：毒 3% / 灼 2% + 层数减半 / 冻结 5% / 寄生 2% 吸取
- [x] 属性免疫：毒系免中毒、火系免灼烧、冰系免冻结、草系免寄生
- [x] 换人清增益/减益（印记保留）
- [x] 应对成功必定先手

## 未实现（按批次）

| 批次 | 缺什么 | 卡在哪 |
|---|---|---|
| S3 | 印记 13 种全集、正/负面各 1 条约束、印记触发时机 | 需要印记施加/驱散技能接入 |
| S4 | 先手/迸发/蓄力/打断/吸血/传动/过载/巧变/选择 | 需要"本回合状态机"扩展（蓄力跨回合） |
| S5 | 4 格生命制胜负、场地亲和 ±25/50 | 简单，可随时做 |
| S6 | 特性全量（243 条依赖状态机） | 依赖 S3/S4 |
| S7 | 解包数据接驳 | 需要跑 rocom-parse（dotnet + CUE4Parse 分支） |

## 数据源速查

```
data/external/nrc-ai/            手写技能/特性语义（MIT）
data/external/roco-cal/          结构化特性、天气/印记、伤害引擎、S5 技能表
data/external/rocom-data/        169 套真实阵容（无 LICENSE，只取数据）
data/external/rocoarena/         15 套天梯快照
data/external/rkpp-official/     官方数据包（技能/buff/使用率）
data/nrc_effects.json            nrc-ai 导出（139 技能 / 169 特性）
data/traits.json                 roco-cal 特性 + 3 条文本推导
data/s5_skills.json              S5 每只精灵的完整技能表
data/skill_buff_calc.json        53 个变化技能的官方增益
```

## 测试基线（每次改完必须全绿）

```
test_imports / test_statmodel / test_decide / test_policy_cost
test_perceive / test_switch_panel / test_sim_engine
test_damage_model / test_traits / test_nrc_effects
```

黄金基线：`data/fixtures/sim_baseline.json`（15 行，引擎行为锁死）。
**改引擎后如果基线变了，必须先解释每一行变化，再重拍。**
