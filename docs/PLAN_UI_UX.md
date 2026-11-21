# UI/UX 优化计划（2026-11-21）

> 依据：对 `ui/`、`src/ui_server.py`、`docs/FRONTEND.md` 的源码审计 +
> 对比度实测计算 + 2026-11-21 16:06 那次真实故障（板子掉线、UI 看不出）。
> 浏览器渲染在本机被代理拦成 502（`127.0.0.1:8770` 走 Clash），
> 所以这份计划里的"问题"都能在源码里指出行号，不靠观感。

---

## 零、边界（先约定，避免和并行会话撞车）

`docs/MULTI_SESSION.md` 记录 A 会话管机器人、B 会话管前端。本计划**只动控制台侧**：

| 文件 | 本计划是否修改 | 理由 |
|---|---|---|
| `src/ui_server.py` | 是（只加函数） | 新增 `/api/bot` 字段、`/api/stats.progress` |
| `src/record.py` | 是（只追加） | 快照落盘，不改读逻辑 |
| `src/test_ui.py` | 是（追加断言） | 契约测试 |
| `ui/console.js` / `ui/console.css` / `ui/console.html` | 是 | 控制台全部 |
| `src/robot.py` / `ui/app.js` / `src/standalone.py` | **不动** | 并行会话正在改 |

git 约定：
- 只 `git add` 明确列出的文件，禁止 `git add -A`（会带上对方未提交的日志/头像）。
- 每一步一个提交，message 注明 `ui:` 前缀。
- 不 `git checkout` / `git reset` 别人的改动。

---

## 一、清单

### P0 · 状态可见性（最伤信任的一类）

| # | 问题 | 证据 | 改法 |
|---|---|---|---|
| **U1** | 机器人卡在"等板子"时 UI 显示绿灯"运行中" | `ui_server.bot_status()` 只有 `stopped/stale/live` 三态；`robot.run()` 在 `BLE waiting for board …` 里可循环 30 分钟，而 `bot_stdout.log` 一直在更新 | `/api/bot` 增加 `phase`（`waiting_board/ble_failed/focus_lost/board_offline`）+ `activity{event,text,age}` + `board{state,age,transport}`，全部由日志尾推断，不碰硬件 |
| **U2** | 顶栏只显示"运行中"，不说卡在哪 | `ui/console.js` 的 `PHASE_TEXT` 只有三个键 | 扩展映射；非正常态用琥珀色点 + 具体文案（如「等待板子 12s」） |
| **U3** | 系统健康 8 格里没有输入链路 | `console.js:refreshHealth()` 的 8 项：机器人/推送/感知/血条/技能栏/倒计时/盲打/决策门 | 新增第 9 格「输入链路」，读 `board.state`；离线时给 `bad` 并显示最后一条日志 |

**验收**：板子不在时 `/api/bot.phase == "waiting_board"`（或 `board.state=="offline"`），
健康格显示红色「离线」，顶栏显示「等待板子」；板子在时回到 `live`。

---

### P1 · 反馈机制

| # | 问题 | 证据 | 改法 |
|---|---|---|---|
| **U4** | 8 处 `alert()`，0 个 toast；异步操作没有"已生效"的证据 | `console.js` 里 `alert(` 8 处、`confirm(` 3 处 | 加 `toast(msg, kind)`（右下角堆叠、4s 自动消失、可手动关）；`alert` 全部替换；`confirm` 保留（破坏性操作该拦） |
| **U5** | 服务断开后各面板各写各的"服务未连接" | `refreshBot` / `refreshLive` / `refreshStats` 各自处理 | 顶部状态条统一提示，`toast` 只在状态切换时弹一次（避免每 3 秒刷屏） |

**验收**：启动失败、标注失败、清理完成都只出现 toast；`grep -c "alert(" ui/console.js` 为 0。

---

### P2 · 可访问性 / 一致性

| # | 问题 | 证据 | 改法 |
|---|---|---|---|
| **U6** | `--ink-3` 对比度 **2.73:1**，却用于所有解释性文字 | `console.css:17` `rgba(242,245,249,.32)`；用在 `.card-sub`(10.5px)/`.nav-label`(10px)/`.top-pid`/placeholder | 提到 ≈4.5:1（0.46 左右），另设 `--ink-4` 给纯装饰 |
| **U7** | `outline:none` 两处且无替代焦点环 | `console.css:613`、`697` | 全局 `:focus-visible{outline:2px solid var(--accent-soft);outline-offset:2px}` |
| **U8** | 表格没有横向滚动容器，窗口拉窄会裁列 | `.tbl` 8 列；只有 `@media(max-width:900px)` 收侧栏 | 加 `.tbl-wrap{overflow-x:auto}` 并包住两张表 |
| **U9** | 控制台没有任何键盘快捷键 | 只有悬浮窗有 `Alt+T/A/P` | `1~7` 切页、`/` 聚焦图鉴搜索、`Esc` 退出搜索 |

