# 前端总体设计（2026-11-21）

> 本文是前端的唯一架构依据。改动前先读这里。
> 状态：进行中

---

## 一、定位

前端不是"伤害计算器的界面"，而是**整套系统的观测与控制面板**。伤害计算器只是其中一块。

三层信息，按"战斗中是否需要看"排序：

| 层 | 页面 | 什么时候用 | 是否常驻 |
|---|---|---|---|
| **实时** | 悬浮窗 `index.html` | 打架时盖在游戏上 | 常驻，可折叠 |
| **控制** | 控制台 `console.html` | 开打前/收工后 | 按需打开 |
| **分析** | 数据页 `data.html` | 复盘、调队伍 | 按需打开 |
| **数据** | 控制台「头像标注」 | 标 1122 张待标注头像 | 按需打开 |

---

## 二、架构

```
┌──────────────── 浏览器（WebView2 / 任何浏览器）───────────────┐
│  index.html    悬浮窗    ──┐                                  │
│  console.html  控制台    ──┼─→ fetch/SSE → 127.0.0.1:8770     │
│  data.html     数据页    ──┘                                  │
└───────────────────────────────────────────────────────────────┘
                              │
┌───────────────── src/ui_server.py ────────────────────────────┐
│  只读文件，不写业务数据（避免和机器人抢写）                    │
│  · GET /api/state        实时状态（读 logs/live_state.json）   │
│  · GET /api/events       SSE 推送（状态变化才推）              │
│  · GET /api/bot          机器人运行状态                        │
│  · GET /api/log?offset=  增量日志                              │
│  · GET /api/stats        战绩统计                              │
│  · GET /api/turns        回合明细                              │
│  · GET /api/dex          精灵图鉴                              │
│  · POST /api/bot/start   启动机器人                            │
│  · POST /api/bot/stop    停止机器人                            │
└───────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │  logs/*.jsonl     │
                    │  logs/live_state  │  ← 机器人每帧覆盖写
                    │  data/roco.sqlite3│
                    └───────────────────┘
```

**为什么用 stdlib 的 `ThreadingHTTPServer` 而不是 FastAPI**：
零依赖、单文件、启动 <100ms。这玩意儿要跟着机器人一起常驻，多一个 uvicorn 进程就是多一个可能挂掉的东西。

**为什么悬浮窗不自己截图**：
机器人已经在抓屏了。两个进程同时 mss 抓屏会抢 GPU。所以悬浮窗**只读机器人推送的状态**；机器人没跑时才自己抓（降级模式）。

---

## 三、数据契约

机器人每帧写 `logs/live_state.json`（覆盖写，原子替换）：

```json
{
  "t": 1732200000.0,
  "seq": 12345,
  "mode": "battle",
  "turn": 12,
  "clock": 8,

  "me": {
    "name": "寂灭骨龙", "types": ["龙"],
    "hp_pct": 78.0, "hp_abs": 382, "hp_max": 490,
    "energy": 6, "energy_max": 10,
    "buffs": [{"name": "物攻", "layers": 1, "pct": 100}],
    "trait": "龙之威压"
  },

  "foe": {
    "name": "水灵", "types": ["水"],
    "hp_pct": 45.0,
    "hp_range": [349, 419],
    "def_range": [178, 231],
    "nature": "固执", "nature_conf": 0.62,
    "known_skills": ["水枪", "泡沫"]
  },

  "skills": [
    {"i": 1, "name": "隼鳞", "element": "龙", "cost": 3,
     "dmg": 245, "pct": 58.0, "usable": true},
    {"i": 2, "name": "龙之怒", "element": "龙", "cost": 2,
     "dmg": 186, "pct": 44.0, "usable": true},
    {"i": 3, "name": "碎岩", "element": "地", "cost": 1,
     "dmg": 92, "pct": 22.0, "usable": true},
    {"i": 4, "name": "龙舞", "element": "龙", "cost": 3,
     "dmg": null, "pct": null, "usable": false, "note": "强化"}
  ],

  "matchup": {"out": 3.0, "in": 0.5, "tag": "双重克制"},
  "verdict": {"kill_turns": 2, "die_turns": 4, "order": "first"},

  "system": {
    "fps_ms": 85.3,
    "budget_ms": 120,
    "detectors": {"bar": true, "skillbar": true, "countdown": true},
    "conflicts": 0,
    "gate": null
  }
}
```

字段缺失一律表示**未知**，绝不填 0。前端必须能处理任意字段缺失。

`fps_ms` 是最近一次 `Perc.observe()` 的耗时；`budget_ms` 是主循环每轮
`sleep` 的下限（`config.POLL_S`）。**它不是上限而是下限** ——
一轮的真实周期 ≈ `fps_ms + budget_ms`，所以控制台用它算等效轮询频率
（`1000 / (fps_ms + budget_ms)`），健康度按 `fps_ms` 与 `budget_ms`
的倍数关系判，不用魔法数字。

实测参考（2026-11-21，2560x1600）：

| 状态 | fps_ms | 周期 | 等效频率 |
|---|---|---|---|
| 改前战斗态 | 387ms | 507ms | 2.0 Hz |
| 改后战斗态 | ~85ms | 205ms | 4.9 Hz |

### 3.1 `/api/stats` 的口径（重要）

战绩有两个数据源，**它们的可信度差一个量级**：

| 源 | 怎么来 | 可信度 |
|---|---|---|
| `logs/record_state.json` | 机器人点开游戏内「历史战绩」面板，OCR 读「胜利 N 失败 M」 | **高**：两个单调累加的计数，不靠推断 |
| `logs/results.jsonl` | 段位卡星级差（`before/after/of`） | **低**：星级会 +1/+2、升段清零，实测还会卡死 |

