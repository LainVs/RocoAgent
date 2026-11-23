# S22 计划：决策层接入新机制（magic/evolve 分支）

> 原则：计划 → 实现 → 测试 → 基线核对 → commit → 文档。
> 范围：policy_v2 决策层（state 可选字段）；robot 实盘接线后续批次
> （需要游戏画面自动化：魔法按钮/进化之力的 UI 探测 + HID 点击）。

## 决策规则（保守优先，宁缺勿滥）

### evolve（进化之力）
- 触发：state["evolution"].usable 且我方有存活技能
- 动作：("evolve", i)——i = 普通路径会选的技能下标
- 理由：首领形态严格更优（种族值↑+新特性），不占行动 → 立即进化
- 🟡 假设：变身当回合无副作用（原文未提）

### magic（共鸣魔法，按 team_magic 名分派）
- 愿力强化：foe_hp 高（>40%）且血脉系别对 foe 的克制 ≥2.0
  （愿力冲击打克制收益最大）→ ("magic", 0)
- 闪焰爆发：我方火系/火血脉 + 选中的攻击技威力 ≥80 →
  ("magic", best_i)（当回合 ×2.5）
- 节流术：我方能量 ≤2 且有 wanting 技能 → ("magic", best_i)
- 光合治愈：我方 hp ≤40% 且草系/草血脉 → ("magic", 0)（续命）
- state["magic"]["name"] 决定走哪条；不在表内 → 忽略

## 实现点

- policy_v2.decide：在标准技能评估**之前**插两个前置分支
  （evolve 最先——严格收益；magic 次之——按上表条件）
- state 可选字段：`state["magic"]`（S17 已有：name/usable/cd/used）、
  `state["evolution"]`（S17 已有：used/usable）
- robot 接线后续：把实盘的魔法/进化状态填进 state（需 UI 探测）

## 测试（test_s22_policy.py）

1. evolve：首领血脉 + usable → 返回 ("evolve", i)
2. evolve 已用 → 不再返回
3. 闪焰爆发：火系 + 强攻击技 → ("magic", i)
4. 光合治愈：草系低血 → ("magic", i)；高血 → 不触发
5. 愿力强化：克制 ≥2 → ("magic", 0)
6. 无 magic/evolution 字段 → 行为完全不变（向后兼容）
7. 基线：policy_v2 不进基线 self-play → 零变化
