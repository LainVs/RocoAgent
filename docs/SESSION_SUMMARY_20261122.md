# 会话总结（2026-11-22）—— 机器人/伤害计算器优化日

## 回归认证

18 个测试文件全绿（scripts/regression_sweep.py）：
imports / statmodel / damage_model / battle_fx / policy / policy_switch /
decide / priority / skill_names / skill_kind / policy_cost /
session_moves / cast_route / avatar_ident / skill_pill_boxes /
standalone / perceive / record — **18 passed, 0 failed**。

## 今日改动（30+ 提交，全部推送 lainvq@gmail.com）

### 识别链
- `avatar_ident.py`：pet_avatars 第三身份通道（639 类），高置信档
  （≥0.60 且 margin≥0.03）实测 96% 精确；接入 foe_ident / robot /
  standalone 三处；带安全带（无属性不许全库投票）
- 全量离线 dry-run：108 真实帧零错误；37 个 unknown 全是结算页帧
  （fail-closed 正确）

### 技能解析（回答了"每个队都要建表吗"——不用）
- 发现卡面下方印技能名 → `session_moves.py` 三层降级链
  （session 播报 / band 卡面直读 / team_db 兜底）
- 播报敌我分流（_name_close 编辑距离 1）+ 槽位归因（6s 窗口）
- 换精灵后名字带自动扫描（perceive.skill_pill_boxes 几何已验证）
- my_team 降级为缓存；表过期自动 TEAM STALE 提示

### 决策层
- 自算伤害补上本系/克制（旧版低估 3.75 倍，交叉校验会覆盖正确卡面）
- 承伤改概率加权 max(期望, 0.75×worst)（12 组对位实测 worst 高估 1.5~3x）
- 换人 KO 打六折（池子幻想：bench 池 max 触发 w_ko=+0.30）
- 决策矩阵 5 用例固化（test_policy_switch.py）

### 观测/运维
- audit_incoming.py：median wt/obs=1.16，blend=0.75 保持
- nightly_report.py：四节仪表；首跑暴露最近 20 场胜率 10.5%
- 104 场低动作局根因：16 幻影 + 14 真实短局，非机器人故障
- MATCH START 幻影防线（双方血量 live 才开新对局）
- git 代理修复（socks5h://127.0.0.1:17896）+ 353 积压提交补推

## 已知边界（诚实清单）
- 替补技能不可在线学习（HUD 无信息源）——换人评分池 max 已打折
- 结算页/大厅帧返回 unknown 是 fail-closed 正确行为
- 自对弈有 65/35 先手偏置——权重标定必须走实盘 audit

## 明日行动（全部依赖实盘）
1. 游戏+板子就位 → run_bot.py --supervisor 跑实盘
2. 盯 BAND/CAST/START suppressed/weighted 四类日志验证新链路
3. nightly_report.py 一条命令出仪表；按数据决定 blend 与 my_team