实测（2026-11-21）：`results.jsonl` 111 条里 **102 条 flat**，其中 **100 条**
的 `before/after` 都是 `2/2`、`of` 都是 3 —— 不是平局，是**段位卡读数卡死在
同一个值**。拿它算胜率是 66.7%，而同一时刻游戏自己记的是
**126 胜 / 155 负 = 44.8%**。

所以：

```jsonc
{
  "wins": 6, "losses": 3, "flats": 102,      // results.jsonl 的已判定项（降级为参考）
  "decided": 9, "rate": 66.7,                 // 同上
  "last20": [-1, 1, 1, -1, 1, -1, 1, 1, 1],   // 走势（卡死时无意义）
  "rank": {"now": 2, "of": 3},

  "season": {                                 // ★ 主口径
    "wins": 126, "losses": 155, "decided": 281,
    "rate": 44.8, "at": "2026-11-21 09:21:25"
  },
  "badge_stuck": true,                        // 段位卡读数卡死
  "reliable": false,                          // 卡死时一律 false
  "primary": "season",                        // 前端该拿哪个当主指标

  "progress": {                               // ★ 增量口径（2026-11-21 新增）
    "today":    {"wins": 3, "losses": 5, "decided": 8, "rate": 37.5},
    "recent20": {"wins": 9, "losses": 11, "decided": 20, "rate": 45.0},
    "since":    {"at": "2026-11-21 18:00:03", "wins": 124, "losses": 152},
    "count": 31
  }
}
```

前端规则：

- `season` 存在就以它为主（游戏内累计战绩）
- `badge_stuck` 为真时，`results.jsonl` 推出来的胜率与走势**整块标注为不可用**
- `season` 缺失（机器人没进过大厅）才退回 `results.jsonl`，并按 `reliable` 提示

`progress` 由 `logs/record_snapshots.jsonl` 算出来：`record.save_state()` 每判完
一场追加一行 `{ts,wins,losses,dw,dl}`，`decided` 只数 `dw/dl` 非零的行
（建立基线的那次读数是 0/0，不算比赛）。**没有快照时整个 `progress` 是
`null`**，前端显示 `—`，不能显示 0。

为什么必须有它：`season` 是累计值，44.8% 一天动不了 0.3 个百分点 ——
拿它判断"这次改动有没有用"等于没有反馈。

### 3.2 `/api/bot` 的 phase（2026-11-21 新增）

以前只有 `stopped / stale / live` 三态。机器人连不上板子时会在
`BLE waiting for board …` 里循环最多 30 分钟，`bot_stdout.log` 一直在更新，
于是 `phase=live`、界面显示绿色"运行中" —— 2026-11-21 16:06 的真实故障。

现在 `phase` 由**日志尾**推断（不碰硬件）：

| phase | 触发（日志最后一条能定性的行） | 界面 |
|---|---|---|
| `waiting_board` | `BLE waiting for board` | 琥珀 + 等待板子 12s |
| `ble_failed` | `BLE FAILED` | 红 + 板子连接失败 |
| `focus_lost` | `FOCUS LOST` | 琥珀 + 焦点丢失 |
| `board_offline` | `(board offline)` | 红 |
| `live` | 心跳 `state battle=` 且 `state_age <= 3s` | 绿 |
| `stale` | 有心跳但 `state_age > 3s` | 琥珀 |
| `stopped` | 没有 robot 进程 | 灰 |

附带两个字段：

```jsonc
"activity": {                    // 最后一条能定性的日志
  "event": "waiting_board", "text": "20:09:18 BLE waiting for board …",
  "age": 12.4,                   // 行首 HH:MM:SS 算出来的秒数，跨天自动回退
  "counts": {"waiting_board": 1}
},
"board": {                       // 输入链路（espfast.log / ble.log）
  "state": "offline",            // connected / offline / idle / null
  "text": "16:06:42 link down, reconnecting (#1)",
  "age": 3323.0,
  "transport": "usb cdc on COM8"
}
```

`board.state == "connected"` 也可能是**很久以前**的日志（espfast 停了不写新行），
所以前端在 `age > 600s` 时降级成「最后已连 X 前」（琥珀），不按绿色处理。

### 3.2b `game`：窗口分辨率是否匹配标定基准（2026-11-21 新增）

```jsonc
"game": {
  "found": true, "hwnd": 262818,
  "w": 2048, "h": 1536,         // ★ 客户区（Screen.scale() 的口径）
  "win": [2074, 1607],          // 含边框的窗口矩形，仅供排查
  "aspect": 1.333,              // w / h
  "base": [2560, 1600],         // winio.BASE_CLIENT_W/H
  "scale_ok": false,            // 长宽比偏离基准 > 4%
  "iconic": false,
  "note": "客户区 2048x1536（比例 1.33）与标定基准 2560x1600（比例 1.60）不一致，固定 ROI 会变形"
}
```

**w/h 必须是客户区，不是窗口矩形**（2026-11-21 修正）：

```
GetWindowRect -> 2074x1607   （含边框 26x71）
GetClientRect -> 2048x1536   （Screen.scale() 真正吃的）
```

第一版报的是窗口矩形，控制台显示 `2074×1607`，而主会话按客户区说
`2048×1536` —— 同一个窗口两个数字。更糟的是**用户按 2074×1607
去游戏里找不到这个分辨率**（游戏只提供 2048×1536 这类标准值），
告警等于没用。

**为什么这条必须有**：`winio.Screen.scale()` 是

```python
return (cw / 2560.0, ch / 1600.0)     # x、y 分别算
```

客户区长宽比和基准不一致时，x 和 y 的缩放系数就不同（实测 0.800 vs
0.960，差 20%），所有写死的 ROI 被非等比拉伸 —— 血条压扁、技能栏错位。
表现是"今天怎么老读错"，而界面上没有任何东西指向分辨率。

**只读实现**：`ui_server.game_status()` 自己 `EnumWindows` +
`GetWindowRect` + `GetClientRect`，**不**构造 `robot.Screen()`（那会开
mss、触发 DPI 探测、刷新缓存）。判据和 `winio.find_game()` 一致
（标题含「洛克王国」且类名含 Unreal）。

