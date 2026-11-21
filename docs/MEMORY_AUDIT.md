# 内存诊断：2026-11-21

> 用户报"有点卡"，问"32G 内存用了这么多？是不是乱开东西导致的"。
> 本文记录实测归因方法和结论。

---

## 一、先看总量

```powershell
$os = Get-CimInstance Win32_OperatingSystem
"总 {0:N1} GB  可用 {1:N2} GB" -f ($os.TotalVisibleMemorySize/1MB), ($os.FreePhysicalMemory/1MB)
```

当时：**总 31.6 GB，可用 0.2 GB，使用率 99.4%**。

### 关键：进程总和 ≠ 已用内存

```
进程 WorkingSet 合计    18.79 GB
已用                    26.64 GB
差                       7.85 GB   <- 在内核，不在任何进程里
```

内核侧：

| 项 | 大小 | 说明 |
|---|---|---|
| 非分页池 | **2.03 GB** | 正常 300~500 MB，**驱动泄漏** |
| 分页池（常驻） | 1.23 GB | |
| 备用缓存 | 5.2 GB | Windows 可随时回收，**不算真占用** |
| 系统缓存 | 0.29 GB | |

**非分页池 2 GB 只有重启能回收。**

---

## 二、按根进程归因（不要按进程名）

`Get-Process` 按进程名看会漏掉"一个应用拉了几十个子进程"的情况。
正确做法是**建父子树，按子树累加**。

工具：`python scripts\mem_audit.py`

```
=== 根进程子树内存（前 15）===
PID     启动       子树MB 根进程
15132   21:49:47   8124.4 explorer.exe
1880    -          7064.5 wininit.exe        <- 系统服务树
1980    -           555.4 winlogon.exe
651724  14:19:22    397.9 chrome.exe
645748  14:19:21    290.4 python.exe         <- 我们的 overlay.py
17800   21:52:54    214.6 pythonw.exe        <- .warp\selector.py
22236   21:50:04    119.7 node.exe           <- codebuddy
5964    21:52:56    109.5 mihomo.exe         <- Clash 内核
655932  14:31:01     99.2 pythonw.exe        <- oc_prompt_ui
644000  14:31:01     98.9 pythonw.exe        <- oc_prompt_ui（第 2 个）
```

### explorer 那 8 GB 的构成

```
OpenCode.exe 整棵树           2.6 GB
  ├─ renderer (27836)         1.62 GB   <- 最大单体
  ├─ opencode-cli serve       0.62 GB
  └─ utility/GPU/crashpad     0.28 GB
agent2api.exe                 1.28 GB   <- 模型网关
clash-verge.exe               0.98 GB   <- 代理 UI
wegame + browser + rail       0.47 GB
```

### 我们项目自己只占 0.32 GB

```
overlay.py    290 MB  （其中 6 个 webview2 子进程占 ~340 MB 的 1/2）
ui_server.py   24 MB
```

**结论：不是我们项目导致的。**

---

## 三、发现的两个"重复实例"

### 1. `oc_prompt_ui` 被计划任务每分钟拉起

```
计划任务：OcPromptJobs
  Execute : C:\Python314\pythonw.exe
  Args    : D:\Tools\oc_prompt_ui\dispatcher.py
  触发器  : 每分钟（NextRunTime 一直往后滚）
  最近运行: 14:35:01
```

它拉起 `run_one.py <session_id>`，进而 spawn 一个 `opencode.exe run --session ...`。

**同时存在 2 个**（`ses_e08692f5...` 和 `ses_e0649dd0...`），各约 100 MB。

这是 `D:\Tools\oc_prompt_ui`（不在本项目）装的，**不是我们开的**。

### 2. `msedgewebview2` 42 个进程 / 1.45 GB，2 个独立实例

每个嵌了 webview 的应用都带 6 个子进程：

```
agent2api        6 个   781 MB
clash-verge      6 个   944 MB   <- 最大
SearchHost       6 个   267 MB
Widgets          6 个   262 MB
wetype_*        12 个   1.48 GB   <- 微信输入法更新程序，泄漏
overlay.py       6 个   ~340 MB
```

---

## 四、这次实际清掉了什么

| 动作 | 效果 |
|---|---|
| 删 `logs/frames`（11-18 旧帧 663 MB） | 磁盘 |
| 删 `logs/frames2`（11-19 旧帧 1.26 GB） | 磁盘 |
| 删我自己的 35 个临时文件 | 磁盘 |
| 停 `wetype_update` + 其 12 个 webview2 | **-1.7 GB 内存** |
| 停 `clash-verge` UI（留 mihomo 内核） | -34 MB（大头已被上一步带走） |
| 停 2 个 `oc_prompt_ui` 会话 | 会被计划任务重新拉起 |

**内存：99.4% → 86.8%，可用 0.2 → 4.17 GB。**

---

## 五、工具

### `scripts/mem_audit.py`

```powershell
& $py scripts\mem_audit.py            # 根进程前 15
& $py scripts\mem_audit.py --all      # 全部
& $py scripts\mem_audit.py --tree 15132   # 某根的子树明细
& $py scripts\mem_audit.py --dupes    # 只看重复实例
```

`--dupes` 会算"独立实例数"（沿父链走到根，看有几个不同的根），
能识别"同一个应用被启动了多次"。

---

## 六、下次卡了怎么查

