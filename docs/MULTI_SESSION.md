# 多会话协作约定（并行 AI 会话实测踩坑）

> 2026-11-21 · 起因：机器人进程反复消失、git 提交互相覆盖

---

## 一、现状

`OcPromptJobs` 计划任务每分钟跑一次 `D:\Tools\oc_prompt_ui\dispatcher.py`，
它按 `jobs.json` 里的 `interval`（30 分钟）向**三个独立 opencode 会话**发送提示词：

```json
{"title": "项目理解与实现计划", "sessionID": "ses_e08692...", "interval": 30, "enabled": true}
{"title": "悬浮窗伤害计算器 UI 规划", "sessionID": "ses_e0649d...", "interval": 30, "enabled": true}
{"title": "项目深度解读", "sessionID": "ses_e02a58...", "interval": 30, "enabled": true}
```

三个会话的 `cwd` **都是** `D:\Projects\RocoAgent`。

派发方式（`run_one.py`）：

```
opencode run --session <sid> --model <model> --auto <prompt>
```

---

## 二、实测踩到的三类冲突

### 2.1 机器人进程被外部结束（最严重）

```
22:19:21 supervisor up, starting bot
22:25:28 supervisor up, starting bot     <- 重启
22:26:55 bot_stdout 最后一行
之后进程全部消失，supervisor_restarts.log 里没有任何退出记录
```

`supervisor_restarts.log` 只记 `starting`，没有 `rc` —— 说明**不是崩溃**，
是被外部 `Stop-Process` / `taskkill` 结束的。

时间上和 `run.log` 里 `22:21:36 RUN 项目深度解读` 吻合。

### 2.2 git 提交互相覆盖

我误覆盖过 `src/test_standalone.py`：把一份 250 行、7 节断言的回归测试
换成了 116 行。`git show --stat` 显示 220 行删除才发现。

**教训：改已有文件前先读，不要用 write 整体覆盖。**

### 2.3 改同一批文件

双方都在改：

```
robot.py / standalone.py / hud_ident.py / portraits.py / ui/app.js
```

---

## 三、约定

### 3.1 绝对不要停机器人

禁止：

```
src\run_bot.py --stop
scripts\stop_all.py
Stop-Process -Name python        （会连带 ui_server / overlay）
taskkill 名字含 python 的进程
```

需要独占游戏窗口做测试时，只能**暂停并声明**，例如在输出里写：
「已暂停机器人，请主会话重启」。

### 3.2 文件所有权

| 范围 | 归属 |
|---|---|
| `ui/*`（app.js / console.js / index.html / style.css） | UI 会话 |
| `src/standalone.py` / `hud_ident.py` / `foe_ident.py` / `portraits.py` / `portrait_onnx.py` / `robot.py` | 主会话 |
| `docs/*` | 谁写谁负责，先看有没有同名文件 |
| `assets/portraits_foe/*` | 主会话 |

需要改对方的文件时，在输出里写明「需要主会话确认：xxx」，不要直接改。

### 3.3 改文件前先 `git status`

有未提交改动时，先判断是不是对方的。**不要 `git checkout --`**。

### 3.4 提交信息带会话标识

方便回溯是谁改的。

---

## 四、发消息给并行会话

改 `jobs.json` 的 `prompt` 就等于发消息（dispatcher 会按 interval 派发）。

不想等 30 分钟，直接调：

```powershell
python scripts\notify_peer_sessions.py --dry   # 只看
python scripts\notify_peer_sessions.py         # 真发
```

脚本会：
1. 备份 `jobs.json`
2. 把协调消息写进每个 job 的 `prompt`，`lastRun` 置 0
3. 直接调 `run_one.py <sid>` 立刻派发
4. **发完恢复原 prompt**（否则每 30 分钟重复发这条协调消息）

---

## 五、怎么判断"是不是被别的会话搞的"

| 现象 | 判断方法 |
|---|---|
| 进程消失但无 traceback | 看 `supervisor_restarts.log` 有没有 `rc`；没有 = 被外部杀 |
| 文件被覆盖 | `git show --stat HEAD` 看删除行数是否异常 |
| 代码莫名变了 | `git log --oneline -5` 看有没有不是自己写的提交 |
| 计划任务是否刚跑过 | `Select-String run.log -Pattern "HH:M"` |

---

## 六、已知的调度器细节

- `dispatcher.py` 用 `LOCK` 文件防重入（`O_CREAT|O_EXCL`）
- 派发前**先落盘 `lastRun`**，所以中途被杀也不会重复发
- `run_one.py` 有 `RUN_TIMEOUT = 900` 秒
- `inflight_<sid>.lock` 防同一会话并发

这些机制只防"重复派发"，**不防"两个会话改同一个文件"**。
所以上面的约定必须人工遵守。