> ⚠️ **必须先声明 DPI 感知**（`ui_server._ensure_dpi_aware()`）。
> `ui_server` 不 import `winio`，而 `winio` 的 DPI 声明在模块级 ——
> 不自己声明就会读到**逻辑坐标**（200% 缩放下是物理值的一半，
> 实测 1037×804 vs 真实 2048×1536）。报错数字会让用户照着改错。

前端规则：

- `found=false` → 顶栏红标「无游戏窗口」，健康格「未找到」
- `scale_ok=false` → 顶栏琥珀 `2048×1536`，健康格「分辨率」并附基准
- 等比但 `iconic=true` → 「已最小化」
- 都正常 → 顶栏不显示，健康格只写尺寸


### 3.3 `/api/decision`（2026-11-21 新增）

`live_state.json` 只有"我看到什么"，没有"我要做什么"。决策在
`turns.jsonl` 里（`action` + `why`），服务端直接捞最后一条：

```jsonc
{
  "action": ["skill", 1],          // skill / energy / switch + 槽位
  "why": "best affordable dmg 107 (red)",
  "turn": 3, "ts": "16:06:20",
  "age": 16835.7,                  // 由 ts 算出来，前端判断是否过期
  "mid": "20261121-160432",
  "my_pct": 45.9, "foe_pct": 100.0, "energy": 0
}
```

**为什么是只读日志，而不是给 `live_state` 加字段**：`_publish_state()` 在
机器人主循环里，改它要动实时回路；而决策本来就已经落盘，读日志零风险。
`action` 为空的行（轮询行）会被跳过。

前端规则：`age > 180s` 时整块降透明度并标「5h前」—— 决策是历史事件，
不能因为机器人停了就把它当成"现在要做的事"。

### 3.4 `bot_stdout.log` 的两类写入者

| 写入者 | 前缀 | 频率 |
|---|---|---|
| `robot.py`（`config.log`） | `HH:MM:SS ...` | 心跳 15s 一行，出招时密集 |
| `standalone.py`（独立模式） | `HH:MM:SS [standalone] ...` | UI 服务常驻时每秒 2~4 行 |

`/api/bot` 的 phase 推断**必须过滤掉 `[standalone]`**，否则窗口里全是感知
日志，机器人事件被挤出去，phase 静默退回 live/stale（2026-11-21 实测：
48KB 尾部 0 条机器人事件）。所以 `ui_server._robot_lines()` 按前缀过滤，
窗口 256KB。日志页（`/api/log`）不过滤 —— 那边要看到全部原始输出。

---

## 四、页面设计

### 4.1 悬浮窗 `index.html`

已实现，继续精修。要点：

```
· 320px 宽，可拖动、可折叠、透明度 20~100%
· 强调色由克制关系推导（克制=暖红/中性=青白/被压=冷蓝）
· 所有文字带 1px 描边 —— 实测这是低透明度下可读的唯一保证
· 图形元素（血条底槽/边框）有不透明度下限补偿
· 唯一高对比元素：技能伤害条
```

### 4.2 控制台 `console.html`

这是今晚新增的主体。信息架构：

```
┌─ 顶栏 ────────────────────────────────────────────┐
│ RocoAgent    ● 运行中  PID 12345   ▸ 停止          │
├─ 左栏 220px ─┬─ 主区 ─────────────────────────────┤
│              │                                    │
│ 概览         │   [当前状态卡片]                    │
│ 实时日志     │   回合 / 血量 / 动作 / 决策理由      │
│ 战绩         │                                    │
│ 队伍         │   [近 20 场走势]                    │
│ 图鉴         │                                    │
│ 设置         │   [回合明细表]                      │
│              │                                    │
└──────────────┴────────────────────────────────────┘
```

关键性能约束（来自旧 UI 的失败教训，见 `docs/UI_TODO.md`）：

| 旧 UI 的问题 | 这次的解法 |
|---|---|
| 每 2 秒无条件刷三个页面 | 只刷当前可见页，切走就停 |
| 头像缩略图重建要 2 秒 | 服务端启动时生成一次，带 mtime 校验 |
| `turns.jsonl` 941KB 每次全读 | 服务端按 mtime 缓存解析结果 |
| 日志全量重读 | 按字节 offset 增量返回 |

顶栏（2026-11-21 新增）：状态点 + 文案 + **输入链路小标**。
非 `live` 状态会写成「等待板子 12s」这种带时长的说法，
板子日志超过 10 分钟没更新时降级成「板子 55min前」，不再按绿色处理。

键盘：`1~7` 切页、`/` 聚焦图鉴搜索、`Esc` 退出输入。
只在没有输入焦点时生效（头像标注页要打精灵名）。

### 4.3 数据页 `data.html`（未实现，功能已并入控制台）

早期设计里计划单独做一个 `data.html`（图鉴 + 队伍编辑 + 复盘），
实际实现时拆进了控制台的「我方队伍 / 精灵图鉴 / 回合明细」三页，
**没有独立文件**。本段保留是为了解释历史文档里的引用。

---

## 五、分阶段

| 阶段 | 内容 | 验收 | 状态 |
|---|---|---|---|
| **F0** | 架构文档 + 版本基线 | 本文 + git tag | ✅ `ui-m0` |
| **F1** | `ui_server.py`：状态/日志/战绩 API | curl 能拿到正确 JSON | ✅ |
| **F2** | 悬浮窗接真实数据（不再 mock） | 机器人跑起来，面板跟着动 | ✅ `ui-m2` |
| **F3** | 控制台：启停 + 实时日志 + 战绩 | 能一条命令开关机器人 | ✅ `ui-m1` |
| **F4** | 控制台：回合明细 + 队伍 + 图鉴 | 完整覆盖旧 UI 功能 | ✅ |
| **F5** | 打磨：性能、空状态、错误处理 | 空闲 CPU ≈ 0，断线有提示 | ✅ `ui-m4` |
| **F6** | 窗口壳 `overlay.py` + 统一入口 `ui.py` | 悬浮窗能独立跑 | ✅ `ui-m3` |

