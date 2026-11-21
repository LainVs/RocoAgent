# 计划：场次 id —— 让「这一场打得怎么样」能算出来

> 2026-11-21 · 代码：`src/matches.py`、`src/test_matches.py`
> 相关：`src/robot.py`（写入三份日志的地方）、`src/ui_server.py`（`/api/matches`）

---

## 0. 问题：三份日志对不上

每场对局的信息散在三个文件里，而它们**没有共同的场次标识**：

```
rounds.jsonl   {match: 3, start: "09:22:25", end: "09:25:09", seconds: 164.4, actions: 5}
turns.jsonl    {ts: "09:23:10", turn: 3, action: [...], skills: [...]}
results.jsonl  {ts: "09:21:25", result: "win", delta: 1, ...}
```

两个致命缺陷：

1. **`match` 是进程内计数**。机器人重启就从 1 重来 —— 实测 `rounds.jsonl`
   里 `match=1` 出现了 11 次。
2. **时间戳只有时分秒，没有日期**。跨天必然撞。

实测后果（3168 条回合）：

| 归属结果 | 条数 | 占比 |
|---|---|---|
| 能唯一归属到某一场 | 1444 | **45.6%** |
| 落空（不在任何 round 的时间窗内） | 313 | 9.9% |
| **落在多场重叠区** | 1411 | **44.5%** |

接近一半的数据根本对不上。于是"上一版策略到底变强了没有"这个问题
**永远回答不了** —— 不是缺数据，是数据拼不起来。

---

## 1. 方案：mid

每场对局开始时生成一个稳定 id：

```
mid = "YYYYMMDD-HHMMSS"     例：20261121-093017
```

- 同一秒不可能开两场 → 跨进程重启也唯一
- 带日期 → 跨天不会撞
- 可读 → 日志里一眼看出是哪一场

三份日志都写这个字段。老记录没有，`legacy=True` 标出来。

### 1.1 为什么不去"补"老记录的 mid

时间戳没有日期，任何按时间窗的猜测都会把不同天的两场混在一起。
**宁可为空，也不要算错** —— 一个错的胜率比没有胜率危险得多
（这一轮的 `ui-m45` 就是被假的 66.7% 坑的）。

所以 `summarize()` 对老记录只输出 rounds 自己的字段，回合数一律 0。

---

## 2. 实现

### 2.1 写入侧（robot.py）

| 位置 | 加了什么 |
|---|---|
| 开局（`if live:`） | `self.match_mid = matches.new_mid(now)` |
| `_end_match()` | rounds 写 `mid`；**然后** `last_match_mid = mid`，清空 `match_mid` |
| `_dump()` | turns 写 `mid` |
| `_log_result()` | results 写 `mid`（取 `last_match_mid`） |
| `record` 通道 | results 写 `mid`（同上） |

关键点：**胜负是在回到大厅之后才判定的**，那时 `match_mid` 已经清空，
所以要用 `last_match_mid` 记住"刚结束的那一场"。这是唯一容易写错的地方，
`test_matches.py` 专门覆盖了。

### 2.2 读取侧（matches.py）

```python
summarize(limit)       # 每场一行：时长/手数/胜负/对面/读数质量
match_detail(mid)      # 某一场的全部回合 + 结论
```

对齐规则：

```
rounds  ← 主索引（一场一条）
turns   ← 按 mid 分组，不按时间窗猜
results ← 按 mid 分组
```

### 2.3 胜负的两条通道，取可信的那条

同一场可能被两个通道各记一次：

| 通道 | 字段 | 可信度 |
|---|---|---|
| `record`（游戏内「历史战绩」面板） | `before/after/before_losses/after_losses/total` | **高**：单调累加计数 |
| `badge`（段位卡星级差） | `before/after/of` | 低：会 +1/+2、升段清零、还会卡死 |

`_pick_result()` 优先取 `record` 那条。UI 上 `badge` 来源的胜负标一个
`*`，鼠标悬停提示"来自段位卡推断，可信度低"。

---

## 3. 测试

`src/test_matches.py`（22 项）用合成数据把规则钉死：

```
[1] mid 格式          形如 YYYYMMDD-HHMMSS；不同秒不同；short_mid 可读化
[2] summarize 对齐    两场都列出；新记录 legacy=False；老记录 legacy=True
[3] join 的内容       胜负取到；优先 record 而非 badge flat；
                      对面/我方/回合去重/盲打计数/读数比例
[4] 老记录不猜        不挂任何回合；胜负 unknown
[5] match_detail      带回回合；胜负；不存在的 mid 返回 None
[6] 缓存失效          追加后读到最新；不重复计数
```

---

## 4. UI

战绩页原来的「对局记录」表（只有 rounds 的 5 列）换成按场次对齐的 8 列：

| 场次 | 开始 | 时长 | 手数 | 胜负 | 对面 | 读数 | 备注 |
|---|---|---|---|---|---|---|---|
| 1121-1155 | 11:55:00 | 180s | 3 | 胜* | 水灵 | 5/6 | 盲打 1 |
| 老#2 | 11:56:24 | 378s | 0 | — | — | — | 老记录(无 id) |

- 场次列 hover 显示完整 mid
- 读数 = 可读槽位 / 总槽位（直接反映这一场的感知质量）
- 备注优先提示会影响出招的东西：盲打次数、无数字回合数

接口：`GET /api/matches?limit=40`、`GET /api/match?mid=...`

---

## 5. 还没做的

- **老记录无法回溯**。这是设计选择（见 §1.1），不是欠账。
- **`rounds.jsonl` 里 `match` 字段仍然保留**，为了兼容旧的复盘脚本
  （`review_turns.py` 等）。等这些脚本都迁到 `mid` 就可以删。
- **`results.jsonl` 的两个 schema 还没合并**（一个记星级、一个记累计胜负）。
  现在靠 `_pick_result()` 在读取时挑，写入端仍是两处。
  合并的收益是文件更干净，风险是改动写入路径 —— 等有稳定实盘再动。
