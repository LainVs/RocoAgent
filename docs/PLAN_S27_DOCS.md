# S27 计划：state 提取器模块化 + docs 索引

> 原则：计划 → 实现 → 测试 → 基线核对 → commit → 文档。

## 1. battle_to_state 模块化

- S26 的 `battle_to_state(bt)` 现在活在测试文件里——robot 实盘接线
  要用它，提升为 `src/policy_state.py` 正式模块
- `battle_to_state(bt)` 签名不变；test_s26_loop 改为 import
- robot 接线时（P4.5）直接 `from policy_state import battle_to_state`

## 2. docs/INDEX.md 导航

docs/ 已积累 20+ 文件（PLAN_S8-S26/SIM_STATUS/SIM_COVERAGE/
RECONCILE_REPORT/FOE_CALIBRATION/…）——加索引页：
- 当前状态入口：SIM_STATUS（最新在上）
- 覆盖率：SIM_COVERAGE
- 实战校准：FOE_CALIBRATION / RECONCILE_REPORT
- 计划档案：PLAN_FULL_SIM / PLAN_S8-S26（一句话摘要）
- 运维：STARTUP / MULTI_SESSION

## 测试

- test_s26_loop 改 import 后全过（行为不变）
- 基线零变化（纯重构）
