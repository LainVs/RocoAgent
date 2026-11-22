# 稳定启动说明（2026-11-20）

> 这一页只讲"怎么起、崩了去哪看"。换人策略见 `SWITCH_PLAN.md`。

---

## 1. 一句话

启动链改成三级，任何一级死掉都有日志：

```
selfcheck.py（自检，不出招、不连板子除非加 --ble）
      ↓ 全绿
run_bot.py --supervisor（脱离 shell 的看门狗）
      ↓ 拉起
robot.py（真正的机器人）
```

```powershell
# 1) 开游戏、站到竞技场，然后自检
& "<venv>\Scripts\python.exe" src\selfcheck.py --ble

# 2) 启动（看门狗 + 机器人，均脱离当前 shell）
& "<venv>\Scripts\python.exe" src\run_bot.py --supervisor

# 3) 状态 / 停止
& "<venv>\Scripts\python.exe" src\run_bot.py --status
& "<venv>\Scripts\python.exe" src\run_bot.py --stop
```

`<venv>` = `C:\Users\Lenovo\Desktop\hardwork\CV_ESP_Game_1\.venv`

### 1.1 前端（悬浮窗 / 控制台）用**另一个**解释器

本机两个解释器的能力是**分裂**的（2026-11-21 实测）：

| | `.venv`（AGENTS.md 的 `$py`） | 系统 Python（`C:\Python314`） |
|---|---|---|
| `bleak`（连板子） | ✅ | ❌ |
| `pywebview`（开窗口） | ❌ | ✅（装在用户 site-packages） |

原因是 `.venv` 建的时候 `include-system-site-packages = false`，
而 pywebview / pythonnet 装在
`%APPDATA%\Python\Python314\site-packages`。

所以就两条命令，各用各的：

```powershell
# 机器人 —— 必须 .venv（需要 bleak）
& "<venv>\Scripts\python.exe" src\run_bot.py --supervisor

# 界面 —— 必须系统 Python（需要 pywebview）
python src\ui.py
```

用错解释器的表现分别是：

- 用 `.venv` 开界面 → **没有窗口**，`overlay.py` 现在会打印
  「缺少 pywebview」并说明该换哪个解释器（以前是静默退出）。
- 用系统 Python 起机器人 → 连板子时 `ModuleNotFoundError: bleak`。

`src\test_ui.py` 也只依赖 pywebview（不 import robot），所以用系统 Python 跑：

```powershell
python src\test_ui.py          # 62 项
```

---

## 2. 已定位的崩溃根因：winrt 的旧 MSVCP140

**现象**：机器人启动 4 秒后直接消失，没有 traceback；Windows 事件日志里是

```
APPCRASH python.exe
faulting module: MSVCP140.dll 14.29.30157.0   exception: c0000005
```

**根因**：

| 位置 | 版本 |
|---|---|
| `C:\Windows\System32\msvcp140.dll` | **14.51**（2026-05，系统最新） |
| `.venv\Lib\site-packages\winrt\MSVCP140.dll` | **14.29**（2021，包自带） |

bleak 连接蓝牙时才 import `winrt`，而 WinRT 的 .pyd 会把同目录的 14.29 拉进进程。
之后 onnxruntime / cv2（2026 年构建）调用 C++ 运行库时就命中这个 2021 年的旧实现
→ 访问违例。**导入顺序决定死活**，所以表现是"有时能跑几秒、有时一启动就死"。

**A/B 实测**（`logs/` 之外，可直接复现）：

```
不预加载:  msvcp140 = site-packages\winrt\MSVCP140.dll (14.29)
           connect BLE → import onnxruntime → EXIT=-1073741819 (0xC0000005)

预加载:    msvcp140 = C:\Windows\System32\msvcp140.dll (14.51)
           connect BLE → import onnxruntime → OK
```

**修法**（已写进 `robot.py` 顶部）：在 import numpy/cv2 **之前**按名字绑定系统
`msvcp140.dll`。之后任何模块再要 `msvcp140.dll`，Windows 都返回已加载的 14.51。

（`hud.warmup()` 也保留了：它在 BLE 连线前把 onnxruntime 载入，是同一问题的第二道保险。）

---

## 2.1 板子连接（BLE）——地址和名字都会变

> 2026-11-20 现场记录，重要。

- **不要写死 MAC，也不要按名字匹配。** 板子的 BLE 地址在换固件/重启后会变，
  广播名也可能为空。以前"按固定地址/固定名字找板子"就是这么失败的。
