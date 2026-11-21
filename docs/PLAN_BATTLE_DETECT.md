# battle 判定过严导致血量数据被丢弃

**版本**：`ui-m78`
**日期**：2026-11-21
**前置**：`ui-m77`

---

## 一、实测数据（14 张历史战斗帧）

这些帧**都是真实战斗画面**（文件名带 `T7_energy` / `T8_skill2` 等回合标记，
之前机器人打排位时截的）。

```
读到我方血量   5 (36%)
读到对面血量   4 (29%)
两边都读到     3 (21%)   <- battle 的必要条件
我方白框       0 ( 0%)   <- 断了
对面白框       1 ( 7%)   <- 断了
技能栏可见     0 ( 0%)   <- 断了
battle=True    0 ( 0%)   <- 全灭
```

**真实战斗帧，`battle` 一个都没判对。**

## 二、判定逻辑（`perceive.py` 第 310-370 行）

```python
bars   = my["pct"] is not None and foe["pct"] is not None
battle = bool(bars and my["ui"] and foe["ui"])   # 主判据
...
if bars and not battle and visible:              # 兜底判据
    battle = True
```

两个条件都依赖 `my_ui` / `foe_ui` / `visible`，实测命中率 0~7%。

## 三、为什么白框检测全灭

`_bar_from()` 里的 `ui` 是"血条周围有没有白色描边"。
这依赖：

- 分辨率精确匹配（模板是按某一档截的）
- 血条没有被技能卡 / buff 图标遮挡
- 游戏 UI 主题没变过

实测本机 2048×1536 客户端，缩放到基准 2560×1600 后
白色描边的像素位置和模板对不上 —— 1px 偏移就检测不到。

## 四、决策：**不修改 `perceive.py` 的判定**

理由：

1. **`perceive.py` 是机器人主链路**（`Perc.observe()` 每帧都在跑），
   改判定风险高 —— 误判成"战斗中"会让机器人乱出招。
2. 白框检测失效是**分辨率/主题适配**问题，属于感知层深层缺陷，
   需要专门的定标工作，不该顺手改。
3. 本次的目标是"让独立计算器能显示数据"，不是"修好机器人的战斗判定"。

## 五、改在 `standalone.py`：独立模式用自己的判据

独立计算器不需要"精确判断是否在战斗"——它只需要
**"有没有可用的血量数据"**。这两件事不一样：

| 场景 | 机器人需要 | 计算器需要 |
|---|---|---|
| 血量读到但白框没检测到 | 不确定，可能误出招 -> 保守 | **有数据就显示** |
| 血量都没读到 | 待机 | 显示"没有对战画面" |

所以 `standalone.observe()` 里改 `detectors.bar` 的判据：

```python
# 原来：两边血量都读到才算
"bar": bool(raw.get("my_pct") is not None
            and raw.get("foe_pct") is not None),

# 改成：任一边读到就算（计算器只需要"有数据"）
"bar": bool(raw.get("my_pct") is not None
            or raw.get("foe_pct") is not None),
```

这样：
- `my_pct=92.6` / `foe_pct=None` -> `bar=True` -> 我方血量正常显示
- 两边都 None -> `bar=False` -> 空态提示

## 六、同时修 `mode`

`mode` 现在用 `raw.get("battle")`，实测恒为 False -> 永远显示「待机」。

改成用同一个判据：有血量数据就算 `battle`。

## 七、实施

1. `src/standalone.py`
   - `detectors.bar` 判据改成"任一边读到"
   - `mode` 跟着改
2. `src/test_standalone.py` 加用例：
   - 只有我方血量 -> bar=True、mode=battle
   - 只有对面血量 -> bar=True、mode=battle
   - 都没有 -> bar=False、mode=idle
3. 用历史帧离线验证（不碰游戏战绩）

## 八、不做

- **不改 `perceive.py`**（机器人主链路，风险高，且需要专门定标）
- **不修白框检测**（那是分辨率适配问题，独立任务）
- **不假装"我方血量一定读得到"**（读不到时仍显示 `—`）

---

## 九、实施结果（2026-11-21 22:15）

### 9.1 离线验证（14 张真实战斗帧）

| 指标 | 改前 | 改后 |
|---|---|---|
| `mode=battle` | 0 (0%) | **6 (43%)** |
| `detectors.bar` | 0 (0%) | **6 (43%)** |
| 非空帧 | 0 (0%) | **6 (43%)** |

其中 2 帧两边血量都读到。

### 9.2 单帧端到端（`235623_T1_skill3.jpg`）

```
mode       battle
me.hp_pct  92.6
foe.hp_pct 80.0
detectors  {'bar': True, 'skillbar': False, 'countdown': False}
matchup    None      <- 身份没识别，所以正确地不瞎算
```

最后一行是**正确行为**：离线帧跳过了 OCR，`me.name` / `foe.name`
都是 None，`_matchup([], [])` 按设计返回 None。
克制关系需要属性，属性需要身份 —— 链路是对的。

### 9.3 仍未解决

`skillbar` / `countdown` 检测器仍 0% 命中（白框/技能栏检测失效）。
它们只影响：
- 技能卡伤害数字（`skills` 全 null）
- 倒计时显示

不影响血条、身份、克制关系。

**下一步**：白框与技能栏的分辨率适配需要专门定标（对照多档分辨率
重新量模板位置），是独立任务。

