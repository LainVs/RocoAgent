# S15 计划：应对技效果引擎（153 技效果子句解析）

> 原则：计划 → 实现 → 测试 → 基线核对 → commit → 文档。
> 数据源：skills.desc 的「应对(攻击|防御|状态)：<效果>」子句（已分类）。

## 效果子句分类（探测结果）

| 我方应对技类别 | 技数 | 效果子句 → ctx 原语 |
|---|---|---|
| 应对攻击 | 50 | 被应对技能能耗+N → enemy_cost_up（已有）；敌方获得物攻-70% → enemy_debuff；敌方脱离 → force_enemy_leave；威力与被应对技能相等 → mirror_power（听桥已特判，泛化）；下回合只能用被应对技能 → 暂缓；冷却+1 → 暂缓 |
| 应对防御 | 29 | 自身不失去能量 → refund_cost；改为速度+160 → 暂缓（速度绝对值口径未证）；改为敌方失去N能量 → enemy_energy_loss；额外使敌方攻击技能能耗+N → enemy_cost_up；额外造成打断 → interrupt（已有）；改为永久+X% → perm_power；改为威力变为N倍 → power_mult（乘法） |
| 应对状态 | 74 | 本次技能威力变为N倍/翻倍 → power_mult；敌方释放技能能耗+N → enemy_cost_up；物攻提高100% → self_buff；提高自身80%魔防 → self_buff；本技能能耗永久-N → perm_cost（暂缓）；额外打断 → interrupt；自身立即使用被应对技能 → 暂缓 |

## 实现方案

1. **counter_fire 扩展**（desc 效果解析不再受 `if not rows` 限制）：
   新增 `_counter_effect_parse(desc)` → ctx 键；无论 skill_rows 是否
   存在都解析（现有 nrc rows 的 ON_COUNTER effects 照旧走 handler）
2. **Battle 消费**（counter 成功块，在 trait_fire 之后）：
   - self_buff → add_buff(owner, ...)
   - enemy_debuff → add_buff(other, 负值)
   - power_mult → attacker._counter_mult = N（本回合，calc_damage 乘法
     通道，与闪焰爆发同款）
   - perm_power → attacker._perm_power_delta += N
   - enemy_energy_loss → other.energy -= N
   - refund_cost → 行动后返还 mv["cost"]
   - force_enemy_leave → 敌方脱离（复用脱离换人）
   - enemy_cost_up / interrupt → 已有
3. **mirror_power 泛化**：威力与被应对技能相等（听桥特判已有）→
   对其他 mirror 技能同样生效（skill 名不在听桥，走 ctx["mirror_power"]）

## 测试（src/test_s15_counter.py）

1. 刺盾（应对攻击：敌方物攻-70%）→ 敌方 atk_buff 下降
2. 吹炎（应对状态：威力翻倍）→ 本次伤害 ×2
3. 应对！放晴（改为永久+100%）→ _perm_power_delta 生效
4. 应对！恶作剧（改为敌方失去6能量）→ 敌方能量 -6
5. 吓退（敌方脱离）→ 敌方换人
6. 应对！摇篮曲（额外打断）→ 敌方技能作废
7. 基线：预期零变化

## 明确不做

- 被应对精灵下回合只能使用被应对技能（临时槽锁，标记后续）
- 速度+160（绝对值口径未证实）
- 防御技能冷却+1 / 本技能能耗永久-N（CD 系统未建模）
- 自身立即使用被应对技能（技能复制）
