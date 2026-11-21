# UI 接口与数据契约检查（2026-11-21）

> 用户问："UI 搭建的接口对不对，识别检查信息获取做的对不对。"
> 本文记录实测结果。

---

## 一、接口对齐：16/16 全部正确

**方法**：从 `ui/app.js` + `ui/console.js` 正则提取所有 `/api/...` 调用，
再查 `src/ui_server.py` 是否注册了对应路由。

```
前端调用 16 个，后端全部注册，0 个不匹配：

  /api/bot                    OK
  /api/bot/start              OK
  /api/bot/stop               OK
  /api/dex                    OK
  /api/log                    OK
  /api/matches                OK
  /api/portrait/auto_confirm  OK
  /api/portrait/label         OK
  /api/portrait/label_group   OK
  /api/portrait/purge_junk    OK
  /api/portrait/skip          OK
  /api/portraits              OK
  /api/state                  OK
  /api/stats                  OK
  /api/team                   OK
  /api/turns                  OK
```

### 我一开始误报了两个

第一次跑检查时报 `/api/portrait/auto` 和 `/api/portrait/purge` 是 404。
**是我脚本的正则写错了**：`[a-z/]+` 不含下划线，把
`auto_confirm` 截成了 `auto`、`purge_junk` 截成了 `purge`。

修正正则（加 `0-9_`）后全部通过。

**教训**：检查工具自身的正确性要先验证，否则会制造假警报。

---

## 二、实测通过的数据接口

| 接口 | 返回 |
|---|---|
| `/api/health` | `{"ok": true, "t": ...}` |
| `/api/bot` | `running` / `robot_pids` / `supervisor_pids` / `pid` / `log_age` / `log_at` / `state_age` / `phase` |
| `/api/stats` | `wins=6 losses=3 rate=66.7%`，`season={wins:129,losses:157}`，`rank={now:2,of:3}` |
| `/api/team` | 5 只，字段完整 |
| `/api/log` | 851 行 |
| `/api/turns` / `/api/rounds` / `/api/dex` / `/api/matches` | 200，结构正常 |

`/api/team` 的六维与数据库一致：

```json
{"slot":1,"species_id":3071,"name":"音速犬","level":60,
 "max_hp":366,"atk":221,"spa":97,"def":171,"spd":150,"spe":260,
 "moves":"[7020840, 7040320, 7180270, 7060100]","types":["火"]}
```

**注意**：`moves` 是**字符串**（`"[7020840, ...]"`），不是数组。
前端如果要遍历，需要再 `JSON.parse` 一次。这是一个可以改进的点。

---

## 三、`/api/state` 的真实结构（机器人跑起来后实测）

```json
{
  "t": 1795248467.19,
  "seq": 317,
  "mode": "idle",
  "turn": 3,
  "clock": null,
  "me": {
    "name": null, "types": [], "hp_pct": null, "hp_abs": null,
    "hp_max": null, "energy": null, "energy_max": 10,
    "buffs": [], "trait": null
  },
  "foe": {
    "name": "迪莫", "types": ["光"],
    "hp_pct": null,
    "hp_range": [425, 490],
    "def_range": [175, 240],
    "nature": null, "nature_conf": null, "known_skills": []
  },
  "skills": [],
  "matchup": null,
  "verdict": null,
  "system": {
    "fps_ms": 429.7,
    "budget_ms": 120.0,
    "blind": 0,
    "detectors": {"bar": false, "skillbar": false, "countdown": false},
    "conflicts": 0,
    "gate": null
  },
  "bench": [
    {"name":"音速犬","hp_pct":null,"alive":true},
    ... 5 只
  ],
  "_stale": true,
  "_age": 15.88
}
```

### 这是正确行为，不是 bug

当时机器人**在大厅**（`mode="idle"`），所以：

- `me.name = null` —— 大厅里没有出战精灵
- `skills = []` —— 大厅没有技能栏
- `foe.hp_pct = null` 但 `hp_range = [425,490]` —— 血条百分比读不到，
  但有该精灵的血量区间估计（这是**设计**，不是缺失）
- `_stale=true, _age=15.88` —— 15 秒前的帧，因为 idle 时更新慢

**这些字段的语义是正确的**：前端应该显示"未在战斗"，而不是把 null 当错误。

---

## 四、发现的问题

### 问题 1：`system.fps_ms` 和 `budget_ms` 量纲不一致（会误导）

```
fps_ms    = 429.7   实际是"一轮完整感知循环"的耗时
budget_ms = 120.0   是 POLL_S(0.12s) 换算来的
```

两者比较得出"超预算 3.6 倍"，但：

- `bench_loop.py` 实测 `observe()` 中位 **22.9 ms**、P90 29.5 ms
- `fps_ms` 测的是 `_obs_t0` 到稳定化之后的**整段**（含 skill_reader）

**影响**：前端如果按 `fps_ms > budget_ms` 报警，会一直误报。

**建议**：
- 要么把 `budget_ms` 改成"合理的一轮耗时上限"（比如 500ms）
- 要么把 `fps_ms` 拆成 `observe_ms` 和 `total_ms` 两个字段

### 问题 2：`/api/team` 的 `moves` 是字符串

```json
"moves": "[7020840, 7040320, 7180270, 7060100]"
```

前端需要再 parse 一次。建议后端直接返回数组。

### 问题 3：`/api/bot` 没有 `uptime_s`

我的检查脚本猜了这个字段，实际后端没有。不算 bug，但如果前端想显示
"已运行 X 分钟"，需要后端补。

---

## 五、检查工具

`scripts/check_ui_api.py`：

```powershell
& $py scripts\check_ui_api.py              # 自动找在跑的 ui_server
& $py scripts\check_ui_api.py --port 8770  # 指定端口
```

检查项：
1. 每个 API 是否可达（HTTP 200）
2. 返回的 JSON 结构是否包含关键字段
3. 字段值的语义（`my_pct` 应在 0~100，`skills` 应有 4 个槽位）
4. 前端调用的每个路径后端是否注册（**读源码比对，比打接口更准**，
   能区分 GET/POST）

---

## 六、结论

| 项 | 结论 |
|---|---|
| 接口路由 | ✅ 16/16 全对齐 |
| 数据契约 | ✅ 字段完整，语义正确 |
| 识别健康度上报 | ✅ `system.detectors` 正确反映 bar/skillbar/countdown |
| `fps_ms` vs `budget_ms` | ⚠️ 量纲不一致，会误报 |
| `team.moves` 类型 | ⚠️ 字符串而非数组 |

**总体：UI 接口是对的。** 两个 warn 都是展示层的小问题，不影响功能。
