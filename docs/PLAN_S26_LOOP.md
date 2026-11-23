# S26 计划：policy_v2 × Battle 全闭环集成测试

> 原则：计划 → 实现 → 测试 → 基线核对 → commit → 文档。
> 动机：S22 的决策分支目前只测过 mock state——需要验证决策输出的
> ("magic", i)/("evolve", i) 能被 Battle.step 真实执行且行为正确。

## 设计

**state 提取器**（测试辅助，也是 robot 接线的参考实现）：
`battle_to_state(bt)` —— 从 Battle 对象提取 policy_v2 需要的字段：
- me: {name, hp, hp_max, energy, types, bloodline,
       skills: [{i, dmg(calc_damage), cost, kind:"num", lit}]}
- foe: {name, hp, hp_max, types}
- magic: bt.team_magic/magic_state（S17 形状）
- evolution: {used, usable}（首领血脉才有）

**闭环循环**：
```
for turn in range(30):
    state = battle_to_state(bt)
    act, why, _ = policy_v2.decide(state)
    kind, payload = act
    ev = bt.step(kind, payload, "energy", None)   # 对方聚能占位
    断言：无异常、事件非空、动作被引擎接受
    bt.begin_turn()
```

## 断言

1. 闪焰爆发队：开局若干回合内出现 ("magic", i) 决策且引擎执行
   （事件含「闪焰爆发」）
2. 首领血脉队：开局出现 ("evolve", i) 且引擎执行（事件含「首领化」）
3. 光合治愈：低血时触发续命
4. 全程无崩溃、事件流合法、动作被接受（不会出现
   「未知动作」类失败）

## 覆盖

- 决策层（S22）× 引擎（S8-S10）× 状态提取——三层接缝
- 这也是 robot 实盘接线时的 state 构造参考实现

## 基线风险

纯测试批次 + battle_to_state 辅助函数（放 tests 内），零引擎改动。
