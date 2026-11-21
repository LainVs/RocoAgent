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
  "primary": "season"                         // 前端该拿哪个当主指标
}
```

前端规则：

- `season` 存在就以它为主（游戏内累计战绩）
- `badge_stuck` 为真时，`results.jsonl` 推出来的胜率与走势**整块标注为不可用**
- `season` 缺失（机器人没进过大厅）才退回 `results.jsonl`，并按 `reliable` 提示

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

### 4.3 数据页 `data.html`

图鉴浏览 + 队伍编辑 + 战绩复盘。可以晚一点做，优先级低于控制台。

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
| `WM_NCLBUTTONDOWN` + `HTCAPTION` 完全不动 | 无边框窗口没有 `WS_CAPTION` | 自己实现：记基准点 + 逐帧移动 |
| `SetWindowPos` 移动窗口不动 | pywebview 的窗口约束挡住 | 必须用 `win.move()` |
| 拖动有固定偏移 (-40,-28) | 第一帧位移被吃掉 | `begin_drag` 时就用按下点坐标做基准 |
| 拖动后窗口飞出屏幕 | 坐标口径混用（CSS / 逻辑 / 物理差 2 倍） | 只换算**位移增量**，起点用窗口 rect |
| `SetCursorPos` 后 `GetCursorPos` 不匹配 | 输入模拟被安全软件拦截 | 完全不用光标坐标，靠前端传 `e.screenX/Y` |

### 8.6 pywebview / JS 桥

| 现象 | 原因 | 解法 |
|---|---|---|
| `const NATIVE = window.pywebview.api` 永远是 null | api 是**异步注入**的 | 每次调用时现取（`nativeApi()`） |
| `evaluate_js` 对 Promise 返回 `{}` | pywebview 不 await | 用 `window.__r` 中转 + 轮询 |
| DOM 测试把整个进程卡死 | `webview.start()` 阻塞 GUI 循环 | 放子进程跑，用文件传结果 |

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
