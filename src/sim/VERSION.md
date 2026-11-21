# src/sim —— 战斗模拟器（效果引擎移植自 NRC_AI）

> 上游：https://github.com/ColinHong10/NRC_AI
> 许可证：**MIT License**，Copyright (c) 2026 ColinHong
> 移植日期：2026-11-21
> 移植范围：9581 行 / 9 个文件

---

## 一、搬了什么

| 文件 | 行数 | 作用 |
|---|---|---|
| `models.py` | 542 | `Pokemon` / `Skill` / `BattleState` / `Type` |
| `effect_models.py` | 456 | `E`(197 原语) / `Timing`(14) / `SkillTiming`(6) |
| `effect_engine.py` | 37 | re-export 兼容层 |
| `engine/__init__.py` | 60 | 包导出 |
| `engine/_monolith.py` | 3822 | **220 个 handler**（效果执行核心） |
| `effect_data.py` | 1944 | 手动配置：技能 139 + 特性 169 |
| `skill_effects_generated.py` | 660 | 自动生成：359 技能 |
| `skill_db.py` | 191 | 技能加载 + 效果注入 |
| `battle.py` | 1869 | 回合流程 / 先手 / 应对 / 印记 / 天气 / MP |

合计 **9581 行**。

## 二、没搬什么（以及为什么）

| 文件 | 原因 |
|---|---|
| `mcts.py` | 它的 `get_actions` 直接遍历 `current.skills`，对抗搜索时**双方都能看到对手技能** —— 完美信息，不真实。PIMC 自己写 |
| `pokemon_db.py` | 它的精灵属性数据是错的（实测 4/5 与 roco-cal 不一致，且缺银月狼王） |
| `server.py` / `main.py` / `team_builder.py` | UI / 入口，我们用不上 |
| `data/nrc.db` | 只借用它的 `skill` 表（技能名/威力/耗能/描述），`pokemon` 表不用 |

## 三、改了哪些地方

### 3.1 import 前缀（71 处，机械替换）

```
src.models        -> sim.models
src.effect_models -> sim.effect_models
...
```

只替换真正的 `from src.` / `import src.` 语句，注释里的 `src.` 字样不动。

### 3.2 `skill_db.py` 的数据库路径（1 处）

原路径：`<src>/data/nrc.db`（指向 `src/data/`，不存在）
改成：`<仓库根>/data/external/nrc-ai/data/nrc.db`

理由：技能表是完整的（491 条全部有效果配置），可以用于
"技能名 -> Skill 对象"的构造。

### 3.3 `adapter.py`（新增，非上游文件）

我们自己的数据适配层，见文件头注释。核心三条规则：

1. 六维用 `my_team` 的**实测值**（不重算）
2. 技能按 **ID** 查（不按名字 —— 我们库有 106 个同名重复）
3. 属性名加「系」后缀（`火` -> `火系`，18/18 命中）

**额外修正**：NRC 的技能数值有错，adapter 会**用我们库的数值覆盖**：

```
月蚀   我们 power=130 cost=5   vs  NRC power=40 cost=2
撞鬼   我们 power=120 cost=4   vs  NRC power=40 cost=2
```

如果不覆盖，这两个技能的威力会被砍到 1/3。

## 四、验证

### 4.1 数据注入正确

```
音速犬     hp=366 atk=221 def=171 spa=97  spd=150 spe=260
冰钻布鲁斯  hp=345 atk=220 def=185 spa=80  spd=157 spe=192
卡瓦重     hp=352 atk=168 def=147 spa=73  spd=140 spe=280
银月狼王   hp=417 atk=234 def=186 spa=116 spd=168 spe=273
恶魔狼     hp=417 atk=218 def=160 spa=104 spd=129 spe=260
```

**与游戏面板 30/30 项完全一致**（5 只 × 6 维）。

### 4.2 引擎能跑完整对局

```
python src\sim\selfplay.py
  T1  音速犬(366) vs 音速犬(366)  火云车 vs 火云车
       -> 音速犬(122) 音速犬(122)
  T2  音速犬(122) vs 音速犬(122)  火云车 vs 火云车
       -> 音速犬(  0) 音速犬(122)
  ...
  结果: 胜方=b  回合=18
```

效果可见：
- 专注力（入场物攻 +100%）让首发一击 244 伤害
- 铭记于月亮（每次攻击后自损 5%）每回合掉 21 血
- 冰雹对轰双方各掉 216

### 4.3 批量自对弈

```
python src\sim\selfplay.py --n 100 --quiet
  A 胜 41 (41.0%)   B 胜 59 (59.0%)
  平均回合 17.5  中位 18
```

A 先手、B 后手，41/59 说明先手优势存在但不极端，合理。

## 五、还没做的

| 项 | 计划 |
|---|---|
| 3 个技能效果补丁（丢冰块/筛管奔流/惊吓盒子） | S4 |
| 2 个缺失技能（月蚀/撞鬼） | S4 |
| 2 个引擎 condition（self_hp_gt_80 / enemy_hp_lt_50） | S4 |
| 悲悯的 scope 改 all + 时序修正 | S4 |
| 伤害后端换 roco-cal（当前用 NRC 的公式） | S5 |
| PIMC（不完美信息） | S6 |

## 六、MIT 许可声明

本目录下的 `models.py` / `effect_models.py` / `effect_engine.py` /
`engine/` / `effect_data.py` / `skill_effects_generated.py` /
`skill_db.py` / `battle.py` 均移植自
[NRC_AI](https://github.com/ColinHong10/NRC_AI)，
遵循其 MIT License：

```
MIT License

Copyright (c) 2026 ColinHong

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

`adapter.py` / `selfplay.py` 是本项目原创。
