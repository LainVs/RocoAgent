# ESP32 蓝牙管理与坐标系

> **改任何 BLE / 坐标相关代码前，先读完这份。**
> 最后更新：2026-11-20

---

## 一、铁律：BLE 只允许一个持有者

Windows 上 `BleakClient` 对同一块板子**不能有两个连接**。
一旦有两个进程同时持有，后连的会拿不到 GATT 服务，或者直接
`Device with address ... was not found`。

**正确做法**

```
robot.py  ──import──>  esp_shim.Session  ──BleakClient──>  ESP32
```

进程内唯一。开机只跑一个 robot.py。

**错误做法（历史上踩过的坑）**

| 东西 | 端口 | 问题 |
|---|---|---|
| `D:\Tools\espd.py` | 8765 | 老守护，会一直持有 BLE |
| `D:\Tools\espwatch.py` | — | **开机自启**，检测到 8765 没响应就拉起 espd.py |
| `D:\Tools\espfast.py` | 9998 | 新守护，也持有 BLE |
| `rocs.py` | — | 每条命令新开进程重连（5 秒/次，太慢） |

`espwatch.py` 在启动文件夹里（`espwatch.bat`），开机自动跑，
于是系统里同时有 **8765 和 9998 两个守护**，互相抢板子 ——
这就是"板子明明开着却永远连不上"的真正原因。

**已处理**：`espwatch.bat` 改名为 `espwatch.bat.disabled`。

---

## 二、现在的架构（最简）

```
┌─────────────┐
│  robot.py   │  同步主循环
└──────┬──────┘
       │ import
┌──────▼──────────┐
│ esp_shim.Session│  进程内单例
│  .client        │  BleakClient
└──────┬──────────┘
       │ BLE (GATT write, no response)
┌──────▼──────┐
│  ESP32-S3   │  USB HID 键鼠注入
└─────────────┘
```

**关键参数**

```python
ADDR = "14:C1:9F:3A:BB:35"     # 主地址
FALLBACK_ADDRS = (ADDR, "28:84:85:5C:96:21")   # 重烧固件后会变
CHAR = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # 写特征值
```

**为什么直连而不是守护进程**

- 直连：首次连接 2~5 秒（只一次），之后每条命令 ~50 ms
- 守护：省掉首次连接，但多一个进程 = 多一个抢链路的可能

在"稳定"和"快 2 秒"之间选稳定。

---

## 三、连接失败排查顺序

按这个顺序查，**每一步都要看实际输出**，不要猜。

### 1. 板子还在吗

```powershell
Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_303A' }
```

- 有 `HID-compliant mouse` / `HID Keyboard Device` → 固件在跑 ✅
- 什么都没有 → 板子没插好 / 没供电 / 固件没起来

### 2. 蓝牙无线电开着吗

```powershell
# WinRT，不需要管理员
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$r = [Windows.Devices.Radios.Radio]::GetRadiosAsync()
# 或用现成脚本
powershell -File C:\Users\Lenovo\AppData\Local\Temp\oc_bton.ps1
```

应该看到 `RADIO Bluetooth state=On`。

### 3. 扫描器还能看到东西吗（最关键）

```powershell
$py -W ignore $env:TEMP\oc_scan10.py
```

**判定标准**

| 扫到的设备数 | 结论 |
|---|---|
| 20~50 个 | 正常 |
| 0~3 个 | **适配器有问题**，跟板子无关 |

扫不到附近的手机/耳机 = 射频层挂了，再怎么调代码都没用。
处理：关掉蓝牙开关 → 等 5 秒 → 打开；或重启电脑。

### 4. 地址对不对

```powershell
# 看 Windows 记住的配对记录
Get-PnpDevice | Where-Object { $_.FriendlyName -match 'ESP32' } |
  Select-Object Status, InstanceId
```

`InstanceId` 里的 `DEV_2884855C9621` 就是 MAC（28:84:85:5C:96:21）。
重烧固件后这个值会变，要同步更新 `FALLBACK_ADDRS`。

### 5. 有没有别的进程占着

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'espwatch|espd\.py|espfast' } |
  Select-Object ProcessId, CommandLine