---

## 五之二、怎么跑

```powershell
# 整套前端（服务 + 控制台）
python src\ui.py

# 悬浮窗（盖在游戏上）
python src\ui.py --overlay

# 两个都要
python src\ui.py --overlay --console

# 只提供数据，不开窗口
python src\ui.py --serve-only

# 单独跑服务
python src\ui_server.py --port 8770 --open
```

热键（悬浮窗，全局）：`Ctrl+Alt+O` 显示/隐藏，`Ctrl+Alt+P` 点击穿透。

面板内快捷键（不用全局钩子，鼠标在面板上时有效）：

| 键 | 作用 |
|---|---|
| `Alt+T` | 折叠 / 展开 |
| `Alt+A` | 透明度循环（100 / 78 / 55 / 35 / 20） |
| `Alt+P` | 点击穿透开关 |
| `Alt+H` | 隐藏（`Ctrl+Alt+O` 叫回来） |
| `Esc` | 关设置抽屉 |

验证脚本：

```powershell
python src\test_live_state.py    # 状态契约（6 组，不需要游戏）
python src\test_ui.py            # 前端自检（62 项，含 DOM/韧性/标注/OCR/批量）
python src\test_ui.py --no-dom   # 同上但跳过渲染（3 秒）
python ui\compose_test.py        # 对比度（面板压在真实游戏画面上）
```

`test_ui.py` 覆盖六组：

| 组 | 内容 |
|---|---|
| 1 | ui_server 的 9 个 API 契约 |
| 2 | 6 个静态资源 + 2 个头像（含回退目录） |
| 3 | live_state 契约（7 项：精度 / None 不填 0 / 边界夹取） |
| 4 | 窗口状态持久化（位置 / 尺寸 / 折叠 / 部分更新 / 无残留 .tmp） |
| 5 | 面板 DOM（关键元素 / 无溢出 / 快捷键） |
| 6 | 韧性（数据源断开再恢复） |

两个必须记住的测试工程细节：

**DOM 测试放子进程。** pywebview 的 `start()` 会阻塞 GUI 循环，
在主进程里跑会把整个测试卡死。子进程 + 文件传结果才可靠。

**测试环境必须真隔离。** 韧性测试要起一个独立服务（`ROCO_LOGS` 指向临时目录），
但第一次写的是 `port+10`，撞上了别的进程 —— 连到的是旧服务
（读机器人正在写的日志），于是断言全错、还看不出原因。
现在改成动态找空闲端口 + 写完探针数据后主动验证 `/api/state`
读到的确实是探针，否则明确报"端口被旧服务占用"。

> 这一条比测试本身更重要：**环境不隔离，测试结果就是假的。**

---

## 五之二·五、头像标注（补回旧 UI 最大缺口）

`docs/UI_TODO.md §3.1` 记着：旧 UI 的头像标注入口移除后一直没有替代，
积压 **1122 张待标注**（已标注只有 115 张）。头像是识别对面精灵的
唯一可靠信号，所以这是数据层面最大的缺口。

控制台「头像标注」页解决这个：

```
GET  /api/portraits?side=&limit=   待标注列表 + 同款分组
POST /api/portrait/label           标注单张
POST /api/portrait/label_group     一次标一组同款
POST /api/portrait/skip            跳过（删除）
```

### 标注 = 改文件名，不是写数据库

识别时是按 `portraits/<名字>.png` 找图的，数据库只是索引。
改名才真正生效。重名自动加 `#2 #3`（`portrait_db` 认的变体规则）。
物种名必须在库里，否则拒绝 —— 防止标成乱码永远匹配不上。

### 同款分组：md5 不行，要用感知哈希

同一只精灵往往有 5~10 张截图，一张张标 1122 张会疯。

**先试 md5 -> `dup_count=0`。** 原因：同一只精灵每帧截图的内容都有
细微差异（动画/光照/抗锯齿），字节永远不等。

改用感知哈希（16×4 灰度 + 均值二值化）+ 汉明距离聚类。
实测 60 张名字条的组大小分布：

| 阈值 | 组数 | 最大组 |
|---|---|---|
| 6 | 15 | 4 张 |
| 8 | 15 | 4 张 |
| **10** | **14** | **6 张** ← 和肉眼看到的"贝古斯 6 次"对得上 |
| 12 | 11 | 9 张（开始把不同精灵合并） |
| 16 | 5 | 21 张（明显错） |

取 10。

### 一个前端坑

`img` 加 `loading="lazy"` 会让 60 张图**全部**在视口外，
一张都不加载（`imgsLoaded=0`）。去掉后 120/120 全部加载。

---

## 五之二·六、OCR 自动预填（1122 张手标 -> 确认+回车）

标注页做出来能用，但 1122 张一张张手输不现实。名字条本身就是文字图片。

### 为什么不用项目里的 ocr_cn

`ocr_cn` 依赖 `rapidocr_onnxruntime`，**本机没装**（`ModuleNotFoundError`），
所以它一直返回空数组 —— 这也是 `label_portraits.py` 一直没起作用的原因。

试过 `D:\Tools\winocr.ps1`（Windows 原生 OCR）：能识别，但
PowerShell 5.1 的 stdout 用系统代码页，中文字符过管道会坏，
绕文件写也解决不了。**最终用 easyocr**（已装，准确率 0.91~0.99）。

### 性能实测（CPU）

| 预处理 | 单张耗时 |
|---|---|
| 3× 放大 | 6476 ms |
| 2× 放大 | 1979 ms |
| **1× 原图** | **1287 ms** ← 准确率不变，选这个 |

630 张实际跑了约 10 分钟。

