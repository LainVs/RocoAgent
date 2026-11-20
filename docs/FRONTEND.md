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
    "fps_ms": 4.2,
    "detectors": {"bar": true, "skillbar": true, "countdown": true},
    "conflicts": 0,
    "gate": null
  }
}
```

字段缺失一律表示**未知**，绝不填 0。前端必须能处理任意字段缺失。

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
python src\test_ui.py            # 前端自检（29 项，含真实 DOM 渲染）
python src\test_ui.py --no-dom   # 同上但跳过渲染（3 秒）
python ui\compose_test.py        # 对比度（面板压在真实游戏画面上）
```

`test_ui.py` 覆盖四组：API 契约 / 静态资源 / live_state 契约 / 面板 DOM。
DOM 测试放**子进程**跑 —— pywebview 的 `start()` 会阻塞 GUI 循环，
在主进程里跑会把整个测试卡死。

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

## 五之四、窗口尺寸标定（必须记住）

pywebview 的 `create_window(width, height)` 参数**不是 CSS 像素**。
200% DPI 下实测（5 组数据）：

```
create(340, 620) -> 窗口 680x1240 物理, inner 327x585 CSS
create(400, 700) -> 窗口 800x1400 物理, inner 387x665 CSS
create(500, 900) -> 窗口 1000x1800 物理, inner 487x865 CSS
create(680,1240) -> 窗口 1360x1950 物理, inner 667x940 CSS
create(706,1310) -> 窗口 1412x1950 物理, inner 693x940 CSS
```

规律：`inner_css = create_param - 13`（宽）/ `- 17`（高）

所以 `css_size(css_w, css_h)` 要**加回**这两个常量：

```python
FRAME_W, FRAME_H = 13, 17
def css_size(w, h):
    return (int(w + FRAME_W), int(h + FRAME_H))
```

另外：**`SetWindowPos` 调尺寸时绝对不能传位置**。之前传了 `(0,0)`
把窗口从初始位置挪到屏幕左上角，外部截图按旧坐标抓，看起来
"面板被切"——查了半天才发现是坐标错位，不是渲染问题。
必须用 `SWP_NOMOVE`。

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
