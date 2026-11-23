# S24 计划：承伤侧校准（per-foe incoming 系数）

> 原则：计划 → 实现 → 校验 → commit → 文档。
> 对称于 S19（我方伤害→对手的 k 表）；本批校准**对手打我们**的伤害。

## 方法

数据源：logs/opponent_actions.jsonl（699 条：my_pct_before/after、
foe=攻击者、act）。

1. live_incoming = (my_pct_before - my_pct_after)/100 × my_max_hp_sim
   （我方面板已知——my_team 或 species 60 级近似）
2. 过滤：act=attack 且 my_pct 下降；换人/治疗回合丢弃
3. 预测 incoming = E.weighted_incoming(foe, me)（policy_v2._incoming
   同款——bot 现在用的预测）
4. 系数 k_in = live_incoming / 预测
5. 按 foe 聚合中位数 → data/foe_incoming_calibration.json

## 接线（policy_v2._incoming）

`weighted_incoming` 结果 × k_in(foe_name)——与 S20 的 k(我方伤害)
对称。未知对手 1.0。

## 验证

- 校准后残差分布（目标：中位 <10%）
- 决策影响：_incoming 更准 → 必死保护/换人判定更准
- 基线零变化（self-play 不走 _incoming 校准路径——校准表未知对手
  k_in=1.0）

## 明确不做

- 减伤来源分离（同 S19：乘积不可辨识）
- act=buff/switch 回合的伤害归因
