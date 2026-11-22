# 计划：修 BAND 三处缺陷（2026-11-23 05:xx）

## 背景

`BAND`（卡面技能名直读）是"技能识别链路"的最后一环。现状：

| 时间 | 现象 |
|---|---|
| 20:45 / 20:53 | `BAND -> ... 未识别 (conf 0.00)` |
| 01:56 | `conf 0.79`（首次读到字，但没过 0.90 门槛）|
| 04:42 起（当前进程）| **一条 BAND 日志都没有**，连 `scan failed` 都没有 |

上一轮已把候选词改成"本宠技能池"+分档门槛（0.80），但**本轮 0 条日志**
说明代码根本没走到打日志那一行 —— 不是阈值问题，是更上游的缺陷。

## 缺陷 1（致命）：`skill_pill_boxes` 把角点当宽高

`config.SKILL_BOX = (250, 480, 780, 1520)` 是 **角点** `(x0,y0,x1,y1)`，
而 `winio.grab_client` 的契约是 `(x, y, w, h)`。

```python
img = screen.grab_client(_C.SKILL_BOX)   # 传成了 780x1520
```

正确应为 `(250, 480, 530, 1040)`。同一个文件里 `observe()` 是对的：

```python
x0, y0, x1, y1 = SKILL_BOX
cw, ch = x1 - x0, y1 - y0          # 530, 1040
crop = self._grab(frame, scale, (x0, y0, cw, ch))
```

**实测证据**（实机 4:3，client=2048x1536，scale=(0.8, 0.96)）：

```
正确 box -> 物理矩形 (200, 461, 424, 998)   y 到 1459，界内
错误 box -> 物理矩形 (200, 461, 624, 1459)  y 到 1920，**越界 384px**
```

抓取时越界部分被裁掉，实得 1075px 高，而 `_fit()` 发现
`1075/0.96 = 1119 ≠ 1520`（调用方约定的 h），于是**整体拉伸到 1520**
—— 纵向 ×1.36、横向 ×1.0。技能栏被拉长 36%，`find_pills` 的
`22<=h<=52` 约束和名字带 ROI 全部失调。

正确裁法的实得尺寸刚好吻合约定（`998/0.96 = 1040 = h`），
`_fit` 只做等比缩放，不产生形变。

**补充实测**：垃圾 box `(250,1401,334,1442)` 并非来自多裁区域，
换人面板本身就会被 `find_pills` 误检（`unread_nodraw_*` 帧上仍是 2 个）。
它靠 `_maybe_band_scan` 的 `st["bar"]` 前置条件挡住 —— 所以这里
**不是**修复重点，真正的伤害是上面那 36% 的纵向拉伸。

## 缺陷 2：槽位划分用硬编码 180px 间距

```python
k = int(round((cy - pills[0]["box"][1]) / 180.0))   # 自己算格子
```

而 `_slots()` 已经用 **lattice fit（pitch 150~215 轮询）+ `_fill_missing`**
算好了槽位，每项都带 `box`（实测 `slot1=(133,495,180,520)`）。

代码注释写着"`_slots` 返回的槽位 dict 没有 box" —— 这条注释是错的，
实测有。结果：白扔了更准的拟合结果，还用 180 硬编码把槽位排错。

## 缺陷 3：外卖接口比数量，会静默死锁 + 索引错位

```python
boxes = _P.skill_pill_boxes(self.screen)
if len(boxes) < len(pending):
    return          # 不打日志，也不标记已扫
```

两个问题：

1. **数量 ≠ 槽位身份**：pending 是 `{2,4}` 时，boxes 有 2 个（对应槽 1、3）
   也会通过，然后拿 slot2 的索引去读 slot1 的 box。
2. **静默**：卡面没画全时永远 return，不留任何日志 —— 这正是"0 条 BAND
   日志"的直接原因，无法诊断。

另外 `_band_scan_at` 只在换精灵时清零，**每只精灵每次上场只给一次机会**，
一次没读全就整场放弃。

## 缺陷 4（真正的 0 日志根因）：`pet_at` 被每 0.5 秒无条件刷新

上面三条修完后实测**仍然 0 条 BAND 日志**，说明还有更上游的阻塞。
逐条核对 `_maybe_band_scan` 的前置条件后发现是这一行：

```python
# _check_active_pet，每 0.5s 跑一次（_pet_poll_at 节流）
self.active_pet = _nm
self.pet_at = now          # ← 无条件赋值
```

而 BAND 的门槛正是 `if now - getattr(self, "pet_at", 0.0) < 1.2: return`。
`pet_at` 每 0.5s 被刷成当前时间，`now - pet_at` **最大只有 0.5s**，
永远小于 1.2 —— 函数在 try 块之前就 return，连一行日志都不会留下。

`pet_at` 的语义应该是"这只精灵**上场**的时刻"，只在身份**变化**时更新。

**同一缺陷的第二个受害者**（`robot.py:3465`，首领变身）：

