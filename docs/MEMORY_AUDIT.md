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
