# UI 待重新设计

> 2026-11-20：旧的 tkinter 面板（`src/roco_gui.py`，988 行）和 Web 面板
> （`src/roco_ui.py`，655 行 + `ui/` 49KB 静态资源）**已全部移除**。
> 本文记录"为什么移除"和"新 UI 必须补回什么"，避免重做时遗漏功能。

---

## 一、为什么移除

| 问题 | 实测 |
|---|---|
| 不丝滑 | `_tick` 每 2 秒无条件刷新**三个页面**（战绩 + 回合 + 头像），不管你停在哪个标签页 |
| 高占用 | 头像页每次重建 67 张缩略图要 **2 秒**，比 2 秒心跳还长，会把自己卡死 |
| 架构差 | 988 行单文件 `App(tk.Tk)`，所有页面、轮询、业务逻辑混在一起 |
| 不美观 | 深色卡片风格，但布局靠硬编码 padx/pady |
| 不实用 | 队伍编辑要逐格点选，没有批量操作 |

关键代码证据（`roco_gui._tick`）：

```python
def _tick(self):
    self._poll_log()
    ...
    self._refresh_results()      # 每次都刷
    self._refresh_turns()        # 每次都刷
    self._refresh_portraits()    # 每次都刷（内部虽有签名去重，但仍要 listdir）
    self.after(2000, self._tick)
```

---

## 二、旧 UI 提供的功能（新 UI 必须覆盖）

### 2.1 只读展示

| 功能 | 数据源 | 说明 |
|---|---|---|
| 机器人状态 | `robot_pid()` + `logs/bot_stdout.log` mtime | 运行中 / 已停止 + PID |
| 实时日志 | `logs/bot_stdout.log`（增量 tail） | 支持跟随、清屏、跳末尾 |
| 战绩统计 | `logs/results.jsonl` | 今日胜/负/胜率/未判定，近 20 场胜率 |
| 近 30 场走势图 | `logs/results.jsonl` | 柱状图，胜绿负红 |
| 回合统计 | `logs/turns.jsonl` | skill/energy/switch 次数、双方平均血量 |
| 回合明细表 | `logs/turns.jsonl` | 时间、回合号、血量、动作、理由 |
| 头像待标注列表 | `assets/portraits/_unknown_*.png` | 缩略图 + 文件名 |

### 2.2 操作

| 功能 | 实现 | 移除后的替代 |
|---|---|---|
| 启动机器人 | `run_bot.py --supervisor` | 命令行 |
| 停止机器人 | `run_bot.py --stop` | 命令行 |
| 打开日志目录 | `explorer logs` | 命令行 |
| **头像标注** | 重命名 `_unknown_*.png` → `精灵名.png` | ⚠️ **暂无替代**，见下 |
| **队伍编辑** | `team_db.save_pet()` | ✅ `python src\team_db.py --set` |

---

## 三、移除后留下的缺口

### 3.1 头像标注（无替代）

旧 UI 里点缩略图 → 输入精灵名 → 重命名文件。这是唯一的人工标注入口。

**现有替代方案**：
```powershell
# 自动标注（名字条 OCR，准确率有限）
python src\label_portraits.py
```
但自动标注失败的那部分仍需人工。临时办法是直接重命名文件：
```
assets\portraits\_unknown_211609.png  ->  assets\portraits\音速犬.png
```

新 UI 应该把这个做成核心功能之一（见 §4）。

### 3.2 队伍编辑（已补命令行）

```powershell
python src\team_db.py                       # 查看用法和当前状态
python src\team_db.py --dump                # 导出当前队伍 JSON
python src\team_db.py --species-list        # 前 40 个可选精灵
python src\team_db.py --set 1 --species 音速犬 --nature 固执 --moves 火云车,力量增强
```

---

## 四、新 UI 的建议方向

### 4.1 架构

```
ui/                          # 前端（独立，可整体替换）
  index.html
  app.js                     # 无框架 or 轻量（Preact/Alpine），不引 React
  style.css
src/ui_server.py             # 只提供 JSON API，不含业务逻辑
```

**关键约束**：
- 服务端**只读日志文件**，不写业务数据（避免和机器人抢写）
- 前端**按需轮询**：只有当前标签页可见才拉数据，切走就停
- 日志用 SSE 或长轮询，不要 2 秒全量拉

### 4.2 必须解决的性能点

| 问题 | 解法 |
|---|---|
| 头像缩略图重建 2 秒 | 服务端启动时生成一次，缓存到 `logs/ui_thumbs/`，带 mtime 校验 |
| 每 2 秒刷三个页面 | 只刷当前可见页；页面切回来时刷一次 |
| 日志全量重读 | 服务端按 offset 增量返回（旧 `roco_ui` 已经这么做了） |
| `turns.jsonl` 941KB 每次读 | 服务端缓存解析结果，按 mtime 失效 |

### 4.3 可以复用的旧代码

旧 `roco_ui.py` 里有几个已经写对的函数，删掉前值得抄回来：

| 函数 | 价值 |
|---|---|
| `_tail_jsonl(path, size, max_bytes)` | 从文件尾部按字节读，不读全文件 |
| `cached_jsonl(path, max_bytes)` | 按 mtime 缓存的 jsonl 解析 |
| `_status_payload()` | 状态聚合（PID + 日志时间 + 战绩） |
| `api_logs(offset)` | 增量日志（前端带 offset 来拉） |

这些在 git 历史里（提交 `b899609` 之前）：
```powershell
git show 8a18c4c:src/roco_ui.py | Select-String "def _tail_jsonl" -Context 0,20
```

---

## 五、桌面快捷方式

原来的 `桌面\RocoAgent 面板.lnk` 指向 `http://127.0.0.1:8770`（Web UI）。
UI 移除后这个快捷方式已经失效，重新设计完成后再建新的。