```

**应该为空。** 有就杀掉：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'espwatch|espd\.py|espfast' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

---

## 四、已知的坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `Device with address ... was not found` | ① 板子没广播 ② 别的进程占着 ③ 适配器半死 | 按上面 1→5 查 |
| 第一次连失败，第二次成功 | Windows BLE 缓存没准备好 | 代码里已做 2 次重试 + 2 秒间隔 |
| `BleakCharacteristicNotFoundError` | 连上了但服务发现没完成 | 代码里已校验特征值，缺失就重连 |
| 扫到设备但连不上 | 扫描结束到连接之间设备停了广播 | 用扫描返回的 `BLEDevice` 对象直连，别用地址 |
| 按键没反应但点击正常 | 游戏失焦，键落到别的窗口 | `send_key` 有前台检查，见下 |

---

## 五、坐标系（第二大概率的 bug 来源）

工程里同时存在**两套**坐标，因为历史原因。混用就会点错位置。

### 两套坐标

| 名字 | 原点 | 常量例子 | 用哪个函数 |
|---|---|---|---|
| **窗口坐标** | 窗口左上角（含 13px 边框 + 58px 标题栏） | `MY_BAR` `FOE_BAR` `SKILL_BOX` `LOBBY_BTN` `ACTION_BTN_BAND` | `grab_s()` / `sbox()` / `phys()` / `abs_point()` |
| **客户区坐标** | 客户区左上角（截图原点） | `portraits.PORTRAIT_BOX` `foe_panel.FOE_PORTRAIT_BOX` `hud.ENERGY_BOX` | `grab_client()` / `sbox_client()` / `phys_client()` |

### 基准尺寸

```
BASE_CLIENT_W, BASE_CLIENT_H = 2560, 1600
BASE_OFF_X, BASE_OFF_Y       = 13, 58      # 客户区在窗口内的偏移
```

### 为什么不能只按窗口大小缩放

窗口 2586×1671 里客户区是 2560×1600。
如果按 2586 缩放，58px 的标题栏也被缩掉，
整块 HUD 会偏移几十像素 —— 血条只有 20px 高，一偏就完全读不到。

**正确换算**

```python
# 窗口坐标 -> 客户区像素
cx = (x - BASE_OFF_X) * (client_w / 2560)
cy = (y - BASE_OFF_Y) * (client_h / 1600)

# 客户区坐标 -> 客户区像素
cx = x * (client_w / 2560)
cy = y * (client_h / 1600)
```

### 抓图统一还原到基准尺寸

阈值、宽高范围、字形模板全是在 2560×1600 下标定的。
所以 `grab_s` / `grab_client` 抓完会**缩回基准尺寸**，
下游代码一行都不用改。

### 新增常量时怎么选

1. 这个位置是**从整窗截图**量的吗（含标题栏）？→ 窗口坐标
2. 是**从游戏截图**量的吗（截图不含边框）？→ 客户区坐标
3. 拿不准 → 用 `frames.py` 抓一张，量完再看 `BASE_OFF` 对不对

---

## 六、抢焦点与点击

游戏只认**硬件输入**（系统鼠标无效），所以点击必须走板子。
但 Windows 只在**前台窗口**收硬件键鼠。

`robot.click_abs()` 的顺序：

```
1. 前台不是游戏？ → _click_to_focus()
2. 目标点在客户区内且属于游戏窗口？ 否则丢弃（CLICK GUARD）
3. 发硬件点击
```

`_click_to_focus()` **必须点客户区正中央偏下**：

```python
cr = screen.client_rect_on_screen()
ble.click_at(cr[0] + cr[2] // 2, cr[1] + int(cr[3] * 0.62))
```

**历史 bug**：以前写死 `r[0] + 700, r[1] + 300`。
窗口 2586 宽时这是场地中央（27%, 18%），
窗口一缩小就变成**左上角**，正好砸在「返回 / 取消匹配」上 ——
于是"结算页一直点左上角"、"匹配成功不点进入对战"。

**任何坐标都不要写死像素偏移，用比例。**

---

## 七、日常操作

### 启动机器人（唯一正确方式）

```powershell
# 双击桌面快捷方式 "RocoAgent 面板"，或
# http://127.0.0.1:8770  →  控制  →  启动
```

不要手动 `python robot.py` 起第二个实例 —— 单实例锁会拒绝，
但如果锁文件残留要先删 `logs/bot.pid`。

### 停止

面板点"停止"，或：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'robot\.py' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

### 看 BLE 日志

```powershell
Get-Content D:\Projects\RocoAgent\logs\ble.log -Tail 20
```

只记连接事件，不刷屏。

---

## 八、如果以后要改回守护进程

**别改。** 如果真有必要（比如要支持多客户端），必须：

1. 只保留**一个**守护，删掉另一个
2. 守护自己负责重连，客户端只管发命令
3. 客户端启动前检查端口，**端口被占就不要再起一个**
4. 从启动文件夹和计划任务里删掉所有自动拉起的东西

历史教训：三套并存 → 互相抢 → 一晚上都在"连不上"。