```python
if now - getattr(self, "pet_at", 0.0) < 1.2:
    return False
```

首领化（恶魔狼变身）也被这条永久挡住 —— 变身不占回合，一直没触发过。

三处 `pet_at = now`（ONNX 主通道 1880、avatar 次通道 1931、旧模板 1994）
中，第三处本来就在 `if name != self.active_pet:` 里，是正确的；
前两处无条件赋值。修法统一为"身份变化才更新"，并顺带重置
`_band_scan_at/_band_tries`。

修完后 BAND 日志**第一次出现**（05:38）：

```
BAND -> 恶魔狼 槽1 未识别 (conf 0.79)
BAND -> 恶魔狼 槽2 未识别 (conf 0.79)
```

`pet_age` 也能正常增长了（诊断日志显示 16.5s / 26.5s / 57.9s），
证明语义修正生效。但 conf 仍差 0.01，继续往下挖。

## 缺陷 5：ROI 把胶囊本身圈了进去，OCR 先读出伤害数字

加上 raw 回传后，日志给出了决定性证据：

```
05:52:53 BAND -> 音速犬 槽1 未识别 raw='5z'          vocab=44
05:52:59 BAND -> 音速犬 槽2 未识别 raw='OSE 恶魔..'  vocab=44
05:53:04 BAND -> 音速犬 槽3 未识别 raw='160'         vocab=44
```

`5z`、`160` 是**胶囊里的伤害数字**，不是技能名。代码里注释写的是
"名字在**胶囊下缘** +5..+95"，实际写的是：

```python
roi = img[fy0 + 5 : fy1 + 95, ...]   # fy0 = 胶囊**上缘**
```

从胶囊上缘 +5 开始，把整颗胶囊连同里面的伤害数字一起圈进 ROI。

真帧对比（`logs/det2/now2_full.png`，4 个胶囊全画出）：

| ROI 起点 | OCR 原文 | 匹配 |
|---|---|---|
| 上缘（旧） | `泡沫 \| 14 影袭 \| 能量 \| 快速移` | 槽2 混入 `14` |
| 下缘（新） | `泡沫 \| 影袭 \| 能量 \| 快速移` | **4/4 全对 1.00** |

另一组真帧（`bar_partial_204511.png`）更直接：

```
上缘(旧): raw='251 跌落'  -> 跌落 0.79   ← 数字把 conf 拖低
下缘(新): raw='跌落'      -> 跌落 1.00
```

**这就是 conf 卡在 0.79 的物理原因** —— 与上一轮把门槛从 0.90 降到 0.80
无关，问题从来不在阈值。

修法：`roi = img[fy1 + 3 : fy1 + 65, ...]`（注释本意），下界从 95 收到 65
（实测 65 已覆盖名字全高，再往下会带进下一张卡的元素）。

## 缺陷 6（致命死锁）：`read_band` 在 `_LOCK` 内调 `confirm`

```python
with _LOCK:
    confirm(pet, slot, name, src="band")   # confirm 内部又 `with _LOCK:`
```

`_LOCK` 是 `threading.Lock()`（**不可重入**），于是永久自锁。
faulthandler 实测抓到：

```
File "src/session_moves.py", line 288 in read_band
File "src/session_moves.py", line 155 in confirm    <- 卡在这里
```

后果：`read_band` 一旦 OCR 匹配成功就挂死，所以
**`data/session_moves.json` 至今不存在**（从未成功采纳过一次），
并且可能拖死感知线程 —— 这是"机器人卡住/进程消失"的一个嫌疑。

修法：去掉外层 `with _LOCK:`，直接调 `confirm`（它自己加锁）。
修后实测读到 `力量增效 1.00`、`跌落 1.00`，`session_moves.json`
**首次成功生成**。

## 修法

### perceive.skill_pill_boxes

- 传 `(x0, y0, x1-x0, y1-y0)` 给 `grab_client`
- 直接复用 `_slots()` 的 `box`（lattice fit + fill），不再自算 180 间距
- 返回长度固定 4 的列表，缺槽位填 `None`；一个都没检出返回 `[]`

### robot._maybe_band_scan

- 按**槽位身份**过滤可用 box，而不是比数量
- 缺槽位/卡面未画出时打**限流日志**（10s 一次），可诊断
- 每次上场给 **3 次**尝试，间隔 ≥1.5s（一次读不全还能补）
- 换精灵、开新局时把尝试计数一起清零

### test_skill_pill_boxes

现有测试对缺陷 1 **无感**（旧版裁图起点相同，胶囊照样落在框内，所以断言都过）。
补一条**诱饵回归**：在旧版会多裁出来的区域（x=850, y=700）贴一个假胶囊，
正确实现必须无视它。

## 验收

1. `test_skill_pill_boxes` / `test_session_moves` / `test_imports` 全 PASS
2. 实机进战斗后出现 `BAND -> <我方> 槽N = <技能名> (conf >= 0.80)`
3. `data/session_moves.json` 首次生成