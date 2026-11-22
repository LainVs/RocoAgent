# S11 计划：nrc 原语扩展第二批（特性层 61%→~72%）

> 原则：计划 → 实现 → 测试 → 基线核对 → commit → 文档。
> 数据源：data/nrc_effects.json（nrc-ai 效果引擎，491/491 技能配置）。

## 现状

nrc HANDLERS 25 个。未生效特性按缺失原语聚类（探测见
Temp/opencode/probe_s11b.py 输出）：

| 原语 | 解锁特性 | params 语义 | 实现难度 |
|---|---|---|---|
| ABILITY_COMPUTE | 6（溶解扩散/不移/快锤/暴食/冻土/飓风） | 开场改写技能（威力% / grant_agility / 计数源） | 中 |
| COUNTER_ACCUMULATE_TRANSFORM | 3（保卫/腾挪/好象坏象） | 应对成功 N 次后转化（变身/形态） | 高（需形态系统） |
| PERMANENT_MOD | 2（身经百练/向心力） | 永久威力修正（按计数/固定 delta） | **低** |
| AUTO_SWITCH_* | 4（警惕/星地善良/防过载保护/奔波鬃） | 能量归零/行动后自动换人 | 中 |
| MARK_POWER_PER_METEOR | 2（坠星/观星） | 每层星陨印记威力+15% | **低** |
| EXTRA_FREEZE_ON_FREEZE | 2（加个雪球/捉迷藏） | 冻结时额外+2层 | **低** |
| ON_SKILL_ELEMENT_COST_REDUCE | 2（浸润/浪潮） | 特定系别技能能耗-N | **低** |
| SKILL_SLOT_LOCK | 2（正位宝剑/宝剑王牌） | 锁定可用技能槽 | 中 |
| BURN_NO_DECAY | 1（煤渣） | 灼烧不衰减 | **低** |
| CONVERT_POISON_TO_MARK | 1（蚀毒） | 中毒 buff → 印记 | **低** |
| SKILL_MOD | 1（乘风连击） | 技能修正 | 中 |
| KILL_MP_PENALTY | 1（付给恶魔的赎价） | 击杀惩罚 | 低 |

## 本批范围（低难度优先，一次批次 8 个原语 / 13 条特性）

1. **PERMANENT_MOD**：`向心力`（威力+30 固定 delta）+ `身经百练`
   （power_pct per_counter——每受击次数+20% 威力，需计数器）
2. **MARK_POWER_PER_METEOR**：坠星/观星——星陨印记层数 ×15% 威力
   （battle_fx.mark_mods 已有印记钩子，加 hook）
3. **EXTRA_FREEZE_ON_FREEZE**：冻结施加时 +2 层（_bump_status hook）
4. **ON_SKILL_ELEMENT_COST_REDUCE**：浸润（水系能耗-1）/浪潮（-2）
   （eff_cost 已有钩子）
5. **BURN_NO_DECAY**：灼烧层数减半时跳过（status_tick hook）
6. **CONVERT_POISON_TO_MARK**：回合末中毒 buff 转印记（ratio 2）
7. **KILL_MP_PENALTY**：击杀者能量 -N（params 待查）
8. **SKILL_MOD**（乘风连击，params 待查）

## 明确不做（后续批次）

- ABILITY_COMPUTE（6 条，需要开场技能改写引擎，单独批次 S12）
- COUNTER_ACCUMULATE_TRANSFORM（3 条，需要形态/转化系统）
- AUTO_SWITCH_*（4 条，需要自动换人决策层）
- SKILL_SLOT_LOCK（2 条，与传动槽位系统联动，单独做）

## 测试（src/test_s11_traits.py）

每原语至少 1 条端到端：载体特性精灵 + 触发场景 → 断言数值。
如：坠星 + 星陨印记 2 层 → 威力 +30%；浸润水系技能能耗 -1。

## 基线风险

这些特性载体不在基线队伍（探测时逐条确认）→ 基线预期零变化；
若变化需逐行解释。