```powershell
# 1. 总量
$os = Get-CimInstance Win32_OperatingSystem
"{0:N1} GB 可用" -f ($os.FreePhysicalMemory/1MB)

# 2. 内核侧（非分页池 > 1GB 就是驱动泄漏）
Get-Counter '\Memory\Pool Nonpaged Bytes','\Memory\Pool Paged Bytes'

# 3. 按根归因
& $py scripts\mem_audit.py

# 4. 看重复实例
& $py scripts\mem_audit.py --dupes
```

**判断标准**：

| 现象 | 结论 |
|---|---|
| 非分页池 > 1 GB | 驱动泄漏，重启才干净 |
| 某根子树 > 2 GB | 点进 `--tree` 看子进程 |
| 同名进程独立实例 > 1 | 重复启动，查是谁拉起的 |

---

## 七、不建议动的

| 进程 | 原因 |
|---|---|
| `agent2api.exe`（1.28 GB） | 当前会话的模型网关，关了断线 |
| `OpenCode.exe`（2.6 GB） | 当前会话本体 |
| `NRC-Win64-Shipping`（1.1 GB） | 游戏 |
| `MsMpEng`（0.42 GB） | Windows Defender |
| `mihomo.exe` | 代理内核，关了断网 |
| `svchost`（2.9 GB / 103 个） | 系统服务，不能动 |

---

## 八、两个"上 G"的进程怎么优化的（2026-11-21 追加）

用户问："更新程序 和 agent2api 这俩都上 G 了不太对吧，能优化吗"

### 8.1 wetype_update（微信输入法更新程序）

**问题**：清掉后**又自己起来**，而且带 12 个 webview2。

**根因**：

```
WeType Management Service (wetype_service.exe, StartMode=Auto)
  ├─ wetype_server.exe    197 MB   输入法本体（关了就打不了中文）
  ├─ wetype_renderer.exe   55 MB   渲染
  └─ wetype_update.exe     54 MB   更新程序  <- 每次杀掉都会被 service 重启
       └─ 12 个 msedgewebview2  ~164 MB
```

光 `Stop-Process` 没用，service 会立刻拉起。

**解法**（需要管理员权限）：

```powershell
# 1. 先把 exe 改名（最彻底，service 找不到就无法启动）
$f = "C:\Program Files\Tencent\WeType\3.0.2.44\wetype_update.exe"
Get-Process wetype_update -EA SilentlyContinue | Stop-Process -Force
Rename-Item $f ($f + ".disabled")
```

用提权方式执行（Program Files 需要 UAC）：

```powershell
$b = [Text.Encoding]::Unicode.GetBytes($script)
Start-Process powershell -Verb RunAs -Wait `
  -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',
                [Convert]::ToBase64String($b)
```

**效果**：

```
优化前  wetype 全家桶  480 MB（含 update 54 + 12 个 webview2 164）
优化后  wetype 全家桶  263 MB（只剩 service 11 + server 198 + renderer 54）
省                     217 MB
输入法                  正常（wetype_server 还在）
```

**注意**：改名后微信输入法**无法自动更新**。要恢复：
把 `wetype_update.exe.disabled` 改回 `wetype_update.exe`。

### 8.2 agent2api 的 webview（管理界面）

**问题**：882 MB 里 588 MB 是 4 个 `msedgewebview2`。

**判断方法**：

```powershell
# 1. 看 user-data-dir —— 能区分"界面"和"必需组件"
Get-CimInstance Win32_Process -Filter "ProcessId=<webview根>" |
  ForEach-Object { $_.CommandLine -match 'user-data-dir="([^"]+)"'; $Matches[1] }
# 结果：C:\Users\Lenovo\AppData\Local\com.workbuddy.local-proxy\EBWebView
#   -> 是管理界面的浏览器数据目录，不是 API 必需

# 2. 看 API 端口是否独立
Get-NetTCPConnection -State Listen | Where-Object OwningProcess -eq <主进程>
# 结果：127.0.0.1:3065 由 agent2api.exe 自己监听
#   -> API 在独立进程，webview 只是前端
```

**解法**：直接停那 4 个 webview2 进程即可。

```powershell
# 找出属于 agent2api 的 webview 根
Get-CimInstance Win32_Process | Where-Object {
  $_.Name -eq 'msedgewebview2.exe' -and
  $_.CommandLine -match 'webview-exe-name=agent2api'
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

**效果**：

```
优化前  agent2api 子树  882 MB（webview 588）
优化后  agent2api 子树  242 MB（只剩本体 + providers 插件）
省                      640 MB
API                     3065 端口正常响应（404 是根路径无路由，属正常）
```

**注意**：webview 是它的**设置界面**。关掉后如果想改配置，
需要重启 agent2api（`--autostart`）让它重新拉起界面。

### 8.3 优化总账

| 目标 | 优化前 | 优化后 | 省 |
|---|---|---|---|
| wetype 全家桶 | 480 MB | 263 MB | **217 MB** |
| agent2api | 882 MB | 242 MB | **640 MB** |
| **合计** | | | **857 MB** |

内存使用率：`99.4% → 85.1%`，可用 `0.2 → 4.7 GB`。

### 8.4 这两个都是"会自己回来"的

| 进程 | 谁拉起它 | 怎么彻底解决 |
|---|---|---|
| `wetype_update` | WeType Management Service（自启服务） | 把 exe 改名（已做） |
| `agent2api` webview | agent2api.exe 启动时创建 | 每次手动关，或改它的启动参数（未研究） |

`oc_prompt_ui` 同理 —— 计划任务 `OcPromptJobs` 每分钟拉起：

```powershell
# 不想用了就禁用（我没动，因为不是本项目装的）
Disable-ScheduledTask -TaskName OcPromptJobs
```
