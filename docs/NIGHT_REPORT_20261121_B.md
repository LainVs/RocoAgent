# 2026-11-21 修复记录（第二轮）

## 一、战绩记录终于修好了（用户报"没有成功记录输赢"）

### 现象

`logs/results.jsonl` 全部是 `flat`，110 条没有一条 win/loss。
但 `bot_stdout.log` 明明读到了真实战绩。

### 根因

`record.load_state()` 返回 **dict**：

```python
return json.load(f)     # {"wins":113,"losses":106,"at":"..."}
```

但 `record.diff()` 用**整数下标**：

```python
if after[0] > before[0] ...    # KeyError: 0
```

每次 diff 都抛 KeyError，被 `robot.py` 的 `except Exception` 吞掉。

**铁证**：`logs/record_state.json` 停在 `2026-11-19 20:06:41`，
之后 5 天没更新过 —— 因为 `save_state` 只在 diff 成功后才调用。

### 修复

`load_state()` 统一返回 tuple，`diff()` 增加 dict 兼容。

### 验证（实盘）

```
09:21:25 record 126W/155L prev=(125, 155) -> win
09:21:25 RESULT win (126W/155L)
```

**第一次成功记录胜负。**

---

## 二、--stop 停不掉（用户报"不会是你之前写的一直拉起吧"）

### 用户猜对了

是我 06:05 加的 `keepalive.py` 在反复拉起。

### 证据

```
09:12:41 botsup 不在，已重新拉起
09:25:48 botsup 不在，已重新拉起
```

### 根因

`run_bot.py --stop` 只停 robot 和 botsup，**不知道 keepalive 的存在**：

```
--stop -> robot 停了、botsup 停了
-> keepalive 还活着 -> 60 秒后拉起 botsup -> 拉起 robot
```

### 修复

按依赖顺序停：`keepalive -> botsup -> robot`。
顺序不能反，否则 keepalive 会在停止过程中又拉起。

---

## 三、点得太快，结算页一闪就进下一局

### 根因

`_idle_tick` 判定"在大厅"就**立刻**点开始挑战，没有任何等待。

### 修复

新增 `LOBBY_HOLD_S = 6.0`，第一次确认在大厅时记时，等满 6 秒才点。
进入战斗时清空计时。

---

## 四、cost 恒为 0（用户报"选低伤害技能"）

### 根因（两个 bug 叠加）

**bug 1**：`robot._decide_v2` 里

```python
meta = E.D.skill(s.get("name") or "")    # name 恒为 None
cost = int(meta.get("energy_consumption") or 0)
```

机器人**不读技能名**（`turns.jsonl` 只有 `c/i/lit/plate/t/v`），
所以 `E.D.skill(None)` 返回 None，**cost 恒为 0**。

初版 `policy_legacy` 用 `my_card_costs()` **查表**，一直是对的。

**bug 2**：我上一轮把 `ENERGY_SCALE` 从 10 改成 2，导致
`energy_after` 全被 clamp 到 1.0，能耗差异完全消失。

### 修复

1. `cost` 改回查表 `slot_costs.get(s["index"], 0)`
2. `energy` 改用"点亮卡的最大耗能"作下界
   （卡面 `lit` 是权威的，实测与 `plate>220` 完全一致，0 处矛盾）
3. `ENERGY_SCALE` 改回 10
4. 加退化检测（cost 全为 0 时告警）

---

## 五、重要数据结论

### 策略选招是对的

扫描 673 条可比较的决策：

```
选最高伤害   661 条 (98.2%)
选非最高      12 条 (1.8%)   <- 全是旧版日志，差距最大 10
差距 100+     0 条
```

**从来没有"放了伤害高 100 以上的卡不打"的情况。**

### 真正的问题是队伍配置过期

用户报的两个场景（丢冰块/主轴、筛管奔流/扬沙）都指向同一件事：

```
配置里卡瓦重：升龙咆哮 / 冰雹 / 顶端优势 / 取念
用户说实际：  筛管奔流 / 扬沙 / ...
配置里有冰钻布鲁斯吗：不在
```

`my_card_costs()` 按过期配置查耗能：

```
配置 slot2 = 冰雹 cost=4
实际 slot2 = 扬沙 cost=1     <- 被当成 4，惩罚重 4 倍
```

策略以为"扬沙很贵"，所以选了主轴。**这是数据错误，不是策略错误。**

实盘还出现过不在配置里的精灵：`护主犬#2`、`绒光优优`。

### color 不影响决策

用户猜"主轴颜色绿色被识别成增益"——查证 `policy_v2.py` 里
`color` 出现 **0 次**，完全不参与决策。`color` 只用于日志和
旧版的 `!= "red"` 平票比较。

---

## 待办

1. **等用户提供真实队伍配置**（6 只精灵 + 各自 4 个技能）
2. 更新 `my_team` 表
3. 加"配置 vs 实盘读数"自动校验
4. BLE 板子重连后继续跑

## 新增工具

| 脚本 | 用途 |
|---|---|
| `scripts/stop_all.py` | 按命令行精确停止三件套（不按名字批量杀） |
| `scripts/team_audit.py` | 对照队伍配置与实盘读数 |
| `scripts/check_pick_vs_max.py` | 统计"选了非最高伤害"的比例 |
| `scripts/compare_policy_versions.py` | v1/v2 策略对比 |
| `scripts/diag_user_skills.py` | 诊断指定精灵的技能与读数 |
| `src/test_record.py` | 战绩记录回归测试（24 项） |
| `src/test_stop.py` | 停止流程测试（10 项） |
| `src/test_policy_cost.py` | 耗能参与决策的测试（11 项） |
