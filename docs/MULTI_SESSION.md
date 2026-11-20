# 多会话协作约定

> 本项目同时有两个 OpenCode 会话在工作：
> **A 会话**：机器人（`src/robot.py`、数据、策略）
> **B 会话**：前端 / UI（`src/ui_server.py`、`src/overlay.py`、`web/`）
>
> 两边共用同一台机器、同一个 git 仓库。本文记录已经踩过的坑和约定。

---

## 一、已发生的事故

### 2026-11-21 05:32 - 06:05：机器人静默 33 分钟

**现象**

```
supervisor.out.log 最后一条: 05:40:36 starting bot
bot_stdout.log 停在:        06:00:10
botsup.py / robot.py 进程都已消失
```

**根因（两层）**

1. 本机同时有 **4 个 `ui_server.py`** 在跑（B 会话反复重启 UI）。
   如果有脚本按进程名批量杀 python（`Stop-Process -Name python`、
   `taskkill /IM python.exe`），会连带杀掉 `botsup.py` 和 `robot.py`。

2. 机器人原有的三层守护是 `robot ← botsup ← 人工启动`。
   `botsup` 自己被杀就再没人拉起来了。

**已做的补救**

- 新增 `src/keepalive.py`（最外一层）：每 60 秒检查 `botsup` 是否存活，
  不在就重新拉起。它只做这一件事，代码极简。
- 启动方式：
  ```powershell
  pythonw src\keepalive.py          # 后台常驻
  python src\keepalive.py --status  # 看 botsup/robot 状态
  ```

---

## 二、进程管理约定

### 绝对禁止：按进程名批量杀

```powershell
# 危险 —— 会杀掉机器人的 botsup 和 robot
Stop-Process -Name python -Force
taskkill /IM python.exe /F
Get-Process python | Stop-Process -Force
```

### 正确做法：按 PID 或按命令行精确匹配

```powershell
# 只杀 UI 服务器（按命令行匹配）
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*ui_server*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 只杀机器人（用项目自带命令）
python src\run_bot.py --stop
```

### 项目内各进程的识别特征

| 进程 | 命令行特征 | 归属 |
|---|---|---|
| `robot.py` | `... src\robot.py --seconds 28800` | A 会话 |
| `botsup.py` | `D:\Tools\botsup.py` | A 会话 |
| `keepalive.py` | `... src\keepalive.py` | A 会话 |
| `ui_server.py` | `... src\ui_server.py --port 88xx` | B 会话 |
| `overlay.py` | `... src\overlay.py` | B 会话 |

---

## 三、端口约定

| 用途 | 端口 |
|---|---|
| BLE 守护（espfast） | 9998 |
| UI 服务器 | 8817 / 8819 / 8832 / 8834 …（B 会话自行分配） |

**UI 端口不要用 9998** —— 那是 BLE 守护的端口，被占用会导致机器人连不上板子。

---

## 四、git 约定

### 提交前先看别人的改动

```powershell
git status --short
git log --oneline -5
```

如果看到不是自己改的文件，**不要 `git checkout` / `git reset`**，
那会丢掉对方未提交的工作。

### 只 add 自己改的文件

```powershell
# 好：明确列出
git add src/robot.py src/policy_v2.py

# 危险：会带上对方未完成的工作
git add -A
```

### 共享文件的冲突区

| 文件 | A 会话用途 | B 会话用途 |
|---|---|---|
| `src/robot.py` | 主循环、决策、识别 | 可能调用 overlay |
| `src/perceive.py` | 感知 | — |
| `data/roco.sqlite3` | 数据清洗 | UI 读队伍/头像 |
| `docs/` | 技术文档 | 前端文档 |

**`data/roco.sqlite3` 是二进制，冲突无法合并。**
A 会话改数据前先 `git status` 确认没有未提交的 UI 相关改动。

---

## 五、文件锁

`logs/robot.lock` 是机器人的单实例锁。如果机器人启动后立刻退出并报
"已有实例"，说明有残留：

```powershell
python src\run_bot.py --stop    # 正常清理
# 还不行就手工删
Remove-Item logs\robot.lock
```

---

## 六、给 B 会话的建议

1. **重启 UI 时只杀自己的 PID**，不要 `Stop-Process -Name python`。
2. UI 端口从 8817 开始往上加，避开 9998。
3. 如果要在 UI 里加"重启机器人"按钮，请调用
   `python src\run_bot.py --stop` / `--supervisor`，**不要自己杀进程**。
4. `src/overlay.py` 如果要在游戏窗口上画东西，注意别挡住
   `SKILL_BOX=(250,480,780,1520)` 和右上角对面头像区
   `(2148,47,84,76)` —— 那两处是机器人识别用的。
