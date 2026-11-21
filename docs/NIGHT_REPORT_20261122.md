# 夜间实盘记录 2026-11-22

> 主线：让机器人**完整打完一局**并持续产出可用数据。
> 本文记录当晚的实测数字、每个 bug 的证据与修法。

---

## 一、成果：手数从 2.0 升到 4.6

按小时统计 `logs/rounds.jsonl`（中位时长 / 平均手数）：

| 时段 | 场次 | 中位时长 | 平均手数 |
|---|---|---|---|
| 20 时 | 45 | 17s | 2.4 |
| 23 时 | 6 | 79s | 2.0 |
| 00 时 | 21 | 46s | 2.1 |
| 01 时 | 28 | 64s | 2.5 |
| 02 时 | 40 | 44s | 3.1 |
| **03 时** | **43** | **62s** | **4.6** |

单场最长：

```
03:47:02 -> 03:51:23   261s  6 actions
03:34:43 -> 03:38:34   231s  8 actions
03:42:41 -> 03:46:29   228s  10 actions
```

对照当晚早期的典型值（26~27 秒 / 0~1 手）。

---

## 二、逐个 bug 与证据

### 1. 板子掉线就退出进程（`_ble` 的 `SystemExit(3)`）

```
00:00:35 BLE key 3 failed (board offline)
00:03:00 BLE offline x6 -> exiting so the supervisor restarts with a full scan
```

重启也是 `BLE waiting for board`，9 分钟连不上 —— 退出没换来恢复，
只把正在打的一场连同内部状态丢了。

现在：标记 `_board_offline_since`，主循环冻结决策 + 后台重连，
超过 20 秒允许 `allow_discover=True`。恢复后打印
`BOARD back online after Ns` 继续。

### 2. 遮挡检测从来没接进主循环

`winio.is_covered()` 早就写好了，只有 `standalone` 在用。
OpenCode 窗口压在游戏左半边时，抓屏抓到的是聊天界面，识别读垃圾，
但界面仍显示"战斗中 · 未知"。

- 主循环每帧检查，被遮挡就 `_raise_topmost()`，抬不起来就等；
- `is_covered()` 从"只看窗口中心点"改成 **3×5 网格采样** ——
  实测遮挡经常是"盖住左半边"，中心点仍是游戏。

### 3. VS 准备页被当成结算页 -> 点掉对局

`logs/stuck_000004.png` 证据：VS 页底部「取消准备」和结算页「退出」
在同一个 `RESULT_BTN` band 里。

```
VS 准备页   white=13824  -> has_exit_button=True（误判）
真结算页    white= 8372  -> True
真实战斗帧  white=    0  -> False
```

于是"脱困"变成了"取消对局"（0 actions）。修法：脱困条件加
`not has_vs_screen(screen)`。

### 4. 对局结束判据：试了三种，最后用技能栏

| 判据 | 结果 |
|---|---|
| `battle=False` 持续 25 秒 | 对面回合被当结束 -> **弃权判负** |
| 我方 HUD 消失 12 秒 | 4:3 下血条常读不到 -> **误伤正常对局** |
| 画面指纹静止 | 结算页角色有呼吸动画 -> **5 分钟不触发** |
| **技能栏 40 秒没画** | ✅ 一局里每个我方回合都会画一次 |

`_end_match()` 还会在 `match_started=None` 时写假的
`MATCH 0 END (0s, 0 actions)`，已修。

### 5. 结算页卡死：`advance` 闸门被自己的修复关掉了

收紧 `_end_match` 之后 `match_ended_at` 永远不被设置，
`_idle_tick` 里 `may_advance` 闸门永远关着 —— 排位结算页
（只剩「我方战况」+「今日获得」）点不动，实测卡 5 分钟。

修法三层：`_idle_tick` 无条件调用 + 画面静止 6 秒例外 +
20 秒不在战斗 + 静止 8 秒直接 `_end_match()`。

### 6. `advance` 无限点击

