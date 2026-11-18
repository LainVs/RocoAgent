# 坐标体系改造任务书 · 修订版（2026-11-18）

> 由主会话下达。用户原话：
> **「我们应该优先选用最有效、最精确、最普适的算法。并且进行逐步验证。」**
> **「我们之前做过检测的完全适应不同分辨率的方案，在禁忌魔法那个项目。」**
>
> ⚠️ 本版**推翻**上一版「从零设计」的思路。已有一个验证过的方案，先复用它。

---

## 零、重大发现：方案已存在

`禁忌魔法` = `C:\Users\Lenovo\Desktop\hardwork\CV_ESP_Game_1`（打包为 `禁忌魔法.exe`）。

**RocoAgent 现在跑的就是这个项目的 `.venv`**，所以可以直接 import，不用重写：

```python
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\hardwork\CV_ESP_Game_1")
from src.script import _mode4_shared as M
```

### 实测（我已跑过，不是推测）

```
GAME_TITLE 默认     洛克王国            ← 默认值就是我们的游戏
get_game_window     (480, 41, 2586, 1671)   物理像素，HWND 已锁定 3476384
_sys_scale          2.0                ← 200% DPI
check_occluded      None               ← 当前无遮挡
resolve_region([166,170,244,20], winrel ref 2586×1671, win)
                    -> [646, 211, 244, 20]   ← 绝对坐标 = 480+166, 41+170
```

---

## 一、它已有的六层机制（`src/script/_mode4_shared.py`，44 KB）

| # | 机制 | 函数（行号） | 解决什么 | 我们能不能直接用 |
|---|---|---|---|---|
| 1 | **窗口身份锁定** | `_resolve_game_window_obj` (L502)、`_is_hwnd_valid` (L483) | 一次找到 HWND 就锁死，每帧只验证存活，绝不换窗口 | ✅ 直接用 |
| 2 | **DPI 自洽** | `_thread_dpi_aware` (L209)、`_dpi_scale` (L295)、`_sys_scale` (L568) | rect 与截图永远同一坐标系，不怕 200% 缩放 | ✅ 直接用 |
| 3 | **winrel 区域映射** | `resolve_region` (L551) + `_meta.ref_window` | 配置存窗口相对坐标，运行时线性缩放 + 窗口原点偏移；老配置无 `coord:winrel` 时原样返回 | ✅ 直接用 |
| 4 | **多尺度模板匹配** | `match_any_ex` (L837)、`_get_scaled_template` (L817)、`content_scale` (L792) | 9 档 scales `[0.3…1.5]` + DPI 归一化 `tscale` + 缩放缓存 | ⚠️ 改思路（我们没模板，见下） |
| 5 | **网格扩框搜索** | `grid_union_box` (L867)、`match_any_grid` (L889) | 目标框扩成 3×3 网格再搜，容忍区域偏移 | ⚠️ 改思路 |
| 6 | **遮挡 / 前台守卫** | `_zorder_occluder` (L685)、`check_occluded` (L751)、`guard_foreground` (L662) | 按 Z 序找第一个盖住游戏 >30% 的窗口；检测前台是否是游戏 | ✅ 直接用 —— **这是我们 FOCUS LOST 的正解** |

### 关键认知

**第 6 条不是「抢焦点」，而是「失焦就别动」。**
我们现在的代码在失焦时还在发按键（48 次 `FOCUS LOST`），这才是伤害来源。
正确做法：发键前先 `guard_foreground()`，False 就跳过这一回合，等恢复。

---

## 二、为什么不能全靠 winrel 线性缩放

`resolve_region` 是纯线性缩放：`x' = win[0] + rel[0] * (win[2]/ref_w)`。

禁忌魔法用它没问题，因为它的检测是**模板匹配**——模板本身多尺度搜索，
坐标有偏差也能命中。

**我们的血条是像素颜色比例读数**，差 10 px 就可能读到墙壁。所以需要多一层：

### 分层方案（每层独立可验证）