- **身份用服务认定**：NUS 服务 `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`，
  写特征值 `6E400002-...`。`esp_shim.discover()` 会：
  1. 先试上次成功的地址（`logs/ble_addr.txt`，只当提示）
  2. 扫描，优先看广播里带 NUS 的设备
  3. 再按信号强度取前 8 个，逐个 GATT 连接，认特征值
  4. 找到后写回 `logs/ble_addr.txt`，并在 `ble.log` 里记 `address changed: old -> new`
- 手动验证：`python src\esp_shim.py --discover`（约 10–60 秒）
- **插 USB 时 BLE 不广播**（11-20 实测）：板子会枚举成
  `USB\VID_303A&PID_1001`（ESP32 原生 USB，HID 键鼠复合设备），
  此时扫 28 个 BLE 设备都没有 NUS。用无线模式要么拔 USB，要么确认固件是双模。
  判断方法：`Get-PnpDevice -PresentOnly | ? InstanceId -match 'VID_303A'`

## 2.2 有线命令通道（USB CDC）—— 100% 稳定的方案

> 目标：不再依赖 BLE。一根 USB 线同时提供 **HID 键鼠**（游戏收到的输入）
> 和 **CDC 串口**（robot 发的指令）。USB 没有配对、广播、地址变化，
> 也没有"被手机占用"的问题。

**固件**：`C:\Users\Lenovo\Desktop\hardwork\CV_ESP_Game_1\firmware\blue_cdc`
（从 `blue/blue.ino` 复制并打了补丁，原文件没动）

相对原固件的改动：

1. 串口命令通道：`PING` / `KEY:f` / `KEY:space` / `CLICK:60` / `MOVESLOW:dx,dy,ms`
   行分隔，每条执行完回 `OK <cmd>`（电脑侧没收到回执就重发）
2. 命令**不再要求进入模式 3 或按按钮**，上电即接受（只影响 PC 指令，手动模式照旧）
3. KEY 增加 `tab` / `enter` / `backspace`
4. BLE 广播看门狗：断线后每 3 秒兜底 `startAdvertising()`
   （11-20 的"灯亮着但扫不到"就是没有恢复广播）

**编译**（已在本机跑通 3.3.8 之外的 PlatformIO 工具链）：

```powershell
cd C:\Users\Lenovo\Desktop\hardwork\CV_ESP_Game_1\firmware\blue_cdc
C:\Users\Lenovo\.platformio\penv\Scripts\platformio.exe run
```

**烧录**（USB 同一根线，但要先让芯片进下载模式）：

```
1. 按住 BOOT（GPIO0）
2. 点一下 RESET，松开 RESET，再松开 BOOT
3. 这时 USB 会枚举成 "USB JTAG/serial debug unit"（出现一个新 COM）
4. 烧录：
   platformio.exe run -t upload --upload-port COMx
```

> 为什么必须手动进下载模式：ESP32-S3 的 USB-OTG 和 USB-Serial-JTAG **共用同一个 PHY**。
> 固件跑起来后 USB 归 TinyUSB（HID+CDC），ROM 的下载通道就不可用了。
> 进入下载模式后芯片回到 ROM，才走 USB-Serial-JTAG。

**电脑侧**：`src/usb_link.py`，`esp_shim.Session.connect()` 会自动优先尝试它：

```
USB CDC 可用  ->  transport = usb:COMx
否则          ->  回退 BLE（按 NUS 服务发现地址）
```

日志里会明确写 `link via usb:COM7` 或 `link via ble:...`。
手动检查：`python src\usb_link.py`（打印端口 + PING 结果）。

## 3. 日志对照表

| 文件 | 谁写 | 看什么 |
|---|---|---|
| `logs/bot_stdout.log` | robot | 决策/心跳，每 15 秒至少一行 `state ...` |
| `logs/bot_stderr.log` | robot（excepthook） | **Python 异常栈**（带时间戳） |
| `logs/crash.log` | faulthandler | **原生崩溃**（段错误/访问违例）的 Python 栈 |
| `logs/supervisor_restarts.log` | botsup | 每次启动/退出：`child pid`、`rc`、存活秒数、退避、卡死击杀 |
| `logs/supervisor.out.log` | botsup 自身 stdout | 看门狗自己的输出（正常时基本为空） |
| `logs/ble.log` | esp_shim | 蓝牙连接/重连 |
| `logs/turns.jsonl` / `rounds.jsonl` | robot | 每手决策 / 每局结果 |

**判断"崩了还是被杀了"**：