**验收**：我算的对比度 ≥4.5:1；Tab 能看见焦点；窗口 900px 宽时表格可横向滚动。

---

### P3 · 指标闭环（"改动有没有用"）

| # | 问题 | 证据 | 改法 |
|---|---|---|---|
| **U10** | 战绩只有一个当前值，没有历史 | `record.save_state()` 只覆写 `record_state.json` | 追加 `logs/record_snapshots.jsonl`：每判完一场一行 `{ts,wins,losses,dw,dl}` |
| **U11** | 没有"今日增量 / 近 20 局" | `/api/stats` 只有赛季累计 | 新增 `progress{today, recent20, since}`，全部由快照算 |
| **U12** | 战绩页看不出趋势 | `console.js:refreshStats()` 只有赛季四项 | 加「今日 +N/-M」与「近 20 局胜率」两格；无数据时显式写"跑完一局后出现" |

**验收**：手工往快照里写 3 行后，`/api/stats.progress.today` 出现 `wins/losses/rate`，
页面两格有数字；快照为空时两格是 `—` 而不是 0。

---

### P4 · 后续

| # | 内容 | 状态 |
|---|---|---|
| U13 | 对局表行点击 → 右侧抽屉渲染 `match_detail`（`/api/match` 早就实现但前端从未调用） | ✅ `b38e42a` |
| U14 | 日志页按 `classifyLine` 分类做筛选 chips（全部/决策/异常，带计数） | ✅ `789d8a0` |
| U15 | 机器人"这一手"：`/api/decision` + 概览页一张卡（**只读 `turns.jsonl`，不改 `robot.py`**） | ✅ `f96c130` |
| U16 | `docs/FRONTEND.md` 里 `data.html` 段落与实现对齐 | ✅ `3394cbb` |
| U17 | `bot_stdout.log` 被 `[standalone]` 日志污染 | ✅ `5bab168` |
| U18 | 回合明细把轮询行当手数（同一手最多重复 5 次） | ✅ `fba9e28` |
| U19 | 队伍页把 `my_team.moves` 原样返回却从不显示（配置过期是最隐蔽的 bug） | ✅ `7f4455a` |
| U20 | 段位卡读数卡死时，走势图仍画着 100 根假柱子 | ✅ `44e37b1` |
| U21 | 头像标注只取前 60 张、无翻页 —— 我方 561 张永远标不完 | ✅ `e150fe2` |
| U22 | 对局记录只取最近 40 场 / 517 场，无翻页；且每次请求跑两遍 `summarize()` | ✅ `ab4978d` |
| U23 | 图鉴 857 只只显示前 120 只，无「加载更多」 | ✅ `f93cd57` |

#### U15 为什么不改 `robot.py`

原计划是在 `_publish_state()` 里加 `plan{action, why}`。但那份文件同时被
另一个会话在改（`standalone.py` / `app.js` 20:43 还在写），而动主循环的
风险远高于收益 —— 决策**本来就在 `turns.jsonl`**（`action` + `why`），
只是没人读。所以改成服务端只读日志的 `/api/decision`：

```jsonc
{ "action": ["skill", 1], "why": "best affordable dmg 107 (red)",
  "turn": 3, "ts": "16:06:20", "age": 16835.7,
  "mid": "20261121-160432", "my_pct": 45.9, "foe_pct": 100.0, "energy": 0 }
```

前端在概览页显示成「出招 1 · T3 · 46% / 100%」+ 一行理由；
`age > 180s` 时整块降透明度并标成「5h前」，不会被误读成当前这一手。

#### U17 的现场与修法（2026-11-21 20:48）

```
bot_stdout.log 最后 48KB，匹配 BLE/GATE/SKILL => 0 条
```

`standalone.py` 用 `config.log()` 往同一个文件写 `[standalone] FOE -> ...`，
UI 服务常驻时每秒 2~4 行。后果：`bot_activity()` 永远返回 None，
**刚做完的 phase 推断整个失效**（退回 live/stale，看不出等待板子）。

修法选了**按前缀过滤 + 窗口放大到 256KB**，而不是改 `standalone.py`：
后者是另一个会话的文件，而且它写日志是有意的（独立模式要留痕）。
修完实测：

