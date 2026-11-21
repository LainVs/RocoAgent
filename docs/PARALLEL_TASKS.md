# 并行任务分工（2026-11-21）

> 目的：多个子代理并行改代码时，避免互相踩。
> **规则：只有主会话（我）能做 git 操作；子代理只改文件。**

---

## 一、铁律

| 规则 | 原因 |
|---|---|
| **子代理不准 `git add` / `git commit`** | 多个 agent 同时操作暂存区会互相污染 |
| **子代理只准改分配给他的文件** | 越界改动会被 git diff 抓到，直接回滚 |
| **`data/roco.sqlite3` 独占** | 二进制文件，无法并行改，必须串行 |
| **每个任务必须自带验证命令** | 不接受"我觉得好了"，只接受"跑这条命令输出 ALL PASS" |
| **主会话复核所有数字** | 实测过子代理会答错（把 12 个 condition 说成 13 个） |

---

## 二、文件所有权表

| 文件 | 归属任务 | 其他人能改吗 |
|---|---|---|
| `src/sim/battle.py` | **S5** | ❌ |
| `src/sim/effect_data.py` | **P2** | ❌ |
| `src/sim/pimc.py`（新建） | **S6** | ❌ |
| `src/sim/belief.py`（新建） | **S6** | ❌ |
| `data/roco.sqlite3` | **P1** | ❌ 独占 |
| `scripts/import_trait_names.py`（新建） | **P1** | ❌ |
| `src/sim/adapter.py` | 主会话 | ❌ |
| `src/sim/skill_db.py` | 主会话 | ❌ |
| `src/sim/engine/_monolith.py` | 主会话 | ❌ |
| `src/robot.py` / `src/policy_v2.py` | 主会话 | ❌ 实盘代码，风险高 |

---

## 三、批次安排

### 第一批：3 个并行

| 任务 | 内容 | 验收命令 |
|---|---|---|
| **S5** | `battle.py` 的 `DamageCalculator.calculate()` 换成 `damage_v2.damage()`（STAB 1.25） | `python scripts\calibrate_damage.py` 中位误差 < 10% |
| **P2** | `effect_data.py` 补 31 个缺失特性效果 | `python src\sim\test_sim_effects.py` ALL PASS |
| **S6** | 新建 `pimc.py` + `belief.py`（PIMC 骨架，先能跑通） | `python src\sim\pimc.py --selftest` |

### 第二批：串行

| 任务 | 内容 | 验收命令 |
|---|---|---|
| **P1** | 从 roco-cal 补 20 只精灵的「？？？」特性名到数据库 | `python scripts\import_trait_names.py --dry` 然后实际执行 |

---

## 四、任务详情

### S5：伤害后端换 roco-cal

**目标文件**：`src/sim/battle.py`（只改 `DamageCalculator.calculate`）

**当前**（NRC 公式）：
```python
base = (atk / dfn) * power * 0.9
damage = base * eff * stab * weather_mult * hits * power_mult_buff
```

**目标**（调我们的 `damage_v2`）：
```python
import damage_v2
return damage_v2.damage(
    atk=int(atk), dfn=int(dfn), power=power,
    type_eff=eff,
    stab=1.25 if same_type else 1.0,   # roco-cal 口径，不是 NRC 的 1.5
    hits=hits,
    trait=power_mult_buff,
)
```

**验收**：
1. 新建 `scripts/calibrate_damage.py`，用 `logs/turns.jsonl` 里
   **731 条**带卡面伤害的记录做校准
2. 中位误差 < 10%
3. `python src\sim\test_sim_effects.py` 仍 ALL PASS

**注意**：
- `damage_v2` 的 `stab` 参数是倍率（1.25），不是加成（0.25）
- 引擎不可用时会自动 fallback 到 statmodel，要确认走的是 roco-cal

---

### P2：补 31 个缺失特性效果

**目标文件**：`src/sim/effect_data.py`（只改 `ABILITY_EFFECTS` 字典）

**数据来源**：
1. `data/roco.sqlite3` 的 `features` 表（name + desc）
2. `data/external/roco-cal/traits/` 的结构化定义（262 个）
3. 如果两处都没有，跳过并记录

**优先级**（按使用精灵数）：
```
完全偏振        12 只   <- 最优先
狂欢开/展翅/淬炼/储备粮/热成像/冷光/换牙   各 3 只
旧玩具/剪影/合拍/护犊/齐鸣/恶龙的奢        各 2 只
其余 18 个                                各 1 只
```