```
00:57:28 advance splash (click 12)
00:57:31 advance splash (click 1)     <- 配额被指纹重置了
```

结算页角色有动画，4x3 指纹一直在变，`advance_count` 每帧清零。
现在要求新指纹**连续 2 秒不变**才算翻页。

### 7. 技能栏判据写死像素 -> 4:3 下永远 `bar=False`

```python
if xs[-1] - xs[0] > 220:                  # 写死
ok = all(120 <= g <= 420 for g in gaps)   # 写死
```

实测 4:3 下 `gaps = [106, 192, 226]`，第一个 106 直接判死。
改成相对比例（x 跨度 `0.45×宽`、gaps `0.05~0.30×高`）。

**踩坑**：第一次改以为改好了，但编辑落到了 x 跨度那段，`gaps` 那行
没变。表现是"三个判据单独算都通过，函数却返回 False"。
最后用 `inspect.getsource()` 打真实源码才看到旧值还在。
**以后改判据必须用 `inspect.getsource()` 确认磁盘代码真变了。**

### 8. 大厅被误判成战斗 -> 在大厅里盲出招

```
FOE_BAR (2251,170,245,20) -> {'pct': 24.5, 'color': 'red', 'ui': False}
日志：battle=True bar=False me=59.0 foe=29.8
      T18 SKILL 2 (bar never drew, blind)
```

大厅的段位横幅压在 `FOE_BAR` 上；"限时试用系别 5天0小时"被当倒计时。
硬闸：`battle=True` 时每秒探一次 `has_start_button()`，有就清掉。

### 9. 4:3 下 `battle` 恒 False（根因）

```python
battle = bars and my["ui"] and foe["ui"]    # ui = 血条上下 2px 灰底板
```

2048x1536 下血条**没有**那两条灰底板。

**走过的弯路（重要，别重试）**：试过"读不到就按偏移上下搜索"，
结果**制造假数据** ——

```
live7.png（我方 0/417 已倒的真实战斗）-> 搜索读到 my=100%
```

场景里到处都有满足颜色条件的色块。也试过"成对搜索 + 两侧同偏移 solid"，
仍在 live6/live7 上给出互相矛盾的偏移（-48 / -32）。

**结论：不做运行时搜索。读不到就报读不到，绝不猜。**

实际修法：

1. `_read_bar` 新增 `solid`：真血条的填充**从最左列开始且连续**
   （实测战斗帧 cols 0..229，场景色块是零散分布）；
2. `battle` 的 HUD 证据改成 `ui or solid`；
3. **技能栏画着就直接判战斗**（技能栏只在"对局中 + 我方回合"出现）；
4. VS 准备页排除：两边都满血且都无灰底板 -> 不是战斗。

### 10. `_fit` 横向拉伸把头像糊掉（我方识别率 16%）

2048x1536 下 `scale=(0.8, 0.96)`，头像框 76x80 的物理尺寸是 61x77。
`_fit` 直接 `resize(img, (76, 80))` —— **横向 ×1.25**，把图糊掉。

```
修复前  best 0.409（认错）
修复后  best 0.765（正确，跨过 0.72 阈值）
```

### 11. 我方头像 margin 天然很小

同一只精灵有多个形态模板（`银月狼王#2` / `#7`），分数差常只有 0.02~0.05。
要求 `margin >= 0.06` 把正确识别（0.82）也挡掉了。
改成"分数 >= 0.80 时不要求 margin"（形态选错无害，
`_portrait_species()` 会归一到种族名）。

### 12. 单帧识别不稳 -> 多帧投票

HUD 头像有入场/待机动画，同一只精灵连续帧分数在 **0.34~0.82** 之间跳。
改成累计投票：

```python
votes[name] += max(0.0, sc - 0.45)
if max(votes.values()) >= 0.25: 采纳
```

真换精灵 / 新一局时清空投票历史。

### 13. 卡面读出 700 伤害还当必杀

```
T1 SKILL 2 (skill 2: 700 dmg, 0 taken, priority +5 [v2 0.5375])
```

