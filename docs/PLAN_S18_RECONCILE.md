# S18 计划：实盘对账（模拟器精度校验）

> 原则：计划 → 实现 → 测试 → commit → 文档。
> 数据源：logs/turns.jsonl（244 场 / 2268 回合 / 1517 回合带伤害数字）。

## 目标

把模拟器的伤害计算跟实盘对战日志对账，产出精度报告：
`scripts/reconcile_sim.py`

- 输入：turns.jsonl 按场分组（mid），每回合 `why` 字段带
  "X dmg, Y taken"（我方造成/承受伤害）
- 模拟：同配置（our_pet + skill → foe_pet）用 Battle.calc_damage 算
  期望伤害，对比实盘 X
- 产出：MAE / 相对误差分布 / 分宠物误差排名 → 找出系统性偏差来源

## 关键口径（先对齐再写代码）

1. **我方伤害 = X**：our_pet 的第 action[1] 号技能打 foe_pet
   —— 技能名从 skills 卡牌 OCR 值取（turns 里有 skills 数组）
2. **我方面板**：从 my_team（DB）读实配（等级/性格/血脉/个体）
   —— 没有配置的宠物跳过并计数
3. **对方承受**：foe_pet 的防御未知 → 用 hp.jsonl 的 pct 变化反推
   合理性，但精确对比只能对我方造成的伤害（X）
4. **效果区间**：实战伤害有随机浮动吗？（游戏里同技能同目标伤害
   恒定——之前实测结论；若有 ±5% 浮动，MAE 阈值放宽）

## 步骤

1. 解析脚本：turns.jsonl → (mid, turn, our_pet, foe_pet, skill_name,
   dmg_done)
2. 配置解析：my_team DB → Pet 构造（血量/攻击/防御面板）
3. 逐回合 calc_damage（带 dt=2 物理路径、STAB、克制）
4. 报告：总体 MAE、|误差|>10% 的回合列表、按宠物/技能聚合
5. 偏差归因：克制系数？STAB？等级差？逐项隔离

## 产出

- scripts/reconcile_sim.py（可重复运行）
- docs/RECONCILE_REPORT.md（精度报告 + 归因）
- 发现的模拟器 bug 逐个修复（每修一个跑一遍基线防回归）

## 明确不做

- 对方伤害（Y taken）的精确对账——对方面板未知，只做量级 sanity
- 实盘随机浮动的建模——先确认有没有
