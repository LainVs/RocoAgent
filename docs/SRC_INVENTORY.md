# src/ 文件清单（2026-11-20 清理后）

> 用途：区分"运行时必需"和"独立工具"，避免误删。
> 判断依据：AST 扫描所有 `import` / `from ... import`（含 scripts/）。
> 当前 64 个文件：运行时 35 / 入口工具 20 / 测试 9。

## 一、机器人运行时（被依赖链引用）

### 核心循环
| 文件 | 职责 |
|---|---|
| `robot.py` | 门面 + `Bot` 类 + 入口 |
| `config.py` | 常量 / 日志 / 锁 |
| `winio.py` | 窗口 / 焦点 / 点击 / `Screen` |
| `perceive.py` | `Perc` / 技能栏 / 血条 |
| `ui_probe.py` | 按钮 / VS 页 / 结算页 / 换人面板 |
| `policy_legacy.py` | 旧 `decide()` |
| `policy_v2.py` | 期望值决策（当前主用） |
| `screen_state.py` | 屏幕状态机 |
| `state_bus.py` | 状态总线 |

### 数值
| 文件 | 职责 |
|---|---|
| `statmodel.py` | **唯一**数值模型（面板 / 伤害 / 克制 / 区间） |
| `pvp_meta.py` | **PvP 大数据**：性格 / 血量 / 物防 / 技能携带的概率分布（见 `docs/PVP_META.md`） |
| `damage.py` | 数据层（roco-claude json） |
| `foe_hp.py` | 对手血量 / 承伤 |
| `pvp_engine.py` | TTD/TKO / 技能池 |

### 识别
| 文件 | 职责 |
|---|---|
| `digitmat.py` | 血条数字字形匹配 |
| `matdet.py` | 材质检测器 |
| `ocr_cn.py` | 中文 OCR |
| `hud.py` | HUD 文字读取 |
| `energy.py` | 能量点读取 |
| `portraits.py` / `portrait_db.py` | 头像识别 |
| `foe_panel.py` | 特性面板读取 |
| `ability_read.py` | 特性名 OCR |
| `trait_db.py` | 特性 → 物种 → 属性 |
| `foe_moves.py` | 对手招式推断 |
| `avatar_ident.py` | `pet_avatars` 头像通道（639 类，高置信才投票） |

### 数据 / 记录
| 文件 | 职责 |
|---|---|
| `gamedb.py` | 建库（数据已建好，代码保留） |
| `rocodata.py` | 原始 json 访问 |
| `team_db.py` | 队伍（**含命令行编辑**，见下） |
| `matchup_db.py` | 对位库 |
| `rank.py` / `record.py` | 段位 / 战绩 |
| `operator_log.py` | 人工操作记录 |

### 链路
| 文件 | 职责 |
|---|---|
| `esp_shim.py` | BLE 兼容层 |
| `usb_link.py` | USB 链路 |
| `providers.py` | 数据源抽象 |
| `paths.py` | 路径解析 |

## 二、独立入口 / 工具（直接跑，不被 import）

| 文件 | 启动方式 |
|---|---|
| `run_bot.py` | `python src\run_bot.py --supervisor` / `--stop` / `--status` |
| `watchdog.py` | 由 `run_bot --supervisor` 拉起 |
| `selfcheck.py` | 启动前静态检查，不碰游戏 |
| `winrate.py` | 胜率统计（项目唯一衡量指标） |
| `review_turns.py` | 单局复盘（`_end_match` 会自动调用） |
| `pipeline_check.py` | 一条命令看全链路是否通 |
| `analyze_operator.py` | 人工操作 vs 机器人决策对比 |
| `bench_loop.py` | 一个回合的时间花在哪 |
| `bench_skillbar.py` | 技能栏识别精度（396 帧） |
| `verify_portraits.py` | 头像库体检（找错误标签） |
| `verify_channels.py` | 多通道并行交叉验证 |
| `audit_incoming.py` | 承伤估计器偏差审计（weighted/observed 比值，每晚跑一次） |

| `bench_avatar_ident.py` | 头像通道精度评测（162 张已标注真值） |
| `label_portraits.py` | 自动标注 `_unknown_*.png` |
| `cleanup_portraits.py` | 清理头像库重复 |
| `s5_import.py` | S5 赛季精灵入库 |
| `frames.py` | 高频抓帧 + 差异分桶 |
| `monitor.py` | 每 N 秒截图存档 |
| `proto_bridge.py` | 协议层状态桥接 |
| `espfast.py` / `esp.py` | BLE 常驻链路 |
| `cap.py` | 单帧抓取 |

## 三、测试（`python src\test_xxx.py`）

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
| `test_pvp_meta.py` | 概率分布合法（和为 1 / 分位有序 / 未知物种返回空） |
| `test_priority.py` | 先发制人：能反杀才用 / 打不死不硬用 / 不压过高伤害 |
| `test_state_bus.py` | 状态总线 |
| `test_avatar_ident.py` | 头像通道：银行 / 高置信不出错 / 候选池 / 融合链 |
| `test_policy_switch.py` | 换人/攻击决策矩阵（KO 幻想 + 安全换人语义锁） |
| `test_session_moves.py` | 我方技能动态解析：确认/撤销/落盘/stale/ROI |
| `test_cast_route.py` | 播报敌我分流：名字编辑距离 + 槽位归因 |

## 四、队伍编辑（原管理面板的功能）

管理面板已移除，改用命令行：

```powershell
python src\team_db.py                    # 用法 + 当前状态
python src\team_db.py --dump             # 导出队伍 JSON
python src\team_db.py --species-list     # 前 40 个可选精灵
python src\team_db.py --set 1 --species 音速犬 --nature 固执 --moves 火云车,力量增强
python src\team_db.py --set 1 --level 60 --max-hp 366
```

## 五、2026-11-20 清理记录

### 删除的旧实现（已被取代）
| 文件 | 取代者 |
|---|---|
| `capture.py` | `winio.Screen` |
| `perception.py` | `perceive.Perc` |
| `runbot.py` | `run_bot.py` |
| `cardread.py` | `digitmat` / `digitread` |

### 删除的一次性实验（结论已进代码）
```
burst, caps_test, cmp_fill, coverage, diag, lives,
memprobe, memtest, probe20, slots_test, speedtest,
tol_test, tune_border
```

### 删除的 UI
```
src/roco_gui.py   988 行 tkinter
src/roco_ui.py    655 行 FastAPI
ui/               49KB 静态资源
```
详见 `docs/UI_TODO.md`（含新 UI 必须覆盖的功能清单）。