```
L0  窗口层   HWND 锁定 + DPI 自洽                      ← 搬运，已验证
L1  坐标层   winrel 线性缩放（ref_window 2586×1671）    ← 搬运，已验证
L2  搜索层   网格扩框（ring=1），容忍偏移                ← 搬思路
L3  锚定层   检测到元素 bbox 后，子 ROI 按 bbox 比例取    ← 新增，最精确
L4  守卫层   遮挡/前台检测，不可信就跳过                  ← 搬运，解决 FOCUS LOST
```

**L3 是新增的，也是精度关键**：血条读数不靠全局坐标，靠 D1 检出的底板 bbox 内缩。

```
padding_x = bbox_w * 0.04      # 比例
padding_y = bbox_h * 0.15
```

L1 负责给出「大致位置」，L2 负责「在这个大致位置附近框到东西」，
L3 负责「框到之后精确取子区」。三级都有，任何一级失效还有下一级。

---

## 三、三个必须注意的坑

### 坑 1 · `ref_scale` 必须写 2.0

`content_scale(meta)` = `当前系统缩放 / meta.ref_scale`。
我们的 ROI 是按**物理像素**量的，也就是在 DPI 200% 下标定的。
若 `ref_scale` 写 1.0，会算出系数 2.0，把所有尺寸翻倍。

```
正确：  {"ref_scale": 2.0}   → content_scale = 1.0   （当前环境）
错误：  {"ref_scale": 1.0}   → content_scale = 2.0   （全乱）
```

### 坑 2 · `resolve_region` 返回的是**绝对**坐标

```
输入 [166,170,244,20]  →  输出 [646, 211, 244, 20]
```

它已经把 `win[0], win[1]` 加进去了。RocoAgent 的 `grab()` 是**窗口相对**的，
所以要么用返回的绝对坐标直接喂 `mss`（不要再用 `grab()`），
要么自己减掉 `win[0], win[1]`。**别双重相加。**

### 坑 3 · 现有 3 个 ABS 常量直接删掉

```
LOBBY_BTN_ABS   (2648, 1605)
RESULT_BTN_ABS  (1736, 1571)
ADVANCE_ABS     (1733, 1279)
```

改成 `resolve_region` 出来的值，或直接用 D3 检出的按钮 bbox 中心。

---

## 四、检测器规格（不变，仍是精度的根）

每个检测器返回 `(bbox, confidence)`，失败必须明确返回失败，不能返回垃圾坐标。

### D1 · 血条底板（我方 / 敌方）

```
判据    长条矩形，宽高比 8:1 ~ 14:1
        内部高饱和彩色像素（绿/黄/红/蓝）占比 > 30%
        在窗口上 30% 区域
        我方中心 x < 35% 窗宽；敌方中心 x > 65% 窗宽
        两者 y 中心差 < 8px
```

### D2 · 技能栏（复用现有 `_is_skillbar` / `find_pills` / `_slots`，补 bbox 输出）

### D3 · 底部按钮（复用现有 `find_action_button`，已验证）

```
确认首发 (1736,1587)  退出 (1736,1571)  加载界面 None   ← 三个用例已过
```

### 几何自检（定位完必须过）

```
D1 两血条 y 差 < 8px
D2 技能栏 y > D1 y
D3 按钮 y > 0.8 * 窗高
任一不满足 → 判定位失败
```

### 三级回退

```
① 检测成功     → 用实测 bbox
② 检测失败 3 次 → winrel 线性缩放，告警 SCALED
③ 仍不通       → 旧常量，告警 LEGACY，暂停动作
```

---

## 五、分步执行（每步验收，不许跳）

### Step 0 · 独立探测器（不改 robot.py）

新写 `D:\Tools\probe_layout.py`：

```
前置    import _mode4_shared，用 get_game_window 拿 rect（不再自己 EnumWindows）
输出    每个候选项：类型、bbox、置信度、判据明细
--save  存一张画框的 png
范围    D1 血条 ×2、D2 技能栏、D3 按钮
```

**验收**（当前 2586×1671）：

