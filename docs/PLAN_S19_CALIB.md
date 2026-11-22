# S19 计划：实战伤害校准表（per-foe 承伤系数）

> 原则：计划 → 实现 → 校验 → commit → 文档。
> 数据源：logs/turns.jsonl（turns 的 foe_pct 快照序列 + team_db 卡牌预览）。

## 方法

对每个 team_db 回合（我方技能 + 卡牌预览 V）：
1. live_dmg = (foe_pct[t] - foe_pct[t+1]) / 100 × foe_max_hp_sim
   （伤害在下一快照体现——1v1 回放已验证该时序）
2. 系数 k = live_dmg / V（含对方面板/减伤/防御增益的全部效应）
3. 按 foe_pet 聚合：中位数 k + 样本数

过滤：
- foe_pct 上升（治疗/换人）→ 丢弃
- foe_pet 变化（对方换人）→ 丢弃该对
- 卡牌预览 0/缺失 → 丢弃

## 产出

- scripts/foe_calibration.py → data/foe_calibration.json
  （{foe_pet: {"k_median": x, "n": N}}）
- docs/FOE_CALIBRATION.md：表格 + 使用说明
  （机器人预测伤害时：预测 × k(对手) ≈ 实盘）

## 验证

- 校准后残差：|live - V×k| / live 的分布（目标：±20% 内 ≥80%）
- 与 S18 静态对账对比：校准前 MAE 146 → 校准后目标 <30%

## 明确不做

- 反解对方面板的绝对值（max_hp/def 分离不可辨识——只能校准乘积）
- 减伤来源归类（印记/增益/面板——数据不支持）
