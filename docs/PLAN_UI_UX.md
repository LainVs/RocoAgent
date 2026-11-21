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

### P4 · 下一轮（本轮不做，先记录）

| # | 内容 | 依赖 |
|---|---|---|
| U13 | 对局表行点击 → 右侧抽屉渲染 `match_detail`（`/api/match` 已实现但前端从未调用） | 无 |
| U14 | 日志页按 `classifyLine` 的分类做筛选 chips | 无 |
| U15 | 悬浮窗显示 `plan{action,why}`（需要改 `robot.py` 的 `_publish_state`） | A 会话 |
| U16 | `docs/FRONTEND.md` 里 `data.html` 段落与实现对齐 | 无 |

---

## 二、执行顺序与提交

| 步 | 内容 | 提交信息 |
|---|---|---|
| 0 | 本文件 | `docs(ui): UX 优化计划清单` |
| 1 | U1+U2+U3 | `feat(ui): 状态可见性——等待板子/焦点丢失/板子离线可辨认` |
| 2 | U4+U5 | `feat(ui): toast 替代 alert，连接状态只提示一次` |
| 3 | U6~U9 | `fix(ui): 对比度 2.73->4.5、焦点环、表格横向滚动、控制台快捷键` |
| 4 | U10~U12 | `feat(ui): 战绩增量——逐场快照 + 今日/近 20 局` |
| 5 | U16 | `docs(ui): FRONTEND 与实现对齐` |

---

## 三、验证命令

```powershell
$py = "C:\Users\Lenovo\Desktop\hardwork\CV_ESP_Game_1\.venv\Scripts\python.exe"

# 契约 + 静态资源 + DOM（DOM 部分需要系统 Python，因为它有 pywebview）
python src\test_ui.py --no-dom        # 3 秒
python src\test_ui.py                 # 40 秒，含真实渲染

# 数值/逻辑回归（不应被 UI 改动影响，跑一遍确认）
& $py src\test_imports.py
& $py src\test_policy_cost.py

# 对比度重算（改 --ink-3 之后）
& $py -c "..."   # 见提交说明里的脚本
```

**基线（改动前已确认）**：`test_ui.py` 62 项、`test_imports.py` ALL PASS、
`test_policy_cost.py` ALL PASS。

---

## 四、明确不做

- 不改悬浮窗的整窗 alpha 方案（CSS 固定 1.0，只改 Win32 层）—— 对比度恒定，是被实测证明过的。
- 不改 `/api/stats` 的主口径（season 优先），只**增加** progress。
- 不做明暗双主题的统一 token 重构（收益低于风险，留到 UI 稳定后）。
- 不引入任何前端框架（控制台保持零依赖）。
