# NEXT.md — 本轮改动记录（2026-11-18 15:xx）

## 结论先行

**0 动作/局的真因不是感知，是「卡在力竭换人状态出不来」。**
`bar=False` 只是症状：pet 在 0 HP 卡死 → 技能栏本来就不该出现 → 观测永远 bar=False。

日志证据（14:24–14:51，27 分钟里 `me=0.0` 不变）：

```
14:25:11 FAINT -> switch slot 4 (... attempt 0)
14:25:16 FAINT -> switch slot 3 (... attempt 0)   ← attempt 永远是 0
14:26:xx FOCUS LOST - key 3 dropped (fg=1705966 game=3476384)   ← 连发 20 次
```

## 改了什么（一次一个，都已 py_compile）

### 1. 力竭状态不再被反复重置（根因）
`_battle_tick` 原来要求 `st["my_ui"]` 才进力竭分支。**换人面板打开时血条不画**，
所以每隔一帧就掉出分支 → 跑到下面的重置代码 → `switch_attempt=0` /
`switch_dead.clear()` 全被抹掉 → 下一帧又从 slot 3 开始试。

改成 **sticky**：一旦进入力竭，只要 `faint_at` 还在就不再依赖 `my_ui`；
只有看到 `my_pct > 5%`（精灵真的活了）才重置计数器，并打一行
`FAINT -> recovered`。

### 2. 盲点上限不再自己复活
`advance_count >= 12` 之后有 `if now - advance_at < 60: return; advance_count = 0`
——**每分钟复位一次**。结果 15:46 它在未识别画面上每分钟再点 12 次，
把角色**点出了 PvP 竞技场、走进了开放世界**（之后所有检测器都匹配不上，再也回不来）。

改成硬上限 8 次，永不复位；只在「新对局开始」或「点了确认按钮」时清零。
超限只打一条 `advance cap reached ... stopping blind clicks`。

### 3. STUCK 看门狗不再点地图
原来卡住时 `click(ADVANCE_ABS)` + `click(RESULT_BTN_ABS)`，
在开放世界里等于让角色乱走。改成**按 ESC**（任何界面都安全），并且不再
顺手把 `advance_count` 清零。

### 4. `_click_to_focus` 先置顶再点
原来直接点窗口内 `(700,300)`。**编辑器盖住游戏时，这个点击落在编辑器上**，
于是"抢焦点"反而把焦点给了编辑器 —— 所以 FOCUS LOST 后面永远跟着 FOCUS LOST。
新增 `_raise_topmost()`：`SetWindowPos(TOPMOST)` → `SetForegroundWindow` →
`NOTOPMOST`，`focus()` 和 `_click_to_focus()` 都先走它。

### 5. 决策门的稳定性死锁（新发现）
```python
if key != self.prev_key:
    self.prev_key = key
    self.stable_since = now
    if self.acted_at == 0.0: return     # ← 没有超时
```
技能槽位在 5-6 个胶囊之间抖动时，`key` **每一帧都变**，
"连续两帧相同"这个条件永远不成立 → 整局 0 动作（15:45 那局就是这样）。

改成：不稳定就先等，**最多等 3 秒后强行用当前帧出招**。
另外把"新回合"的判定从"技能栏消失 1.5s"补上"数字变了且距上次出招 > 2.5s"，
否则 8 秒重试会连下一回合一起堵住。

### 6. 决策门现在会自己解释
新增 `_gate(now, reason)`，每秒最多一条 `GATE ...`，直接写出是哪个条件在拦
（`read not stable` / `no readable damage digit` / `press sent, waiting ...`）。
以后不用再从"只有心跳行"去猜。

### 7. 复盘一定落盘
`review_turns.py` 在窗口内没数据时只 `print` 不写文件，日志却打了
`review queued -> xxx.md`。改成**没数据也写一个 stub 文件**，说明"那局一次决策都没做"。

## 当前阻塞（下次接手第一步）

**游戏现在停在开放世界（闪耀大赛入口广场），不在竞技场里。**
bot 无法自己回去：开放世界里点击 = 走位，没有任何 UI 检测器认识那个界面。
需要人工把角色带回竞技场（或站到「闪耀大赛」导师旁的传送点），
botsup 一启动就会接管。

导航路径（人工 10 秒）：`ESC` → 菜单左侧 **S5赛季**（窗口相对 580,435）
→ 面板左下 **闪耀大赛 前往** → 再点进去的 **前往**（相对 2221,360）
→ 等载入 → 站在导师旁。

## 下次要做的（按优先级）

1. **进竞技场后先别改代码**，直接跑 10 局，看 `GATE` 行 + `rounds.jsonl`
   的动作数，验证改动 1/2/5 是否真的把动作数拉起来。
2. 如果动作数还是低，看 `GATE` 行落在哪一类，再针对性改。
3. 加一个「不在 PvP」的检测（识别开放世界/竞技场入口），
   并且**只允许在识别出的界面上点击**，禁止盲点。这是防止再走丢的根本办法。
4. 换人补丁（`switch_pending` / `_pick_switch_slot` / `_is_dash`）在动作数
   拉到 ≥10/局 之后再验，现在这种噪声下测不出来。

## 本轮没动的东西

- `TASK_LAYOUT.md` 的 Step 1 结论（需要用户手动切分辨率）
- 决策层 / 执行层 / 换人选择逻辑
- 检测器的 ROI 常量（等 Step 3 才改）