```
{"event": "running", "counts": {"waiting_board": 1, "focus_lost": 2,
 "board_offline": 10, "match_end": 13, "lobby": 58, "running": 384}}
```

之前这些计数全是 0。回归测试会先写一条 `BLE waiting for board`，
再压 400 行 `[standalone]`，断言仍然读得到 `waiting_board`。

#### U18 的现场

`turns.jsonl` 是**轮询日志**：同一手每帧写一行。实测尾部 100 行里只有
94 手，最多一手重复 5 次。前端直接倒序显示，看起来像"机器人连按 5 次
同一张卡"。现在按 `(mid, turn, 血量, 动作)` 去重并显示
「最近 100 条轮询 → 94 手（已去重）」。

#### U19：配置过期是"最隐蔽的 bug"，但界面上看不到配置

`docs/AGENTS.md` 记着：`my_team.moves` 一过期，`my_card_costs()` 就会把
1 费技能当成 5 费，策略于是"莫名其妙选了低伤害技能"，很容易被误判成
策略 bug。而 `ui_server.team()` 把 `moves` 原样返回、前端**从不渲染** ——
等于没有任何人工核对入口。

现在 `/api/team` 把 id 数组联表展开：

```jsonc
"skills": [
  {"id": 7040320, "name": "火云车", "element": "火", "power": 140, "cost": 5},
  {"id": 7020840, "name": "借用",   "element": "普通", "power": 0, "cost": 0}
]
```

查不到的 id **保留成 `{"id": N, "name": null}`**，不静默丢掉 —— 让
"库里没有"和"没配"能区分。队伍页每只显示 4 个技能块（序号 + 名字 +
属性/威力/耗能），数量不是 4 时给警告。

#### U20：卡死的读数不能画成趋势

`badge_stuck` 为真时那 100 条 flat 是**段位卡读数卡在 2/3**，不是平局。
原来走势图照画 100 根 18% 高的灰柱，等于把"读坏了"伪装成"这些局都没分
胜负"—— 和绿灯骗人是同一类问题。现在整块换成说明文字，并指向新增的
「今日 / 近 20 局增量」。

#### U21：561 张待标注，只能看到前 60 张

实测 `/api/portraits?side=self` → `total: 561`，但前端固定 `limit=60`
且没有翻页。旧 UI 移除后头像标注积压了 1122 张，新 UI 做了标注页却
**没法翻到第二页**，等于积压永远清不掉。现在加 offset 分页（60/页），
显示「1–60 / 561 张待标注」+ 上一页/下一页，切 side 时重置到第 1 页。

#### U22 / U23：长列表都没有第二页

同一类问题在另外两处长列表上重复出现：

| 接口 | 实测总量 | 原来显示 | 修法 |
|---|---|---|---|
| `/api/matches` | 517 场 | 最近 40 场 | offset 分页，40/页，`1–40 / 517` |
| `/api/dex` | 857 只 | 前 120 只 | 「加载更多」追加，120/次 |

图鉴用追加而不是翻页：网格布局下滚到底继续加载更自然，也不会丢掉
已经浏览的位置。实测点击一次 120 → 240 张。

顺带修掉 `/api/matches` 的性能问题：原来一次请求调
`summarize(limit)` 和 `summarize()` **两遍**，每遍都要把三份 jsonl
全量过一遍（517 场 / 3181 条回合）。现在只跑一遍再切片。

---

## 二、执行顺序与提交（实际）

| 步 | 内容 | 提交 |
|---|---|---|
| 0 | 本文件 | `06b4094 docs(ui): UX 优化计划清单` |
| 1 | U1+U2+U3+U10+U11 | `9cdf45d feat(ui): 状态可见性 + 战绩增量数据层` |
| 2 | U4+U5 | `cfc0806 feat(ui): toast 替代 alert，连接状态只在变化时提示` |
| 3 | U6~U9 | `6fa031c fix(ui): 对比度 2.73→4.72、焦点环、表格横向滚动、键盘快捷键` |
| 4 | U12 | `b93fef7 feat(ui): show today and last-20 win delta on stats page` |
| 5 | U16 | 见下方「执行记录」 |

---

## 二之二、执行记录（2026-11-21 晚）

**已完成**：U1–U12、U16。

