# S12 计划：ABILITY_COMPUTE（开场技能改写引擎，6 特性）

> 原则：计划 → 实现 → 测试 → 基线核对 → commit → 文档。
> 数据源：data/nrc_effects.json params + features 表原文（已逐条核对）。

## 载体与语义（原文已核对）

| 特性 | 原文 | params |
|---|---|---|
| 溶解扩散 | 每携带1个毒系技能，水系技能使敌方+1层中毒 | count_poison_skills |
| 不移 | 无额外效果的攻击技能威力+30% | power_pct 0.3, pure_attack |
| 快锤 | 能耗<3 的技能获得迅捷 | grant_agility, energy_cost_lt 3 |
| 暴食 | 龙系技能获得迅捷 | grant_agility, element [龙] |
| 冻土 | 每携带1个冰系技能，地系技能威力+10% | count_source self_element |
| 飓风 | 其他翼系精灵携带相同技能→迅捷；被击败魔力-1 | shared_wing_skills |

## 实现方案

**开场改写引擎**（Battle.__init__ 末尾，每精灵一次）：
- 解析 ABILITY_COMPUTE params → pet 上挂消费标记：
  - `_skill_power_mods`: {skill_name: pct}（不移/冻土/冰钻类）
  - `_swift_pred`: 谓词（快锤能耗<3 / 暴食龙系 / 飓风共享）
  - `_poison_skill_count`: 溶解扩散计数
- `不移` 的 pure_attack 判定：power>0 且 desc_fx/authority 无
  status/mark/heal/buff 附加（desc_effects 全空）
- `冻土/溶解扩散` 计数：按自己 moveset 数对应系别技能数
- `飓风` 共享扫描：全场（敌我双方）翼系精灵 moveset 里有同名技能
  → 该技能 swift_pred 命中

**消费点（4 个，全部已有钩子）**：
1. calc_damage：`_skill_power_mods[move.name]` → power ×(1+pct)
2. 换人入场迅捷检查（L1765/L1796）：`m in swift_skills` 或
   `_swift_pred(m)`
3. 状态施加循环（L1623 后）：水系技能 + `_poison_skill_count` →
   额外中毒层数
4. 飓风被击败魔力-1：ON_BE_KILLED 时机（TRAIT_TIMINGS 没有）——
   在击杀块与 ON_FAINT 同点补触发；ENEMY_LOSE_ENERGY handler 加
   `target: self_mp` 分支（扣自己魔力）

## 测试（src/test_s12_compute.py）

- 快锤：能耗<3 技能入场迅捷出手；能耗≥3 不迅捷
- 暴食：龙系技能迅捷
- 不移：纯攻击技威力+30%（对照）；带附加效果技不加
- 冻土：1 冰系 → 地系威力+10%；2 冰系 → +20%
- 溶解扩散：2 毒系 → 水系技能施加 2 层中毒
- 飓风：队友翼系同名技能 → 迅捷；被击败魔力-1
- 基线：6 载体不在基线队 → 预期零变化

## 明确不做

- 冰钻/勇敢/挺起胸脯/"国王"的威严/水翼飞升：nrc 数据存在但
  species 表无载体（不入图鉴特性集），数据层等 CUE4Parse