对面雪影冰灵最大血 ≈400。`standalone` 早就有这道过滤，
**机器人链路一直漏着**。两处都加 `dmg > 对面最大血 * 1.2 -> 不可读`。

### 14. `_stuck_at` / `now` 未初始化导致连续崩溃

```
AttributeError: 'Bot' object has no attribute '_stuck_at'
UnboundLocalError: cannot access local variable 'now'
```

两次都是"给实例加新字段 / 往主循环插代码"时漏了初始化。
`test_imports` 查不出这种错误，只有真跑才会炸。
**教训：往主循环插代码前，先确认 `now` 在插入点之前已赋值。**

---

## 三、新增能力

### PvP 大数据（`src/pvp_meta.py`）

从 169 套真实天梯阵容 + 1228 只种族值，给出**概率分布**而不是单点：

```
寂灭骨龙  样本 35
  性格  固执 49% / 平和 31% / 慎重 6% ...
  HP    425 (65.7%) / 490 (34.3%)
  物防  191 (5.7%) / 207 (88.6%) / 238 (5.7%)
  携带  隼鳞 91% / 偷袭 60% / 吓退 54% / 电弧 51% / 先发制人 43%
  伤害 213 -> p10=43.5% 中位=50.1%，>=50% 概率 65.7%
```

详见 `docs/PVP_META.md`。

### 对手行为模型接入决策

`opponent_log` 采集 -> `opponent_model` 三层池化 -> `anticipate` 预判
-> `policy_v2` 用进决策。日志里能看到：

```
T1 SKILL 2 (... [v2 0.2004] [对手 攻28% 换51% 增21% global n=230])
```

详见 `docs/OPPONENT_MODEL.md`。

### 先发制人

`skills` 表本来就有 `priority` 列，整条链路从来没读过。现在：

- 这回合会死 + 先发技能能反杀 -> 用先发技能（不换人）；
- 先发技能在速度之前结算（`acts_first = we_outspeed or priority > 0`）；
- 不会因为 priority 高就压过明显更高的伤害。

### 借用技能识别

借用/取念用出去后卡面会变成复制来的技能、**会画伤害数字**。
原来一律按"无伤害"处理，policy 永远不敢用。现在标 `copied=True`。

---

## 四、仍未解决

1. **我方头像识别率仍不稳定**：旧模板是另一套渲染下抓的
   （同批抓帧互配 0.99，对旧模板只有 0.10~0.19）。
   需要按当前渲染重抓模板，或用 `label_portraits.py` 批量标注。
2. **`pet identity unknown` 仍间歇出现**，导致 v2 覆盖率不到 100%。
3. **`test_flow.py` 有 4 个历史失败**（`has_start_button` 样本帧对不上），
   不在测试基线里。
4. **性格表缺 10 个**（保守/紧张/孤僻/认真/浮躁/淘气/狂妄/沉着/马虎/理智），
   影响物防约 5%，HP 不受影响。
5. **buff 界面显示形式仍未知**（不知道图标/文字/有无层数），
   所以 `a2 防御 / a3 状态` 还无法细分。

---

## 五、命令

```powershell
$py = "C:\Users\Lenovo\Desktop\hardwork\CV_ESP_Game_1\.venv\Scripts\python.exe"

& $py src\run_bot.py --supervisor     # 启动
& $py src\run_bot.py --status
& $py src\run_bot.py --stop

# 测试基线
& $py src\test_imports.py
& $py src\test_perceive.py
& $py src\test_record.py
& $py src\test_record_geometry.py
& $py src\test_portrait_fusion.py
& $py src\test_standalone.py
& $py src\test_policy_cost.py
& $py src\test_priority.py
& $py src\test_pvp_meta.py
& $py src\test_anticipate.py
& $py src\test_opponent_model.py
& $py src\test_skill_kind.py
& $py src\test_statmodel.py
& $py src\test_action_button.py
& $py src\test_foe_hp.py
```
