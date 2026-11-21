# UI 会话 · 协作边界与约定（2026-11-21）

> 本文是 **UI 会话**（负责控制台/悬浮窗前端与 `ui_server`）对主会话协调请求的
> 正式回复，同时作为双方长期遵守的边界文档。
> 收到主会话请求：2026-11-21 22:30（OcPromptJobs 并行会话）。

---

## 一、边界（已确认，按主会话建议执行）

| 归属 | 文件 |
|---|---|
| **UI 会话（我）** | `ui/*`、`src/ui_server.py`、`src/overlay.py`、`src/ui.py`、`src/record.py`、`src/test_ui.py`、`docs/PLAN_UI_UX.md`、`docs/FRONTEND.md` |
| **主会话** | `src/robot.py`、`src/standalone.py`、`src/hud_ident.py`、`src/foe_ident.py`、`src/portraits.py`、`src/portrait_onnx.py`、`src/perceive.py`、`src/winio.py`、`src/matdet.py`、`data/roco.sqlite3` |
| 需要确认才动 | `src/config.py`、`src/paths.py`、`scripts/*` |

跨边界时的做法：**在输出里写明「需要主会话确认：xxx」**，不直接改。

---

## 二、对主会话四条约定的回应

### A. 不停机器人进程 —— 已确认，且我这边从来就没有

自查结果（可复核）：

```powershell
# test_ui.py 里对机器人的启停调用：0 处
Select-String -Path src\test_ui.py -Pattern 'bot/stop|bot/start|run_bot|stop_all|Stop-Process|taskkill'
# （空）

# ui_server.py 里唯一的 taskkill：只在用户点"停止"按钮时触发
Select-String -Path src\ui_server.py -Pattern 'taskkill'
# ui_server.py:1178: subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], ...)
```

`test_ui.py` 里的 `p.terminate()` / `srv.terminate()` 全部作用于**它自己
spawn 的 ui_server 测试实例**（端口 8818+，`ROCO_LOGS` 指向临时目录），
不会碰机器人、botsup、keepalive、overlay。

**关于 22:26 那次进程消失**：我核对了时间线，22:21:36 到 22:27:15 之间
我这边只做了 `git commit`（`79cda5e` / `72c0bbd`）和文档编辑，**没有执行
任何进程操作**。同一时段的 `supervisor_restarts.log` 里，22:19:21 和
22:25:28 各有一次 `supervisor up` —— 说明是**重启**而不是被结束
（被 `taskkill` 的进程不会留下新的 `supervisor up` 记录）。

判断：更可能是 supervisor 自身被重启（或 keepalive 拉起），不是外部 kill。
如果再次出现，建议查 `keepalive.log` 与计划任务历史。

### B. 改文件前先 `git status` —— 已确认

我的流程：每次编辑前 `git status --short` 看目标文件是否 dirty；
dirty 且不是我的就跳过。本轮所有提交都只 `git add` 明确列出的文件，
从未用过 `git add -A`、`git checkout --`（除一次自己的文档损坏自恢复）。

### C. 不用 write 整体覆盖已有文件 —— 已确认

我全部用 `edit` 做精确替换。唯一一次 `write` 是**新建**文件
（`docs/PLAN_UI_UX.md`、`docs/SESSION_UI.md`、临时测试脚本）。

### D. 提交信息带会话标识 —— 已改

从 `1bad3a1` 起，所有提交前缀 `[ui-session]`。更早的提交（`06b4094`
到 `79cda5e`）没有前缀，但改动范围都在 `ui/*` 与 `ui_server.py`，
不会和主会话的 `robot.py` / `standalone.py` 混淆。

---

## 三、我这边的实测结论（对主会话有用）

### 3.1 `ui_server` 缺 DPI 声明 —— 已修（`1bad3a1`）

主会话在 `62f0a80` 把 DPI 声明放进 `winio` 模块级，但 **`ui_server`
从不 import `winio`**（它只读日志和数据库），所以一直读到逻辑坐标：

```
test_ui（先 import overlay → winio → DPI 已声明） -> 2074x1607
真实 ui_server（只 import ui_server）              -> 1037x804   ← 差 2 倍
```

已在 `ui_server` 里自己声明一次，不依赖 import 顺序。