```powershell
python src/ocr_batch.py --status     # 看进度
python src/ocr_batch.py --side both  # 跑全量
python src/ocr_batch.py --reclean    # 只重清洗（不重跑 OCR）
```

### 结果清洗（不做的话 20% 是垃圾）

第一版跑完 127 个未匹配项，大部分是**数字/血量/耗时被误读**：

```
'07417'   '0/366'   '4.2ms'   '55%'   '852/52'
```

名字条截图有时会把旁边的血量数字一起框进来。加 `_clean_name()`
过滤含数字/符号/纯 ASCII 的结果 —— 151 条被清掉。

### 模糊匹配（OCR 错字救回 96%）

OCR 常给错字（"锤头鹳" 实际是 "锤头鲨"），三级匹配：

1. 精确
2. 前缀（"锤头鳄" -> "锤头鳄（...的样子）"）
3. **编辑距离 <= 1**（早停版 DP，只算首字相同的候选）

实测 404 条有效 OCR，**387 条匹配到库里（96%）**。
剩下 14 种确实库里没有（如"小鲸鲸"），保留原文标黄让人判断。

> 预填用的是**匹配到库里的名字**而不是 OCR 原文 ——
> 人工直接回车就对，不用再改一遍。

### 前端三态

| 状态 | 样式 | 含义 |
|---|---|---|
| 匹配到库里 | 绿底 | 直接回车就对 |
| 有 OCR 但库里没有 | 黄底 | 看一眼，可能是错字或库里真没有 |
| 没识别出来 | 空 | 手输 |

---

## 五之二·七、批量确认（630 次回车 -> 几十次）

OCR 预填做完还有 630 次回车。实测 404 条有效 OCR 里 **189 条（30%）**
是「精确匹配 + 置信度 >= 0.9」—— 这些几乎不可能错。

```
POST /api/portrait/auto_confirm
  {side, min_conf, dry_run}
  dry_run=true   返回候选列表（前 200）+ 总数
  dry_run=false  真跑，返回 done/fails
```

### 三条安全约束（宁可不标，也不要标错）

1. **只接受精确匹配**（OCR 文字 == 库里名字），模糊匹配不进批量。
   模糊匹配整体 96% 准，但剩下 4% 标错会污染训练数据。
2. 置信度 >= 0.9。
3. **两段式**：先 `dry_run` 报告会标哪些，用户确认后才真跑
   （改名不可撤销）。

前端弹窗列出前 8 个样例（`stem → 名字 置信度`），确认后执行。

### 实测

```
dry_run 候选判断（造 3 张测试）：
  TEXACT (水灵, 0.98)    在候选 ✓  -> 已改名
  TFUZZY (水灵灵, 0.98)  不在候选 ✓  -> 保留（模糊不进批量）
  TLOW   (音速犬, 0.60)  不在候选 ✓  -> 保留（置信度不够）

真实数据：敌方 61 张 / 我方 128 张 = 189 张可批量
第一次真跑标了 62 张敌方，改名结果核对：
  伊贝粉粉 / 冥古龙 / 加油蟹 / 卡瓦重 / 圣水守护 / 寂灭骨龙 / 卡波斯
  都是库里的真精灵。
```

### 标注页现在的完整能力

| 能力 | 效果 |
|---|---|
| OCR 预填 | 96% 能自动匹配到库里名字 |
| 批量确认 | 30% 一次提交（精确匹配 + 高置信度） |
| 同款分组 | 标一张等于标一批（最大 ×18） |
| 垃圾检测 | 36% 的噪声一键清掉 |
| 三态提示 | 绿=匹配成功 / 黄=库里没有 / 红标=垃圾 |

---

## 五之三、原生桥（window.pywebview.api）

悬浮窗里前端可以调这些原生能力。浏览器里没有这个对象，全部降级到 CSS。

| 方法 | 作用 | 为什么 JS 做不到 |
|---|---|---|
| `drag()` | 让系统接管拖动 | CSS 只能移 DOM，窗口不动，拖出边界就看不见 |
| `set_click_through(b)` | 改 `WS_EX_TRANSPARENT` | 扩展样式位只能 Win32 改 |
| `set_opacity(0~255)` | `SetLayeredWindowAttributes` | 同上 |
| `fit_window()` | 窗口贴合内容高度 | 折叠后必须缩，否则透明区域挡鼠标 |
| `hide()` / `toggle_visible()` | 显隐 | — |
| `quit()` | 退出 | — |

**关键坑：不能在模块加载时取 `window.pywebview.api`。**

pywebview 的 api 是**异步注入**的。写成

```js
const NATIVE = window.pywebview.api;   // 永远是 null
```

的话，脚本执行时它还不存在。必须每次调用时现取（`nativeApi()`），
或者用 `callNative(method, ...args)` 包装。实测改成现取后
`hasNative()` 立刻返回 true。

---

## 五之四、窗口尺寸标定（这块踩了 10 轮，务必读完）

### 三套坐标口径，互相矛盾

这台机器 200% DPI，同一个窗口有三种读数：

| 来源 | 口径 | 例子 |
|---|---|---|
| `create_window(width)` | **CSS 口径** | 传 353 -> 内视口 340 CSS |
| `SetWindowPos(w)` | **物理像素** | 传 340 -> 窗口 340 物理 = 170 CSS |
| `GetWindowRect` | 进程 DPI-aware 时物理，否则逻辑 | 同一窗口能读出两个值 |

**最容易踩的**：以为 `SetWindowPos` 和 `create_window` 一样是 CSS 口径，
于是传 `css_w`，结果窗口被砍成一半（实测 680x1239 -> 400x503），
面板底部被裁，技能区和替补都看不见。

正确做法：

```python
FRAME_W, FRAME_H = 13, 35      # 逻辑像素
def css_size(w, h):            # create_window 用
    return (int(w + FRAME_W), int(h + FRAME_H))

s = dpi_scale()                # 从页面读 devicePixelRatio 最可靠
SetWindowPos(hwnd, 0, x, y, int(css_w * s), int(css_h * s), ...)   # 物理
```

