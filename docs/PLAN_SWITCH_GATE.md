# 计划修订：放弃"20s 新鲜度门禁"，改用"换人未确认"门禁

## 为什么原方案错

实测我方身份事件统计：

```
成功 40 / 失败(unknown bust) 22   失败占比 35%
成功识别之间的间隔：中位 103s，均值 262s，最大 2804s
>20s 的间隔占 90%
```

**ONNX 对战中的识别是稀疏事件**（每帧都带入场动画，多数帧读不出）。
20 秒门禁会拦掉 90% 的 BAND 机会 —— 这会把一个已打通的功能重新废掉。

## 真正的判据不是"时间"，是"换人信号"

09:46 那次的关键不是"过了一段时间"，而是**场上精灵换过了**。而换人
有明确的信号，不需要靠时间猜：

1. **`FORCED SWITCH -> panel is up`**（09:48:58）—— 强信号
2. **`FAINT -> switch slot N`**（死亡换人）
3. **`v2 wanted SWITCH` / `SWITCH -> click card`**（主动换人）
4. **`T<n> SWITCH`** 回合日志

这些事件发生时，立刻把 `_ident_confirmed = False`，直到下一次**成功**
身份识别才恢复。

## 修法

1. `_check_active_pet` 成功采纳身份时置 `self._ident_confirmed = True`
   并记 `self._ident_at = now`
2. 任何换人信号（上表 1~4）置 `self._ident_confirmed = False`
3. `_maybe_band_scan` 加前置：`if not self._ident_confirmed: return`
   （限流日志 `BAND skip: identity unconfirmed after switch`）
4. **首次进场**（新对局 START）也置 False，等第一帧确认

## 这样为什么不会误伤

正常情况下：换人 -> 身份未确认 -> 跳过 BAND -> 一两帧内 ONNX 认出
-> 确认 -> BAND 恢复。代价只有换人后那 1~3 秒，而这正是
`pet_at` 的 1.2s 窗口本来也会跳过的区间。

长时间认不出（如 09:46~09:49 那 3 分钟）时，BAND 一直不写数据 ——
**这正是我们要的**：那段时间的卡面属于新精灵，用旧词表校验必然错。

## 需要确认的实现点

- 换人信号的具体日志点在哪（`FORCED SWITCH` / `FAINT -> switch` /
  `SWITCH -> click card`）
- 是否有统一的"换人"代码路径可以挂钩，而不是在日志字符串上匹配

## 验收

1. 正常对局：BAND 仍能覆盖（音速犬 4/4、银月狼王 3/3 已验证）
2. 09:46 那种"换人后认不出"的场景：BAND 停止写入，日志明确
3. 不再出现"用甲的池校验乙的卡面"

## 若挂钩点不好找

退一步：用"换人面板出现"作为信号（`ui_probe` 已有检测），
或直接用 `cur_slot` 变化 + `bart` 的 `battle` 状态转换。