# S17 计划：手操 UI 接入 S8-S16 新机制

> 原则：计划 → 实现 → 测试 → 基线核对 → commit → 文档。
> 范围：manual_battle.py（引擎已支持，UI 层接线）。

## 缺口清单

1. **evolve 动作缺失**：Battle.step("evolve") S10 已支持，手操
   act() 没有 kind="evolve" 分支 → 玩家无法首领化
2. **state 展示不全**：
   - me/foe 缺 bloodline（血脉）、_cond_mods（活跃条件规则）、
     _allowed_slots（槽位锁）、_yuanli_override（裁决愿力替换）
   - evolution 状态（每场 1 次的进度 + 可用目标形态名）
3. **magic.usable** 未包含血脉校验（首领血脉不能愿力强化）

## 实现

- `act({"kind":"evolve","i":N})`：校验次数/血脉 → payload=N
  → step("evolve")；actions 记录
- `state()`：
  - `"evolution": {"used": bool, "usable": bool, "target": 形态名|None}`
  - `_pet_json` 加 `"bloodline"`, `"cond_mods"`, `"allowed_slots"`
- `magic.usable` 排除首领血脉

## 测试（test_s17_ui.py）

1. Session 带 fire 血脉火神 → state().evolution.usable=True
2. evolve act → state 里形态名变烈火战神、evolution.used=True
3. 正位宝剑载体 → state().me.allowed_slots=[0]
4. 愿力强化对首领血脉 → magic.usable=False（或 act 报错）
5. 基线：UI 层零引擎改动 → 零变化