### `dpi_scale()` 里绝对不能调 `SetProcessDpiAwarenessContext`

实测：一旦把进程标成 DPI-aware，随后 `create_window` 的 width/height
会被当成物理像素，窗口只有预期的一半（340x620 -> 170x310），
而且从外面完全看不出原因。

保持系统默认的 DPI 虚拟化，让 `create` 和 `SetWindowPos` 的口径各自稳定。

### `SetWindowPos` 的两个细节

- **`hWndInsertAfter` 传 0（HWND_TOP）而不是 -1（HWND_TOPMOST）**。
  传 -1 且窗口已是 TOPMOST 时，实测**尺寸参数被系统忽略**。
  置顶单独用 `set_topmost()` 做。
- **不要传 `SWP_NOMOVE`**。实测 `SWP_NOMOVE` + 尺寸参数时尺寸不生效。
  直接把当前位置读出来再传回去。

### `win.resize()` 会阻塞

在透明 + 无焦点的窗口上，`win.resize()` 实测会卡死
（日志只到 `start` 就没下文）。不要用它，用 `SetWindowPos`。

### 截图验证必须 DPI-aware

外部脚本如果不声明 DPI 感知，`GetWindowRect` 和 `PrintWindow`
只能看到**一半**的尺寸（680x942 读成 340x471），会误判成"窗口太小"。

```python
UD.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
# 之后 GetWindowRect / PrintWindow 才是真实物理像素
```

### 调试用文件日志

overlay 是脱离父进程跑的（`creationflags=DETACHED`），**stdout 拿不到**。
必须写 `logs/overlay.log`，否则出问题时完全靠猜 ——
这次的 10 轮排查全靠它才定位到根因。

---

## 六、性能账（都是实测）

| 项目 | 数字 | 说明 |
|---|---|---|
| `/api/bot` 响应 | **90ms** | 曾经 10000ms（PowerShell 查进程） |
| 进程枚举 | 554ms / 358 个 | Toolhelp32，只在缓存过期时跑 |
| 读单个进程命令行 | 1~2ms | PEB 直读，比 PowerShell 快 3~4 个数量级 |
| `turns.jsonl` 读取 | 按 mtime 缓存 | 941KB，不再每次全读 |
| 悬浮窗轮询 | 10Hz，无变化跳过 | 空闲 CPU ≈ 0 |
| 面板 20% 透明度 | 对比度 10.65:1 | 加 1px 描边前只有 1.59:1 |

---

## 七、硬约束

```
① 服务端只读日志，不写业务数据
② 前端按需轮询，切走即停
③ 机器人不在时，所有页面显示"未运行"而不是报错
④ 悬浮窗不能抢焦点（focus=False），不能挡游戏点击（穿透锁）
⑤ 任何页面在 1280x800 到 3840x2160 之间都要正常
⑥ 中文路径必须 URL 编码
```

---

## 八、踩坑清单（按现象查）

> 20 条，按"你看到什么"分组。遇到问题先在这里搜关键词。
> 每条都标了实测证据，不是猜的。

### 8.1 窗口透明 / 渲染

| 现象 | 原因 | 解法 |
|---|---|---|
| 首次显示是白底 | pywebview 的透明是延迟生效的 | 等 `panel.offsetWidth>100` 后 `ShowWindow(0)` → 0.55s → `ShowWindow(5)` |
| `SetLayeredWindowAttributes` 返回 0 | pywebview 开的窗口没有 `WS_EX_LAYERED` | 自己 `SetWindowLongW(GWL_EXSTYLE, ex \| 0x80000)` |
| 页面中心是 (240,240,240) | 白屏，页面没画出来就修透明了 | 同第一条：等 offsetWidth 再修 |
| 窗口被别的程序盖住后不刷新 | 少见，但发生过 | 无需处理，WebView2 146 已自动重绘 |
| 圆角外面是白色而不是桌面 | 透明没生效 | 检查 `WS_EX_LAYERED` + 上面的顺序 |
| `backdrop-filter` 看不出效果 | 悬浮窗是独立合成层，采不到背后像素 | 用实心半透明 + 噪点 + 顶部高光 |
| **拉透明度滑块完全没反应，面板一直纯白** | **CSS `rgba` 半透明透出的是 WinForms Form 的浅灰底 #F0F0F0，不是游戏画面** | **改用整窗 alpha，CSS 固定不透明（见 8.1.1）** |
| **`DefaultBackgroundColor = Color.Transparent` 后整个窗口只剩桌面，内容全没了** | 它把 WebView2 的**整个网页画布**挖空，不是"背景透明" | 不要碰这个属性（见 8.1.1） |
| **设 `DefaultBackgroundColor` 报 `InvalidOperationException: CoreWebView2Controller members can only be accessed from the UI thread`** | 从后台线程访问 COM 属性 | 见 8.1.1 第三条 |
| **`webview.start(setup)` 之后窗口不显示、日志停在某一行** | `setup` 占住了 UI 线程，里面 `evaluate_js` 需要消息循环 → **死锁** | `setup` 必须留在 `threading.Thread` 里 |
| **`add_layered` 后窗口仍完全不透明** | 只加了 `WS_EX_LAYERED` 标志，没调 `SetLayeredWindowAttributes` → `GetLayeredWindowAttributes` 返回 `flags=0`，alpha 不参与合成 | 加标志后**立即**设 `LWA_ALPHA, 255` |

#### 8.1.1 为什么"CSS 半透明"这条路走不通（2026-11-21 彻查）

现象：滑块拉到 42%，面板还是纯白。

排查链（全部实测）：

