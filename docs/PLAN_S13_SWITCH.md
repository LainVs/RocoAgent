# S13 计划：SLOT_LOCK + AUTO_SWITCH + COUNTER_ACCUMULATE（9 特性）

> 原则：计划 → 实现 → 测试 → 基线核对 → commit → 文档。

## 语义（原文已核对）

### 1. SKILL_SLOT_LOCK（2 条）
- 正位宝剑：仅可使用 1 号位技能（allowed_slots [0]）
- 宝剑王牌：仅可使用 1/3 号位（[0, 2]）
- 实现：PASSIVE handler 挂 `pet._allowed_slots`；step 技能选择时
  锁外的槽 → **回落到第一个允许槽**（事件播报，🟡 假设：游戏内
  大概直接不可选，模拟器策略层也按允许槽过滤）

### 2. AUTO_SWITCH（4 条）——复用 S6.10 脱离机制
- 警惕：回合末自己能量=0 → 脱离
- 防过载保护：每次行动后（回合末）脱离
- 奔波鬃：本回合用过防御技能 → 回合末脱离
- 星地善良：回合末己方场上精灵能量=0 → 自己（holder，可能在替补）
  替换该精灵
- 实现点：turn-end 块（status_tick 之后）扫双方在场精灵：
  条件满足 → 复用脱离换人代码（清增益 → bench[0] 入场 → 能量满）
- 🟡 假设：脱离目标 = 第一个存活替补（同 S6.10）；星地善良 holder
  在替补时才生效，场上 holder 能量 0 视同警惕语义不重复触发

### 3. COUNTER_ACCUMULATE_TRANSFORM（3 条）
- 保卫：防御技应对 2 次 → 回满状态 + 变为棋绮后
- 腾挪：攻击技应对 1 次 → 同上
- 好象坏象：状态技应对 1 次 → 同上
- 实现：
  - counter_fire 的 ctx 里带 `category`（skill_category 结果）
  - ON_COUNTER_SUCCESS 触发时把 ctx 传给 trait_fire（现在没传）
  - handler：ctx["category"] == category_filter → 计数 +1；
    达阈值 → `Pet.rebuild_as(棋绮后)` + hp/energy 回满 + 状态清空
  - 变身目标：载体名含（白子）→ 棋绮后（白子）2000000103；
    黑子 → 2000000104；无后缀 → 默认白子（🟡 假设）
  - 复用 S10 rebuild_as（血量比例保留逻辑在此改为回满——回满是
    原文明确语义，rebuild_as 返回后由 handler 覆盖 hp=max）

## 测试（src/test_s13_slots.py）

1. 正位宝剑：选 1 号位外的槽 → 回落 0 号位；allowed=[0] 生效
2. 宝剑王牌：[0,2] → 2 号位可用
3. 警惕：能量清零回合末 → 脱离换人；有替补才触发
4. 防过载保护：每回合末都脱离
5. 奔波鬃：用防御技 → 回合末脱离；用攻击技 → 不脱离
6. 腾挪：攻击应对成功 1 次 → 回满+变棋绮后；能量满/血满
7. 保卫：防御应对 1 次 → 不变身（阈值 2）；第 2 次 → 变身
8. 基线：载体不在基线队 → 预期零变化

## 风险

- 星地善良 holder 在替补的语义：sim 的 Pet 都在 team 列表里，
  turn-end 扫描覆盖；若 holder=场上精灵则跳过（避免自我替换死循环）
- 变身目标棋绮后 4 行同名（3552/3557/2000000103/2000000104）——
  Dex.species 取 total 最高行；按白/黑子后缀显式指定 species_id
  绕过歧义（rebuild_as 走名字，用全名「棋绮后（白子）」）