| # | 落地位置 | 验证 |
|---|---|---|
| U1 | `ui_server.bot_activity()` / `board_status()` / `_line_age()` | `test_ui` 8 条新断言 |
| U2 | `console.js` `PHASE_TEXT` / `PHASE_LEVEL` / `fmtAge()` | 真实渲染：`top_text=未运行` |
| U3 | `console.js refreshHealth()` 第 0 格 + `console.html #botLink` | 健康格 8→9 |
| U4 | `console.js toast()`、`setConn()`；8 处 alert 清零 | `grep -c "alert(" = 0` |
| U5 | `refreshBot` 的 `setConn(true/false)` | 断开只提示一次 |
| U6 | `console.css --ink-3: .50`，新增 `--ink-4: .32` | 对比度自算：4.72:1（bg-3 最差） |
| U7 | `:focus-visible` 焦点环 | 渲染实测 `outline: solid 2px` |
| U8 | `.tbl-wrap` + 两处表格 + 生成的技能表 | 渲染实测 `tbl_wrap=2` |
| U9 | `console.js` 键盘监听 | 按 `5` 实测切到 `page-dex` |
| U10 | `record.save_state()` 追加 `record_snapshots.jsonl` | 单测：无快照时 `progress=None` |
| U11 | `ui_server._progress()` + `stats().progress` | 单测：2 胜 1 负 = 66.7% |
| U12 | `console.js refreshStats()` 两格 | 战绩页 5→7 格 |
| U13 | `console.js openMatch()` + `console.html #matchDrawer` | 渲染实测：点行打开抽屉、含回合表 |
| U14 | `console.js renderLog()` + `#logFilter` chips | 渲染实测：全部 887 行 → 异常 0 行 |
| U15 | `ui_server.last_decision()` + `/api/decision` + `console.js refreshDecision()` | 实测返回真实决策；渲染「出招 1 T3 46% / 100% best affordable dmg 107」 |
| U16 | `docs/FRONTEND.md` §3.1 / §3.2 / §4.2 / §4.3 | — |
| U17 | `ui_server._robot_lines()` 按 `[standalone]` 前缀过滤 + 窗口 16KB→256KB | 计数从全 0 变成 `board_offline:10 / focus_lost:2 / waiting_board:1` |
| U18 | `console.js refreshTurns()` 按 `(mid,turn,hp,action)` 去重 | 渲染实测：100 条轮询 → 94 手 |
| U19 | `ui_server.team()` 联表展开技能 + `console.js skillRows()` | 渲染实测：5 只 / 20 个技能块，`借用 普通·0费` 等可读 |
| U20 | `console.js refreshStats()` 的走势分支 | 渲染实测：`spark_bars=0`，显示"段位卡读数卡死" |
| U21 | `console.js refreshLabel()` + `#labelPager` | 渲染实测：`1–60 / 64`、`第 1 / 2 页` |
| U22 | `ui_server.matches_summary(offset)` + `console.js renderMatchPager()` | 单测：`offset=3` 与 `offset=0` 无重叠、total 一致；渲染 `1–40 / 517`、`第 1 / 13 页` |
| U23 | `console.js loadDex()` + `#dexMore` | 渲染实测：点一次 120 → 240 张，`还有 737 只` |

**验证基线**：`python src/test_ui.py` → 127 项全过（原 62）；
`--no-dom` 91 项；`test_record.py` / `test_imports.py` / `test_policy_cost.py` ALL PASS。

**未做**：无（U15 用只读方案绕开了对 `robot.py` 的依赖）。

---

## 三、验证命令

```powershell
$py = "C:\Users\Lenovo\Desktop\hardwork\CV_ESP_Game_1\.venv\Scripts\python.exe"

# 契约 + 静态资源 + 对比度 + 日志尾推断 + DOM（DOM 需要系统 Python，它才有 pywebview）
python src\test_ui.py --no-dom        # 91 项，约 5 秒
python src\test_ui.py                 # 127 项，约 90 秒，含真实渲染

# 数值/逻辑回归（UI 改动不该影响它们，跑一遍确认）
& $py src\test_record.py              # 战绩读写 + 快照
& $py src\test_imports.py             # robot.py 模块级名字
& $py src\test_policy_cost.py         # 耗能参与决策
```

**基线（本轮结束）**：`test_ui.py` 127 项、`--no-dom` 91 项、
其余三个脚本 ALL PASS。

---

## 四、明确不做

- 不改悬浮窗的整窗 alpha 方案（CSS 固定 1.0，只改 Win32 层）—— 对比度恒定，是被实测证明过的。
- 不改 `/api/stats` 的主口径（season 优先），只**增加** progress。
- 不做明暗双主题的统一 token 重构（收益低于风险，留到 UI 稳定后）。
- 不引入任何前端框架（控制台保持零依赖）。
