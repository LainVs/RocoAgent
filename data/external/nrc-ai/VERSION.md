# 外部资产：NRC_AI

> 来源：https://github.com/ColinHong10/NRC_AI
> 拉取日期：2026-11-21
> 许可证：**MIT**（可自由复制、修改、商用，需保留版权声明）
> 体积：2.44 MB / 54 个文件

## 为什么拉进来

这是目前找到的**最完整的洛克王国战斗效果引擎**：

| 能力 | 规模 |
|---|---|
| 效果原语 | 100+ 种，数据驱动，零硬编码 |
| 技能效果配置 | 472 / 495 |
| 特性效果配置 | 68 / 170 |
| 印记系统 | 12 种完整实现 |
| 战斗引擎 | `battle.py` 1869 行 |
| AI | MCTS，每回合 150 次模拟 |
| 测试 | 14 文件 / 107 用例 |

我们的 `simulator.py` 只算伤害（504 行，无任何效果）。
NRC_AI 可以直接作为效果系统的设计蓝本或整体移植的基础。

## 目录结构

```
data/external/nrc-ai/
├── src/
│   ├── effect_models.py          29 KB  效果原语定义（E 枚举 / Timing / EffectTag）
│   ├── effect_engine.py           1 KB  执行引擎（Handler 注册表）
│   ├── effect_data.py            66 KB  手工配置：技能 59 + 特性 68
│   ├── skill_effects_generated.py 33 KB  自动生成：455 个技能
│   ├── battle.py                 78 KB  战斗逻辑 + 印记 + 回合流程
│   ├── models.py                 22 KB  Pokemon / Skill / BattleState
│   ├── mcts.py                   18 KB  对抗式 MCTS
│   ├── server.py                 58 KB  FastAPI + WebSocket
│   ├── engine/_monolith.py      158 KB  （合并版，与上面重复）
│   └── main.py / pokemon_db.py / skill_db.py / team_builder.py
├── data/
│   ├── nrc.db                  1.19 MB  SQLite：461 精灵 / 491 技能 / 20433 关联
│   ├── skills_all.csv            43 KB
│   ├── spirit_evolution.csv      74 KB
│   └── raw/skills_wiki.csv      211 KB
├── tests/                        18 文件
├── scripts/                      10 个爬虫/生成器
├── docs/                         设计文档
├── README.md / ROADMAP.md / LICENSE / requirements.txt
```

## nrc.db 表结构

```
pokemon           461 行    精灵（种族值/属性/特性）
skill             491 行    技能（威力/耗能/属性）
pokemon_skill   20433 行    精灵×技能学习关系
evolution         206 行    进化链
```

## 效果原语设计（值得借鉴）

所有技能/特性效果都拆成 `EffectTag` 列表：

```python
EffectTag = (效果类型, 参数字典, 触发条件, 子效果列表)
```

核心枚举（`effect_models.py`）：

```python
# 伤害/回复
E.DAMAGE, E.HEAL_HP, E.HEAL_ENERGY, E.STEAL_ENERGY
E.ENEMY_LOSE_ENERGY, E.LIFE_DRAIN, E.GRANT_LIFE_DRAIN

# 属性修正
E.SELF_BUFF, E.SELF_DEBUFF, E.ENEMY_DEBUFF

# 状态
E.POISON, E.BURN, E.FREEZE, E.LEECH, E.METEOR

# 印记（12 种，全队共享，换人不消失）
E.POISON_MARK, E.DRAGON_MARK, E.WIND_MARK, E.CHARGE_MARK,
E.SOLAR_MARK, E.ATTACK_MARK, E.SLOW_MARK, E.SLUGGISH_MARK,
E.SPIRIT_MARK, E.METEOR_MARK, E.THORN_MARK, E.MOMENTUM_MARK

# 机制
E.DAMAGE_REDUCTION, E.FORCE_SWITCH, E.FORCE_ENEMY_SWITCH, E.INTERRUPT

# 动态计算
E.POWER_DYNAMIC, E.ENERGY_COST_DYNAMIC, E.PERMANENT_MOD,
E.SKILL_MOD, E.NEXT_ATTACK_MOD, E.ENERGY_ALL_IN
```

配置一处技能只需 1~2 行：

```python
"蝙蝠": [SE(SkillTiming.ON_USE, [T(E.DAMAGE), T(E.LIFE_DRAIN, pct=1.0)])],
"吞噬": [SE(SkillTiming.ON_USE, [T(E.DAMAGE)]),
         SE(SkillTiming.ON_HIT, [T(E.HEAL_ENERGY, amount=6)], on_kill=True)],
```

## 我们实盘队伍的覆盖情况（实测）

**19 个技能：17 个已配置**，只有「月蚀」「撞鬼」缺（这两个 `desc` 本来就是空的）。

**5 个特性：4 个已配置**：
```python
"专注力": 入场首回合物攻 +100%
"冰钻":   敌方技能总能耗每点，攻击威力 +10%
"诈死":   力竭时少损失 1 点魔力
"悲悯":   每只力竭的己方精灵，双攻 +30%
# 缺 "铭记于月亮"（银月狼王）—— roco-cal 也承认这个没实现
```

## 已知缺陷（用之前必须知道）

自动生成的部分（`skill_effects_generated.py`）**有漏**：

| 技能 | roco-cal 记录的效果 | NRC_AI 配置 |
|---|---|---|
| 筛管奔流 | 生命>80% 时威力 +75 | ❌ 只有 DAMAGE |
| 丢冰块 | 敌方速度 -30 | ❌ 只有 DAMAGE |

我们手上的 roco-cal 数据有这些（`triggered` / `buff_effects` 字段），
两者互补：**用 NRC_AI 的引擎框架 + roco-cal 的效果数据**。

## 整合时的注意

1. **不要用它的伤害公式**。我们用 roco-cal 移植的 `damage_v2`（用户口径：
   "存疑的以 roco-cal 为准"）。只搬效果系统。
2. **它是 MCTS，我们是规则策略**。两者可并存：先让规则策略更准，
   MCTS 作为可选增强。
3. `src/engine/_monolith.py`（158KB）与分文件版本重复，移植时只用分文件版。

## 更新方式

GitHub `codeload` 和 `git clone` 走代理都不稳定（实测 early EOF / 超时）。
用项目内脚本逐文件拉：

```powershell
& $py scripts\gh_fetch.py ColinHong10/NRC_AI data\external\nrc-ai src data docs tests scripts
& $py scripts\raw_get.py  ColinHong10/NRC_AI data\external\nrc-ai README.md ROADMAP.md LICENSE requirements.txt
```

`gh_fetch.py` 走 `api.github.com` contents 接口（>1MB 自动转 raw），
`raw_get.py` 对 `raw.githubusercontent.com` 的 SSL 抖动做 6 次重试。
两个都写死了代理 `http://127.0.0.1:7897`（Clash）。

## 许可与致谢

[MIT License](https://github.com/ColinHong10/NRC_AI/blob/main/LICENSE)，
Copyright (c) 2026 ColinHong。本项目仅用于个人研究。
