# S14 计划：authority 链路第 4 层（skill_links→buff_desc 解析）

> 原则：计划 → 实现 → 测试 → 基线核对 → commit → 文档。
> 数据源：data/authority_effects.json（skill_links 1205 / buff_names 546）。

## 现状探测（2026-11-23）

- power=0 未覆盖技能 529 个，其中 **485 有 skill_links**、**191 带
  buff_name/buff_desc**（results[].buff_name/buff_desc）
- buff_desc 是结构化文本（"物攻+10%"、"冻结5%生命…力竭"），可直接
  正则解析
- 高频 buff_name：魔防等级降低 13 / 能耗提升 10 / 物防等级降低 10 /
  防御 9 / 物攻等级提升 8 / 魔攻等级提升 7 / 连击 6 / 眩晕 5 / 萌化 5
- 模式正则直打 desc 只有 17 命中（放弃该路径）；走 link 链是正道

## 实现：desc_effects 第 4 层 `_link_fx(name)`

优先级：desc_fx(手写) > authority_fx(180技正则) > **link_fx(S14 新)**。
解析 results[].buff_name/buff_desc → 现有 dv 键（**零新增消费代码**）：

| buff_desc 模式 | dv 键 | Battle 消费点（已有） |
|---|---|---|
| 物攻+X% / 攻击+X% | buff_atk | add_buff（L1913） |
| 魔攻+X% | buff_atk（max 合并，S10 教训） | 同上 |
| 物防+X% / 魔防+X% / 防御+X% | buff_def | add_buff |
| 冻结X%生命… | status.freeze | _bump_status（L1625） |
| 敌方获得N层中毒 | status.poison | 同上 |
| 灼烧 | status.burn | 同上 |
| 回复N能量 | energy_gain | L1338 |
| 吸取/偷取N能量 | energy_drain | L1704 |

- 等级提升语义：desc 显式给了百分比（物攻+10%），按 desc 数值加
  （用户已证：每层+10% 线性，与 desc 一致）
- 条件型（云行雨施 生命高于50%…）：条件引擎未建模 → skip 计数
- 眩晕/萌化/连击/借用/随机 → skip 计数（系统未建模，如实统计）
- `<span color=...>10%</>` 富文本 → 剥标签正则

## 测试（src/test_s14_links.py）

1. 严霜：link → freeze 状态施加 → 冻结 5% 伤害
2. 中毒：link → poison
3. 伺机爆发：物攻+10% → atk_buff 0.1
4. 魔防等级降低类：敌方 debuff
5. 覆盖数统计：link_fx 新覆盖 ≥100 技
6. 基线：预期零变化（新增覆盖的技能不在基线队配置里）

## 明确不做

- 条件型效果（生命低于50%时…）——需要条件引擎
- 连击/眩晕/萌化/随机类——对应系统未建模
- 109 个应对！技能的效果部分——需 counter 时点消费，单独批次