```
1. DOM 完全正确
     panelBg = rgba(250,249,245,0.42)
     alpha   = 0.42
     stage   = none（模拟背景已隐藏）

2. 但像素不对
     窗口外亮度 22，面板区域 241
     rgba(250,249,245,0.42) 压 22 应该是 0.42×250+0.58×22 = 118

3. 读 WinForms 控件树
     Form.BackColor     = A255 R240 G240 B240   (#F0F0F0 浅灰)
     WebView2.BackColor = A255 R240 G240 B240

4. pywebview 的 transparent=True **从不设 Form.BackColor**
     if window.transparent and self.browser:
         self.SetStyle(SupportsTransparentBackColor, True)
         self.browser.DefaultBackgroundColor = Color.Transparent
     else:
         self.BackColor = ColorTranslator.FromHtml(...)   # 只在 else 里

5. 设 WebView2.DefaultBackgroundColor -> InvalidOperationException（UI 线程）
6. 改 setup 到 UI 线程 -> 死锁（evaluate_js 需要消息循环）
7. DefaultBackgroundColor=Transparent -> 整个画布被挖空，内容消失
8. DefaultBackgroundColor=不透明     -> 内容也不显示
9. DWM blur-behind -> API 返回 OK，但窗口不透明时模糊被盖住
```

**结论：WebView2 的透明链路在 pywebview 6.x + 本机环境下不可靠。**

**最终方案：整窗 alpha + CSS 固定不透明**

```
整窗 alpha   -> 面板整体变淡，背后游戏画面透出来（真正的透明）
CSS --alpha  -> 固定 1.0，面板底色够实，文字对比度稳定
```

两层都改会**相乘**：`0.78 × 0.78 = 0.61` → 灰雾（实测踩过）。

实测数据（浅色面板压深色背景，背景亮度 23）：

| 整窗 alpha | 面板亮度 | 文字对比度 | 评价 |
|---|---|---|---|
| 255 | 246 | 7.2 | 优秀（不透明） |
| 230 | 224 | 6.6 | 优秀 |
| 200 | 198 | 5.8 | 优秀（推荐） |
| 180 | 181 | 5.3 | 优秀（下限） |
| 150 | 154 | 4.4 | 浅色背景下文字糊掉 |

**下限取 180**：深色背景 5.3:1 宽裕，浅色背景仍可分辨。
150 那张截图（`E2E_alpha150.png`）里面板几乎消失。

### 8.2 文字可读性

| 现象 | 原因 | 解法 |
|---|---|---|
| 面板压在亮背景上白字看不清 | 对比度只有 1.59:1 | 所有文字加 `text-shadow: 0 1px 2px rgba(0,0,0,.92)`，回到 10.65:1 |
| 20% 透明度时血条消失 | 图形元素没有描边 | 给 `--well-a` / `--line-a` / `--edge-a` 加不透明度下限补偿 |
| 拉到低透明度后整块面板看不见边界 | 边框也跟着透明了 | 边框单独算 `--edge-a = 0.10*boost + (1-a)*0.10` |

### 8.3 布局 / CSS

| 现象 | 原因 | 解法 |
|---|---|---|
| 设置抽屉默认就是展开的 | `grid-template-rows:0fr` 只对**唯一子元素**生效，多行并排时第二行按 auto 撑开 | 包一层 `.drawer-inner` |
| 4 张技能卡只显示 1 张 | `.skills` 写 `flex: 0 1 auto` 在固定高度面板里被压缩 | 面板用 `min-height:100%`，技能区 `flex: 0 0 auto` |
| 面板右侧被切 | 三种可能：元素溢出 / 窗口太窄 / 截图坐标错位 | 先查 DOM：`scrollWidth > clientWidth` 才是真溢出 |
| 数字变化时整行抖动 | 字体不是等宽 | `font-variant-numeric: tabular-nums` + 等宽字体 |

### 8.4 窗口尺寸 / 位置

| 现象 | 原因 | 解法 |
|---|---|---|
| 面板溢出窗口 | `create_window(width)` 不是 CSS 像素 | 实测 `inner_css = param - 13`（宽）/ `- 17`（高），要加回去 |
| 窗口越缩越大 | `panel.scrollHeight` 在 flex 容器里返回容器高度 | 累加所有直接子元素的 `getBoundingClientRect().height` |
| 窗口越调越宽（无限增长） | `fit_window` 读 `window.innerWidth` 算宽度，resize 后又变大 | 宽度固定用启动值，绝不读 innerWidth |
| 调尺寸时窗口跑到屏幕左上角 | `SetWindowPos` 传了位置 (0,0) | 只改尺寸，用 `SWP_NOMOVE` |
| 关掉重开回到默认位置 | pywebview 不记无边框窗口位置 | 自己存 `config/overlay.json` |

### 8.5 拖动 / 交互

| 现象 | 原因 | 解法 |
|---|---|---|
| `WM_NCLBUTTONDOWN` + `HTCAPTION` 完全不动 | 无边框窗口没有 `WS_CAPTION`（实测 SendMessage 返回 0，窗口纹丝不动） | 自己实现：记基准点 + 逐帧移动 |
| **鼠标一离开标题栏窗口就卡住；在外面松手后鼠标一动窗口跟着跑** | **原生拖动分支里直接 `return`，跳过了 `setPointerCapture`** | **无论走哪条路径都先 `setPointerCapture`** |
| **拖动一顿一顿，最后猛地跳过去** | 每个 `pointermove` 都走一次 pywebview JS 桥（120Hz = 每秒 120 次 IPC） | 用 `requestAnimationFrame` 合并，一帧最多发一次 |
| **窗口只走鼠标的一半距离** | `set_drag_pos` 里 `w.move(int(nx/d), ...)` 除以 dpr，但两者**同口径**（实测 SetWindowPos(400)→rect 400） | 位移直接相加，**不做 dpr 换算** |
| 拖动有固定偏移 (-40,-28) | 第一帧位移被吃掉 | `begin_drag` 时就用按下点坐标做基准 |
| 拖动后窗口飞出屏幕 | 坐标口径混用（CSS / 逻辑 / 物理差 2 倍） | 只换算**位移增量**，起点用窗口 rect |
| `SetCursorPos` 后 `GetCursorPos` 不匹配 | 输入模拟被安全软件拦截 | 完全不用光标坐标，靠前端传 `e.screenX/Y` |
| **重启后窗口跑到屏幕外，任务栏有但屏幕上看不见** | `config/overlay.json` 存的位置越界（换显示器/改分辨率/存了坏值） | `clamp_to_virtual_screen()` 自愈，并把修正结果写回配置 |
| **配置改了没生效** | 文件带 BOM（PowerShell 的 `Set-Content -Encoding UTF8` 会写），`json.load` 抛异常被吞掉 | 用 `encoding="utf-8-sig"` 读 |
| **没拖窗口也触发了 drag_end，位置被写成中间值** | 前端 `pointerup` 只要经过页头就调 | 只在 `\|Δ\| > 2px` 时才保存 |