```
D1 我方 ≈ (166,170,244,20)      误差 ≤ 3px
D1 敌方 ≈ (2251,170,245,20)     误差 ≤ 3px
D3 按钮中心 ≈ (1736,1571)        误差 ≤ 5px
各跑 10 次，给成功率
```

### Step 1 · 分辨率扫描（需要用户在电脑前）

用户手动切分辨率，每档跑 Step 0 并记录。**同时算出两张表**：

```
表 A  winrel 线性缩放的预测值 vs 实测值，偏差多少
表 B  D1 检出 bbox 与线性缩放预测的偏差
```

**这两张表决定 L1 够不够用**：偏差普遍 <4px → L1 够，L3 做保险；
偏差 >10px → 必须靠 L3。

### Step 2 · `LayoutMap` 类

```python
class LayoutMap:
    def locate(self) -> bool:      # D1/D2/D3 + 几何自检
    def bar_box(self, which):      # 'my' / 'foe'，返回窗口相对或绝对，明确写清
    def skill_box(self): ...
    def button_center(self): ...
    def stale(self, rect): ...     # 尺寸变了？
```

### Step 3 · 接入 robot.py（只动感知层）

```
改     MY_BAR / FOE_BAR / SKILL_BOX / CNT_ROI 的取用 → 走 LayoutMap
改     _read_bar 的 rect → D1 bbox 内缩（按比例）
改     3 个 ABS → resolve_region 或 D3 结果
加     发键前 guard_foreground()，False 则跳过本回合
不改   决策层、执行层、换人逻辑
```

**回归必须全绿**：

```
血条   100% / 39.1% / 23% / 7.4% 四帧，误差 ≤ 1%
技能栏  4 张卡槽位与改造前一致
按钮   首发 / 退出 / 加载界面-None 三用例
```

### Step 4 · 实战 A/B（同分辨率，各 15 分钟）

```
感知失败率   现状 35~40%  →  目标 < 10%
FOCUS LOST   现状 48 次    →  目标 0 次「发键时失焦」
```

### Step 5 · 不停机切分辨率

```
期望日志   LAYOUT RELOCATED 2586x1671 -> 1920x1080
不出现     连续 3 回合 UNKNOWN
```

---

## 六、需要用户配合

```
Step 1 五档分辨率     用户手动切，bot 暂停
Step 5 切换实测       同上
用户在用电脑时        禁止启动 bot（抢焦点 → 按键全丢）
```

---

## 七、风险与回滚

```
风险 1  检测器被战斗特效干扰 → 几何自检 + 连续 3 帧一致才采纳
风险 2  定位拖慢回合（15 秒预算） → 只在启动/尺寸变化/连续失败时定位
风险 3  改造比现状更差 → 保留 --legacy-layout 开关，一条命令回滚
风险 4  跨项目 import 耦合 → 只 import 纯函数（get_game_window /
        resolve_region / check_occluded / content_scale），
        不 import 会起 BLE / GUI 的东西
```

---

## 八、硬约束

```
① 游戏内操作只能用 ESP32 板，绝不能用软件模拟输入（ACE 反作弊）
② 回合内不调用大模型，全本地（15 秒/回合）
③ 伤害数字不自己算，读游戏显示的数字
④ 换人 = 数字键 → 等 1 秒 → 空格；死的精灵是灰色、点不到
⑤ 用户在用电脑时不启动 bot
```

---

## 九、文件

```
D:\Projects\RocoAgent\src\robot.py                    主程序（Step 3 才动）
D:\Projects\RocoAgent\TASK_LAYOUT.md                  本文件
D:\Projects\RocoAgent\NEXT.md                         上一轮换人补丁待验证
D:\Projects\RocoAgent\logs\HANDOFF.md                 完整背景
D:\Tools\probe_layout.py                              Step 0 新增

C:\Users\Lenovo\Desktop\hardwork\CV_ESP_Game_1\src\script\_mode4_shared.py   复用源
C:\Users\Lenovo\Desktop\hardwork\CV_ESP_Game_1\docs\cv_training_guide.md     YOLO 归一化说明
```
