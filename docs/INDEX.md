# 文档索引（docs/INDEX.md）

> 最后更新：2026-11-23（S27）

## 当前状态（从这里开始）

| 文件 | 内容 |
|---|---|
| [SIM_STATUS.md](SIM_STATUS.md) | **逐批实现记录**（S1-S26，最新在上） |
| [SIM_COVERAGE.md](SIM_COVERAGE.md) | 覆盖率（技能/特性/机制/实战校准） |
| [../README.md](../README.md) | 项目总览 + 架构 + 路线图 |

## 实战校准与对账

| 文件 | 内容 |
|---|---|
| [FOE_CALIBRATION.md](FOE_CALIBRATION.md) | per-foe 承伤系数表 + 机器人用法 |
| [RECONCILE_REPORT.md](RECONCILE_REPORT.md) | 实盘对账：公式验证 + 回放路径 |
| ../data/foe_calibration.json | 出伤系数（21 对手） |
| ../data/foe_incoming_calibration.json | 承伤系数（38 对手） |

## 工具脚本（scripts/）

| 脚本 | 用途 |
|---|---|
| snap_sim_baseline.py | 黄金基线重拍（引擎改动后必跑） |
| reconcile_sim.py | 静态对账（卡牌预览 vs calc_damage） |
| replay_validate.py | 1v1 战斗状态回放验证 |
| foe_calibration.py | S19 出伤校准表生成 |
| foe_incoming_calibration.py | S24 承伤校准表生成 |
| probe_game_state.py | 游戏画面状态采集（S23，对战时运行） |

## 计划档案（按批次）

| 批次 | 计划文件 | 一句话 |
|---|---|---|
| S8 | PLAN_FULL_SIM.md（章节） | 血脉系统完整链路 |
| S9 | —（S8 内） | 共鸣魔法愿力强化 |
| S10 | —（S8 内） | 进化之力/首领化 |
| S11 | PLAN_S11_NRC.md | nrc 原语第二批（7 原语 10 特性） |
| S12 | PLAN_S12_COMPUTE.md | ABILITY_COMPUTE 开场改写 |
| S13 | PLAN_S13_SWITCH.md | 槽位锁/自动脱离/应对变身 |
| S14 | PLAN_S14_LINKS.md | authority link 第 4 层 |
| S15 | PLAN_S15_COUNTER.md | 应对技效果引擎 |
| S16 | PLAN_S16_COND.md | HP 阈值条件引擎 |
| S17 | PLAN_S17_UI.md | 手操 UI 后端接线 |
| S18/S18b | PLAN_S18_RECONCILE.md | 实战对账 + 回放骨架 |
| S19 | PLAN_S19_CALIB.md | 出伤校准表 |
| S20 | —（S19 内） | 校准接进决策 |
| S21 | —（S17 内） | UI 前端渲染 |
| S22 | PLAN_S22_POLICY.md | 决策层 magic/evolve 分支 |
| S26 | PLAN_S26_LOOP.md | 决策×引擎全闭环测试 |

## 运维（既有）

| 文件 | 内容 |
|---|---|
| STARTUP.md | 启动链/崩溃排查/BLE 链路 |
| MULTI_SESSION.md | 多会话协作约定 |
| ARCHITECTURE.md / DAMAGE_MODEL.md / DATA_SOURCES.md | 设计文档 |
| PLAN_FULL_SIM.md | 模拟器总体规划（S1-S8 前半） |