### 8.6 pywebview / JS 桥

| 现象 | 原因 | 解法 |
|---|---|---|
| `const NATIVE = window.pywebview.api` 永远是 null | api 是**异步注入**的 | 每次调用时现取（`nativeApi()`） |
| `evaluate_js` 对 Promise 返回 `{}` | pywebview 不 await | 用 `window.__r` 中转 + 轮询 |
| DOM 测试把整个进程卡死 | `webview.start()` 阻塞 GUI 循环 | 放子进程跑，用文件传结果 |
| **`PrintWindow` 抓到的图和实际显示不一样** | WebView2 窗口上抓到的是**缓存的静态帧**（实测连续三次像素均值完全相同） | 用屏幕 BitBlt；判断内容完整性用 DOM 测量 |
| **`BitBlt` 抓到的不是我的窗口** | 窗口坐标是负数（在屏幕外）或窗口被遮挡 | 先把窗口挪到屏幕中间；或改用 DOM 测量 |
| **从外部改不了页面的 CSS 变量** | 跨进程 | 加 `window.__setAlpha()` + `--alpha N` 启动参数 |
| **`webview.start(setup)` 后窗口不出现** | `setup` 占住 UI 线程，内部 `evaluate_js` 需要消息循环 → 死锁 | `setup` 留在 `threading.Thread` 里 |

#### 8.6.1 排查手段的优先级（这几轮被骗过多次）

判断「渲染对不对」时，按这个顺序选手段：

| 手段 | 可信度 | 适用 |
|---|---|---|
| **DOM 测量**（`clientWidth` vs `scrollWidth`、`getComputedStyle`） | 最准 | 布局、样式、变量值 |
| **屏幕 BitBlt**（`BitBlt(mem, ..., screen, rect.left, rect.top, SRCCOPY)`） | 真实 | 最终视觉效果，但会被遮挡 |
| **`PrintWindow(PW_RENDERFULLCONTENT)`** | **WebView2 上不可信** | 只用于普通 Win32 窗口 |
| **截图肉眼判断** | 受前两条影响 | 只能做最终确认 |

本轮实测教训：`PrintWindow` 连续三次返回完全相同的图（像素均值都是 20.6），
让我以为修复没生效、继续瞎改。而 DOM 测量早就显示 `overflowX: false` 了。

### 8.7 服务端 / 数据

| 现象 | 原因 | 解法 |
|---|---|---|
| `/api/bot` 10 秒超时，整个服务卡死 | `Get-CimInstance Win32_Process` 单次 2~10s 且阻塞 | 读 PEB 拿命令行，1~2ms/进程（快 100 倍） |
| Win11 上 `wmic` 报 FileNotFoundError | 已从系统移除 | 不用它，改 Toolhelp32 + PEB |
| `team` 接口 1 秒 | 每次请求都 `sqlite3.connect()` | thread-local 连接复用 + `PRAGMA query_only=1` |
| `ROCO_LOGS` 环境变量不生效 | 路径硬编码 `ROOT/logs` | 统一走 `paths.py` |
| 120 张头像只加载成功 14 张 | 并发打满 `ThreadingHTTPServer` | 限并发 6，排队加载 |

### 8.8 数据语义（最容易错的地方）

| 现象 | 原因 | 解法 |
|---|---|---|
| 把伤害数字当成技能名 | 机器人只读卡面伤害，不读技能名 | `name=None`，前端显示"卡 N"，不编造 |
| 显示"满血但已死" | `hp_pct=100` 但 `hp_abs=0`（力竭瞬间） | 一致性校验，矛盾时两个都置未知 |
| "2 回合击杀 · — 回合被击杀" | verdict 只有部分字段 | 加 `complete` 标记，不全时显示"数据不足" |
| 能量显示空白被当成 0 | 读不到时返回 None，前端没处理 | 显式显示"能量未知" |
| 机器人没跑时报警告 | 状态推送显示"751s 前（过期）" | 没跑就显示"—"，那不是故障 |

---

## 九、踩坑的方法论（比具体条目更重要）

这 20 条背后有四个反复出现的模式：

**1. 先怀疑坐标，再怀疑代码**
有 5 个 bug 的根因都是"三套坐标系混用"（CSS / 逻辑 / 物理，差 2 倍）。
症状都是"位置不对/不动/飞出去"。遇到这类问题先打印三种坐标对照。

**2. pywebview 的窗口约束会静默忽略 SetWindowPos**
不报错，不返回 0，就是不动。凡是改窗口位置/尺寸，
优先用 `win.move()` / `win.resize()`，`SetWindowPos` 只做兜底。

**3. 异步注入的东西不能在模块加载时取**
`window.pywebview.api`、DOM 就绪、CSS 布局完成 —— 都要轮询等。

**4. 未知必须显示为未知**
这是产品层面最重要的一条。宁可显示"—"，也不要用 0 或默认值冒充。
用户看到"—"会知道"没读到"，看到 0 会以为"真的是 0"。