**验收**：
1. `python src\sim\test_sim_effects.py` ALL PASS
2. 新增一个自检：列出补了多少个，跳过多少个及原因

**约束**：
- 只加配置，**不改引擎**（`_monolith.py` 是主会话的）
- 如果某个特性需要新的 `E` 原语或 condition，跳过并在报告里说明

---

### S6：PIMC 骨架

**目标文件**：
- `src/sim/belief.py`（新建）：对手技能信念采样
- `src/sim/pimc.py`（新建）：PIMC 搜索

**belief.py 要做**：
```python
def sample_moveset(foe_species, k=4, rng=None):
    """从信念分布采样对手的 4 个技能。

    信念来源优先级：
      1. ROCO ARENA 样本库（data/external/rocoarena/teams.json）
         同一精灵的真实配法
      2. 我方日志观测（foe_moves 的 Dirichlet 后验）
      3. 该精灵全部可学技能（均匀）
    """
```

**pimc.py 要做**：
```python
def choose(state, foe_species, sims=32):
    """PIMC：采样对手配招 -> 确定性搜索 -> 平均。"""
```

**验收**：
1. `python src\sim\pimc.py --selftest` 能跑通
2. 给出一个"同一局面下 PIMC 与贪心的选择差异"的例子

**约束**：
- 不改 `battle.py`（S5 在改）
- 不改 `models.py`
- 可以 import 现有模块，但不要修改它们

---

### P1：补 20 只精灵的特性名

**目标**：`data/roco.sqlite3` 的 `features` + `feature_pets` 表

**要修的**：58 只精灵当前特性名是「？？？」，其中 20 只能从 roco-cal 补：

```
雪影冰灵   -> 抓到你了     （实盘 36 次）
烈火战神   -> 爆燃         （实盘 27 次）
饮雪狂兽   -> 冰雪魂魄     （实盘 22 次）
圣水守护   -> 浪潮
恶魔狼王   -> 悼亡
千棘海针   -> 溶解腐蚀
女王蜂     -> 虫群突袭
奇丽花     -> 养分内循环
幻影荆棘   -> 大捞一笔
迷嶂布莱克  -> 地脉馈赠
霜翼领主   -> 破空
风暴战犬   -> 全神贯注
黑猫密探   -> 先知
祭礼巨像   -> 坠星
奇梦蝶     -> 三鼓作气
波普尔     -> 超级电池
圣剑骑士   -> 宝剑王牌
伊兰亚龙   -> 游弋
公平鸽     -> 衡量
饮雪狂兽   -> 冰雪魂魄
```

**流程**：
1. 写 `scripts/import_trait_names.py`
2. `--dry` 模式先打印"要改什么"，人工核对
3. 实际执行，**先备份数据库**
4. 验证：查询确认改对了

**约束**：
- **必须串行**（数据库独占）
- 改前 `cp data/roco.sqlite3 data/roco.sqlite3.bak_traitnames`
- 只 UPDATE，不 INSERT/DELETE

---

## 五、子代理的 prompt 模板

每次派活都带上这些：

```
仓库：D:\Projects\RocoAgent
解释器：C:\Users\Lenovo\Desktop\hardwork\CV_ESP_Game_1\.venv\Scripts\python.exe

【硬性约束】
1. 只准修改 <文件列表>。其他文件一律不碰。
2. 不准 git add / git commit / git checkout。
3. 不准改 data/roco.sqlite3（除 P1）。
4. 改完必须跑 <验收命令> 并贴出输出。

【背景】
<该任务相关的实测结论>

【验收标准】
<可执行的命令 + 期望输出>

【报告要求】
- 改了什么（文件 + 行数）
- 验证命令的实际输出
- 遇到的坑
- 没做完的部分及原因
```

---

## 六、主会话的监督清单

每轮子代理报告后，我要做：

- [ ] `git diff --stat` 确认只改了授权文件
- [ ] 自己跑一遍验收命令（不信子代理的输出）
- [ ] 复核关键数字（实测过子代理会答错）
- [ ] 跑全量测试：`test_sim_effects` / `test_imports` / `test_policy_cost`
- [ ] 分任务提交，message 注明哪个 agent 做的

---

## 七、当前基线（不能被破坏）

```
python src\sim\test_sim_effects.py    ALL PASS
python src\test_imports.py            ALL PASS
python src\test_policy_cost.py        ALL PASS
```

自对弈：100 局同队，A 41% / B 59%，平均 13.0 回合。