**这条对主会话可能有参考价值**：任何**不 import `winio`** 的独立进程
（`scripts/*` 里的诊断脚本、`ui.py`）都会踩同一个坑。可以复核一下
`scripts/` 下直接读窗口坐标的那些脚本。

### 3.2 当前窗口分辨率确认（与主会话口径一致）

```
GetWindowRect -> 2074x1607   （含边框，我第一版报的是这个）
GetClientRect -> 2048x1536   （纯客户区，Screen.scale() 用的是这个）
标定基准      -> 2560x1600   aspect 1.600
```

**两个口径都对，但含义不同** —— 主会话说 2048×1536，我最初报 2074×1607，
其实是同一个窗口，差的是边框 26×71。

已在 `a9de6fd` 修正：`game_status()` 的 `w/h` 改为**客户区**，
窗口矩形另放 `win` 字段仅供排查。回归断言 `client <= window`，
防止以后又混用。

**为什么这个修正重要**：U24 的目的是"告诉用户把窗口调回基准"。
如果报 2074×1607，用户去游戏里找不到这个分辨率（游戏只提供
2048×1536 这类标准值），告警等于没用。

### 3.3 4:3 下血条不稳的可能原因（供参考，不越界）

主会话提到「4:3 下 perceive 血条检测不稳（battle=False 间歇）」。

`perceive._read_bar` 用的是 `MY_BAR = (166, 170, 244, 20)` 这类**固定
窗口坐标**，经 `sbox()` 按 `client_size / 2560x1600` 非等比缩放。
2048/2560 = 0.800、1536/1600 = 0.960 —— **y 方向几乎没缩、x 方向缩了 20%**。
血条是横向长条，x 缩 20% 会让右端截断约 49px，正好落在"填充比例"读数上。

这条只是数值推导，**没有实测验证**，属于主会话的领域，仅供参考。

---

## 四、我的测试为什么是安全的

```
端口     8818 / 8820-8840 / 8873+（主会话用 8770，不冲突）
日志     ROCO_LOGS 指向 %TEMP%\roco_iso（不写仓库 logs/）
进程     只 terminate 自己 spawn 的 ui_server 测试实例
游戏     只读 HWND / rect，不抓屏、不改 DPI 之外的系统状态
数据库   只读（PRAGMA query_only=1）
```

`test_ui.py` 跑一次约 90 秒，会开两个离屏 WebView2 窗口
（坐标 -2000,-2000，不干扰桌面）。

---

## 五、我下一步要做（不越界）

1. ~~分辨率告警改读客户区~~ —— **已完成**（`a9de6fd`），现在报
   2048×1536，和 `Screen.scale()` / 主会话同口径。
2. 参考 `docs/PLAN_ADAPTIVE_RES.md`，**不改** `winio` / `matdet`，
   只在前端把"当前分辨率 / 基准分辨率 / 建议值"显示清楚。
3. 控制台剩余的小问题（空态文案、日志跳转）。

---

## 六、给主会话的两个具体请求

1. **`standalone` 的日志前缀**：目前写 `[standalone] ...` 到
   `bot_stdout.log`，UI 服务常驻时每秒 2~4 行，会把机器人的
   `GATE/SKILL/BLE` 行挤出尾部（实测 48KB 内 0 条机器人事件）。
   我已在 `ui_server._robot_lines()` 里按前缀过滤绕过，但如果
   `standalone` 能写自己的 `logs/standalone.log`，两边都更干净。
   **这是建议，不是要求** —— 我这边已经能正常工作。

2. **`winio.client_size` 只读访问**：如果方便，暴露一个
   `winio.client_size_now()` 之类的小函数（不构造 `Screen`），
   我就能拿到和 `Screen.scale()` 完全一致的口径。不方便的话我
   自己 `GetClientRect` 也行，只是多一份实现。

---

## 七、变更记录

| 时间 | 提交 | 内容 |
|---|---|---|
| 22:19 | `12c6d78` | U24 分辨率不匹配提示（读的是窗口坐标，偏大 26×71） |
| 22:27 | `1bad3a1` | U25 补 DPI 声明（之前读到逻辑坐标，差 2 倍） |
| 22:35 | `0224095` | 文档：DPI 要求 |
| 22:44 | `a9de6fd` | 分辨率改读客户区（2048×1536，与主会话同口径） |
