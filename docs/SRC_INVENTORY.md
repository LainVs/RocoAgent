# src/ 文件清单（2026-11-20）

> 用途：区分"运行时必需"和"独立工具"，避免误删。
> 判断依据：AST 扫描所有 `import` / `from ... import`（含 scripts/）。

## 一、机器人运行时（被 robot 依赖链引用）

| 文件 | 职责 |
|---|---|
| `robot.py` | 门面 + `Bot` 类 + 入口 |
| `config.py` | 常量 / 日志 / 锁 |
| `winio.py` | 窗口 / 焦点 / 点击 / `Screen` |
| `perceive.py` | `Perc` / 技能栏 / 血条 |
| `ui_probe.py` | 按钮 / VS 页 / 结算页 |
| `policy_legacy.py` | 旧 `decide()` |
| `policy_v2.py` | 期望值决策（当前主用） |
| `statmodel.py` | **唯一**数值模型 |
| `damage.py` | 数据层（roco-claude json） |
| `foe_hp.py` | 对手血量 / 承伤 |
| `pvp_engine.py` | TTD/TKO / 技能池 |
| `foe_panel.py` | 特性面板读取 |
| `ability_read.py` | 特性名 OCR |
| `trait_db.py` | 特性 → 物种 → 属性 |
| `foe_moves.py` | 对手招式推断 |
| `hud.py` | HUD 文字读取 |
| `energy.py` | 能量点读取 |
| `digitmat.py` | 血条数字字形匹配 |
| `matdet.py` | 材质检测器 |
| `portraits.py` / `portrait_db.py` | 头像识别 |
| `rank.py` / `record.py` | 段位 / 战绩 |
| `team_db.py` / `matchup_db.py` | 队伍 / 对位库 |
| `ocr_cn.py` | 中文 OCR |
| `paths.py` | 路径解析 |
| `gamedb.py` | 建库脚本（数据已建好，代码保留） |
| `rocodata.py` | 原始 json 访问 |
| `screen_state.py` | 屏幕状态机 |
| `operator_log.py` | 人工操作记录 |

## 二、独立入口（直接跑，不被 import）

| 文件 | 启动方式 |
|---|---|
| `run_bot.py` | `python src\run_bot.py --supervisor` / `--stop` / `--status` |
| `watchdog.py` | 由 `run_bot --supervisor` 拉起 |
| ~~`roco_gui.py`~~ | **已移除**（2026-11-20，等待重新设计） |
| ~~`roco_ui.py`~~ | **已移除**（同上） |
| `selfcheck.py` | 启动前静态检查，不碰游戏 |

## 三、诊断 / 分析工具（按需手动跑）

| 文件 | 用途 |
|---|---|
| `winrate.py` | 胜率统计（项目唯一的衡量指标） |
| `review_turns.py` | 单局复盘（`_end_match` 会自动调用） |
| `pipeline_check.py` | 一条命令看全链路是否通 |
| `analyze_operator.py` | 人工操作 vs 机器人决策对比 |
| `bench_loop.py` | 一个回合的时间花在哪 |
| `bench_skillbar.py` | 技能栏识别精度（396 帧） |
| `diag.py` | 感知是否饥饿 |
| `coverage.py` | 打击面计算 |
| `lives.py` | 魔力结算规则 |
| `verify_portraits.py` | 头像库体检（找错误标签） |
| `verify_channels.py` | 多通道并行交叉验证 |
| `label_portraits.py` | 自动标注 `_unknown_*.png` |
| `cleanup_portraits.py` | 清理头像库重复 |
| `s5_import.py` | S5 赛季精灵入库 |
| `frames.py` | 高频抓帧 + 差异分桶 |
| `burst.py` | 固定间隔抓 N 帧 |
| `monitor.py` | 每 N 秒截图存档 |
| `probe20.py` | 连抓 20 帧观察 |
| `speedtest.py` | 抓屏耗时分析 |
| `proto_bridge.py` | 协议层状态桥接 |
| `espfast.py` / `esp.py` | BLE 常驻链路 |
| `esp_shim.py` | BLE 兼容层 |
| `usb_link.py` | USB 链路 |
| `capture.py` | 旧抓屏（已被 `winio.Screen` 取代） |
| `perception.py` | 旧感知（已被 `perceive.py` 取代） |
| `runbot.py` | 旧启动器（已被 `run_bot.py` 取代） |
| `cardread.py` | 旧技能卡读取 |
| `caps_test.py` / `tol_test.py` / `tune_border.py` / `cmp_fill.py` / `slots_test.py` | 一次性调参实验，结论已进代码 |
| `memprobe.py` / `memtest.py` | 内存读取可行性验证（未采用） |

## 四、测试（`python src\test_xxx.py`）

| 文件 | 内容 |
|---|---|
| `test_statmodel.py` | 4 只实测血量 + 公式断言 |
| `test_trait_db.py` | 特性 → 属性反查覆盖率 |
| `test_decide.py` | `decide()` 回归（八连治疗那批） |
| `test_flow.py` | 确认首发 / 结算页识别 |
| `test_lit.py` | 卡片可放判定 |
| `test_skillbar.py` | 技能栏识别（含负样本） |
| `test_switch.py` | 换人面板（只有 5 行、倒地宠不可选） |
| `test_policy.py` | `policy_v2` 决策回归 |
| `test_state_bus.py` | 状态总线 |

## 五、明确可删（本轮保留，等确认）

这些是**已被取代的旧实现**，没有引用也没有独立价值：

```
capture.py      旧抓屏      -> winio.Screen
perception.py   旧感知      -> perceive.Perc
runbot.py       旧启动器    -> run_bot.py
esp.py          espfast 的 CLI 包装 -> 保留（有独立入口）
```

> 删除前需确认没有外部计划任务/快捷方式指向它们。
