# 接下来要做的（2026-11-18 上午）

> 上一轮（凌晨 03:00–09:40）已经把系统跑通了，详见 `logs/HANDOFF.md`。
> 这一页只讲**当前状态**和**还没做完的事**。

---

## 一、刚刚动过的代码（未验证）

`src/robot.py` 改了三处，**语法通过，但一次都没跑过**：

### 1. 强制换人重写（`_try_switch` + 新增 `_living_rows`）

用户提供了关键情报：

```
换人按键顺序   数字键 → 等 1 秒 → 空格
               （之前失败就是因为没等，0.3 秒就按空格）
死掉的精灵     还在列表里，但是灰色的，点它没用
               （之前的代码盲点第 1 行，正好点到尸体上）
```

新实现：
- `_living_rows()` 采样每张卡片的颜色散度（max-min），`>= 25` 判定为存活
- 跳过灰色行，取第一只活的
- 发数字键 → `time.sleep(1.0)` → 发空格

### 2. 主动换人（`decide()` 新增分支）

```python
SWITCH_DMG = 40        # 最高伤害低于这个值就考虑换人

if slot and ctx.get("switch_streak", 99) >= 3 and best["value"] < SWITCH_DMG:
    return ("switch", slot), "all skills weak ..."
```

冷却是 3 回合，避免连续换人。

### 3. 执行器支持 switch

```python
elif action[0] == "switch":
    self.send_key(action[1])
    time.sleep(1.0)
    self.send_key("space")
    self.switch_count += 1
    self.last_switch_turn = self.turn
```

`next_switch_slot()` 按 `[2,3,4,5,6]` 轮转——**因为战斗中看不到队伍列表，不知道哪只活着**，只能轮着试。

### ⚠️ 这三处一定要先验证再继续改别的

---

## 二、验证清单

```powershell
cd D:\Tools
C:\Users\Lenovo\Desktop\hardwork\CV_ESP_Game_1\.venv\Scripts\python.exe `
  -W ignore -u botsup.py --seconds 86400
```

跑起来后看 `D:\Projects\RocoAgent\logs\bot_stdout.log`，确认三件事：

| 检查点 | 期望日志 |
|---|---|
| 力竭换人 | `FAINT -> switch slot N (spread ...)` 后面接 `T? SKILL ...` |
| 主动换人 | `T? SWITCH N (all skills weak ...)` |
| 换人冷却 | 换人后 3 回合内不再出现 SWITCH |

**如果换人还是失败**，说明 `spread >= 25` 的阈值不对，需要：
1. 抓一张换人面板的帧
2. 打印每张卡片的 spread 值
3. 按实际分布调阈值（存活/灰色应该有明显分界）

---

## 三、还没解决的问题（按优先级）

### P0 · 感知失败率 35~40%

```
现象   best affordable dmg 40 (None)   ← 数字读到了但颜色判定失败
       整行 --                        ← 完全没读到
代价   读不到就空过一整个 15 秒回合
```

数字模板已经做好了（`D:\Tools\digitread.py`，排序准确率 90~95%），但**颜色分类**（绿/红/白）不稳。

建议：统计「有数字但颜色不达标」的帧，和「真的没数字」的帧分开看，别凭感觉调阈值。

### P1 · 策略层基本空白

```
属性克制表      没接。现在选技能只看「伤害数字最大」，不看属性
首领化          从来没用过（W → 1 → 空格）
对面换人        没有反应，只是继续打
对面防御        无法预判（双方同时选行动，本来也做不到）
```

属性克制表在 `D:\GameData\洛克王国世界\roco-world-skill\.claude\skills\roco-world\SKILL.md` 里，是一段 JS 对象。

### P2 · 技能播报没用起来

```
已实现   D:\Tools\announce.py，字符准确率 98%
未接入   robot.py 里没有调用它
用途     知道对面用了什么技能 → 推断对面的精灵和打法
```

### P3 · 强制换人仍然只能靠止损

即使换了新的按键方式，也可能有别的边界情况，保留「试一次就等自动选人」的兜底。

---

## 四、代码位置

```
D:\Projects\RocoAgent\src\robot.py      主程序（刚改过）
D:\Projects\RocoAgent\src\capture.py    截图
D:\Projects\RocoAgent\logs\HANDOFF.md   完整背景（15 KB，先读这个）
D:\Projects\RocoAgent\logs\bot_stdout.log   运行日志
D:\Projects\RocoAgent\logs\rounds.jsonl     每局结果
D:\Projects\RocoAgent\logs\turns.jsonl      每回合决策

D:\Tools\ble.py         直连蓝牙发指令
D:\Tools\bleclick.py    移动+点击（--hold 按 Alt）
D:\Tools\digitread.py   技能伤害数字识别
D:\Tools\announce.py    技能播报识别
D:\Tools\hpbar.py       血条像素读数
D:\Tools\review.py      对局复盘
D:\Tools\botsup.py      看门狗
D:\Tools\rec3.py        抓帧
```

数据：`D:\GameData\洛克王国世界\`（700+ 精灵、技能、官方解包）

---

## 五、昨晚的成绩（供参考）

```
03:30   0 次动作/局      bar 检测传参错误
04:00   4 次/85 秒
05:00  24 次/580 秒
06:00  26 次/400 秒      最高纪录
09:00  13 次/234 秒

战果   地绡银龙 1 打 4 横扫，段位 见习决斗家 I → IV
```

**结论：核心链路是通的，卡在感知和换人两个点上。**
