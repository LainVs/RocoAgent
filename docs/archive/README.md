# 归档文档

这些是**历史设计稿**，结论已被后来的实测取代或已落地到代码里。
保留是为了追溯"当时为什么这么想"，**不要按这里的内容改代码**。

活跃文档在 `docs/` 根目录，见 `README.md`。

| 文件 | 是什么 | 现状 |
|---|---|---|
| `SWITCH_A_INFO.md` | 换人决策的信息底盘（事实盘点） | 信息已进 `foe_hp.py` / `statmodel.py` |
| `SWITCH_B_ALGO.md` | 换人算法推导（59KB） | 核心判据已进 `_switch_advice` / `_pick_switch_slot` |
| `SWITCH_C_MATRIX.md` | 换人收益矩阵与离线验证（44KB） | 同上；矩阵部分未采用 |
| `SWITCH_DECISION.md` | 上面三份的汇总 | 已被 `TURN_FLOW.md` 取代 |
| `SWITCH_PLAN.md` | 换人改造计划（草稿） | 部分已实施 |
| `PLAN_v1.1.md` | 早期总体方案 | 已被 `ARCHITECTURE.md` 取代 |
| `SYSTEM.md` | 早期系统设计 | 同上 |
| `PROJECT_MANAGEMENT.md` | 项目管理笔记 | 一次性，无持续价值 |
| `ESP_AND_COORDS.md` | ESP32 蓝牙与坐标系 | 内容仍有效，但代码已稳定，不再需要文档 |
| `PVP_NOTES.md` | S5 PvP 对局笔记 | 数据已进库 |

## 为什么归档而不是删除

这些文档里有**踩过的坑的记录**（比如换人面板只有 5 行、倒地宠物仍占一行
且不能选）。这类知识在写新代码时仍可能用到，但不需要出现在活跃文档里
干扰阅读。