- `bot_stderr.log` 有栈 → 代码异常（可修）
- `crash.log` 有栈 → 原生崩溃（看栈最后一行的扩展模块）
- 两个都没有、`supervisor_restarts.log` 只有 `rc=-1/1` → 外部终止或运行时退出，
  看 `rc` 和存活时长，再查 Windows 事件日志

---

## 4. 这一轮加的容错

| 改动 | 解决什么 |
|---|---|
| msvcp140 预加载 | 启动 4 秒静默死亡（0xC0000005） |
| OCR 预热 | BLE 连上后再 import onnxruntime 崩溃 |
| 抓屏失败不再当 0 血（`Perc._bar`） | 窗口最小化/掉帧时误触发力竭换人 |
| 没有窗口时不点任何东西 | `rect=None` 时白扣 advance 配额、窗口回来后点错位置 |
| `bot_stderr.log` / `crash.log` | 异常和原生崩溃都留栈，不再"日志停在半句" |
| `boot pid=... / exit rc=...` | 每次启动/退出都有边界，能对齐时间 |
| botsup 记录 `child pid` / `rc` / 存活时长 | 区分"崩了"和"被杀/预算到点" |
| botsup 连崩 3 次退避 30 秒 | 避免崩溃热循环刷屏、占满 CPU |
| botsup 4 分钟无日志判卡死并重启 | 卡在 BLE/抓屏/死锁时不会干等一整晚 |
| botsup 预算到点带走子进程 | 不再留下"没有看门狗的机器人"占着 `robot.lock` |
| 播报 OCR 按画面指纹去重 | 单次 OCR 实测 2.4 秒；以前播报停留的几秒里连续 OCR，把 0.12 秒的感知循环挤成几秒一帧 |
| `run_bot.py --supervisor / --stop / --status` | 看门狗和机器人整条链脱离 shell，不会被会话回收 |

---

## 5. 还有一件事没解释

10:50–10:57 的几次退出 **没有** WER 报告（不是 MSVCP140 那种原生崩溃），
`rc` 是 `4294967295`（= `sys.exit(-1)` 的形态）和 `1`（`taskkill /F` 的形态）。

现在每次退出都会在 `supervisor_restarts.log` 留下 `rc + 存活秒数`，
再跑一次就能区分是"看门狗杀 / 预算到点 / 外部终止 / 代码退出"。
**在没有新证据之前，不要把它当成已修复。**

---

## 6. 下次崩了，按这个顺序查

1. `logs/supervisor_restarts.log` 最后 10 行：`rc` 是多少、活了多久
2. `logs/crash.log`：有没有原生栈 → 有就直接看最后一帧是哪个扩展模块
3. `logs/bot_stderr.log`：有没有 Python 栈
4. 都没有 → Windows 事件日志 `Application`（Id 1000）看 faulting module
5. 仍没有 → 在 `botsup` 里把 `--bot-seconds` 设短、前台跑一遍复现

---

## 7. 自检能查什么

`selfcheck.py` 不点击、不出招；`--ble` 才会连一次板子（几秒）：

- Python 版本 / 解释器路径
- 系统 `msvcp140.dll` 版本，以及**进程内实际加载的是哪一个**（关键）
- winrt 自带的 14.29（只提示，不影响）
- 游戏窗口：rect / client / 缩放，找不到会提示先开游戏
- OCR 引擎：加载耗时 + 空图推理耗时
- `robot.lock` 是否被占用
- 日志目录可写

返回码 0 = 可以启动；1 = 有 FAIL，先按提示修。


## 网络代理（git push/fetch 失败的根因，2026-11-22 实测）

环境变量把三套代理指到 17894/17896，但**只有 17896（socks5）能通 GitHub**：

    HTTP_PROXY  = http://127.0.0.1:17894   <- 对 github.com TLS 直接断
    HTTPS_PROXY = http://127.0.0.1:17894   <-（unexpected eof while reading）
    ALL_PROXY   = socks5://127.0.0.1:17896 <- 这个是好的

libcurl 对代理的优先级是 **环境变量 > git 配置**，所以光在 repo 里配
http.proxy 没用；必须把 git 配置指向 socks5 的 17896（或清掉环境变量）。

已修复（repo 级配置）：

    git config http.proxy  socks5h://127.0.0.1:17896
    git config https.proxy socks5h://127.0.0.1:17896

（socks5h = 域名解析也走代理，比 socks5 更稳。）
验证：`git fetch origin` rc=0、`git ls-remote origin` rc=0。
曾因这个断口积压了 353 个 commit 未推送（2026-11-22 已补推）。

---

