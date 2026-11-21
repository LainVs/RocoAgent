# AGENTS.md — RocoAgent

洛克王国世界 PvP 自动对战机器人（Windows + 硬件 HID 开发板）。

## 必须用指定解释器

```powershell
$py = "C:\Users\Lenovo\Desktop\hardwork\CV_ESP_Game_1\.venv\Scripts\python.exe"
```

系统 `python` **缺 `bleak`**，跑了会在连板子时炸。所有命令都用 `$py`。

## 外部依赖不在仓库里

`src/paths.py` 启动即校验，缺任何一个直接 `RuntimeError`：

```
config/paths.local.json  ->  tools / game_data / raw / helper
```

本机值已配好（`D:\Tools`、`D:\GameData\...`）。克隆到新机器必须先建这个文件，
样板见 `config/paths.example.json`。`esp_shim` / `digitread` / `winocr.ps1` 都在 `paths.TOOLS`。

## 启动 / 停止 / 看状态

```powershell
& $py src\selfcheck.py --ble        # 自检（不出招，--ble 才连板子）
& $py src\run_bot.py --supervisor   # 看门狗 + 机器人，脱离 shell
& $py src\run_bot.py --status
& $py src\run_bot.py --stop
```

`--stop` 必须按 **keepalive → botsup → robot** 顺序杀。`keepalive.py` 每 60 秒
检查 botsup，只停 botsup/robot 会被它 60 秒内重新拉起。

**禁止按进程名批量杀**（`Stop-Process -Name python`）。本机还有 `ui_server.py`
在并行开发。只按 PID 或命令行精确匹配，`scripts/stop_all.py` 已实现。

## 测试：不是 pytest

没有 pytest / CI / 构建配置。每个测试是独立脚本，`if __name__ == "__main__"` 自跑：

```powershell
& $py src\test_imports.py       # 先跑这个：验证 robot.py 的模块级名字没被编辑误删
& $py src\test_skill_kind.py
& $py src\test_policy_cost.py
```

失败时退出码 1，末行打 `ALL PASS` 或 `FAILED N`。
`test_imports.py` 值得单独记住：给 `robot.py` 的 `from config import (...)` 加名字时
很容易覆盖掉同行已有的名字，编译器查不出来（只在运行时 `NameError`），
曾导致 botsup 连崩 5 次。

## 架构：robot.py 是门面

`robot.py` 有 4000+ 行但主要是 `Bot` 类和入口。实际实现已拆到：

| 文件 | 职责 |
|---|---|
| `config.py` | 常量 / 日志 / 单实例锁 `acquire_lock()` |
| `winio.py` | 窗口 / 焦点 / 点击 / `Screen` |
| `perceive.py` | `Perc.observe()` — 截图 → state |
| `ui_probe.py` | 按钮 / VS 屏 / 结算页 / 换人面板 |
| `policy_v2.py` | **当前主用**的期望值决策 |
| `policy_legacy.py` | 初版 `decide()`，只比字面伤害 |
| `statmodel.py` | 唯一数值模型（面板/伤害/克制） |
| `damage_v2.py` | roco-cal 移植的伤害公式 |

`robot.py` 用 `from X import (...)` 把子模块符号 re-export 出来，
所以 `robot.log` / `robot.acquire_lock` 都能用。

## 两套坐标系，混用必错

| 函数 | 参数坐标系 | 是否减 `BASE_OFF`(13,58) |
|---|---|---|
| `click_rel(x, y)` | **窗口坐标**（`config.*_REL` 用的） | 是 |
| `abs_point(box, cx, cy)` | **客户区坐标** | 否 |

客户区原点 ≠ 窗口原点，实测差 `(13, 58)`。
`config.py` 里 `LOBBY_BTN_REL` / `RESULT_BTN_REL` / `ADVANCE_REL` 全是窗口坐标。

用 MCP 截图坐标换算时走 `scripts/click_img.py`（三层换算 + 校验打印），不要手算。

## 输入必须走开发板

用户明确要求：**任何鼠标/键盘操作都必须经开发板**，不能用 `SendInput` / `ctypes.mouse_event`。
`scripts/game_cli.py`（click/move/key）和 `scripts/click_img.py` 都已走 BLE。
MCP 的 `Click`/`Move`/`App` 会动系统鼠标，**只允许用 `Screenshot`/`Snapshot` 读屏**。

## 数据源优先级：存疑以 roco-cal 为准

`data/external/` 下是**外部资产**（固定资产，已入库，换机器可用），
每个目录都有 `VERSION.md` 记录来源/许可/更新命令：

| 目录 | License | 能做什么 |
|---|---|---|
| `roco-cal/` | MIT | 伤害公式 + S5 精灵数据的权威源 |
| `nrc-ai/` | **MIT** | 效果引擎（197 原语、12 印记、491/491 技能配置） |
| `rocom-helper/` | 无 | 只读数据（buff_map / innate_skills / special_move_map） |
| `rocom-data/` | 无 | 只读数据 + 战术 YAML（169 套玩家阵容） |
| `rocoarena/` | 无 | 天梯样本库快照（真实配法多样性，PIMC 信念来源） |

无 LICENSE 的几个**只作数据参考，不要复制代码进 `src/`**。

**克制矩阵已三方交叉验证**（roco-cal、9178、ROCO ARENA 的 18×18 表），
324/324 完全一致。注意 ROCO ARENA 的 `typeMultipliers[守][攻]` 方向是反的。

`data/roco.sqlite3` 是主库（1228 只精灵）。与外部数据冲突时按 roco-cal。

**做技能/特性效果系统前先看 `data/external/nrc-ai/src/effect_models.py`
和 `effect_data.py`**，它已经把 197 种效果原语和 12 种印记设计好了，别从零写。
注意它的自动生成部分有漏（筛管奔流、丢冰块缺效果），roco-cal 的数据更全。

第三方参考项目总表见 `docs/REFERENCE_PROJECTS.md`。

### 拉取外部项目的网络坑

`git clone` 和 `codeload` 走 Clash 代理（`127.0.0.1:7897`）**必失败**
（超时 / early EOF / 0 字节）。用项目内脚本：

```powershell
& $py scripts\gh_fetch.py <owner/repo> data\external\<名字> [子路径...]
& $py scripts\raw_get.py  <owner/repo> data\external\<名字> <文件路径>
```

`gh_fetch.py` 走 api.github.com（未认证限 60 次/小时，大仓库会中途 403）；
`raw_get.py` 补大文件，对 SSL 抖动做 6 次重试。

## 日志：判断"崩了还是被杀"

| 文件 | 看什么 |
|---|---|
| `logs/bot_stdout.log` | 决策 / 心跳，正常每 15 秒至少一行 `state ...` |
| `logs/bot_stderr.log` | Python 异常栈 |
| `logs/crash.log` | 原生崩溃栈（段错误） |
| `logs/supervisor_restarts.log` | `rc` + 存活秒数，区分崩溃/被杀/预算到点 |
| `logs/turns.jsonl` | 每手决策（**轮询日志，同回合重复多次，需按 (turn,my_pct,foe_pct,action) 去重**） |
| `logs/opponent_actions.jsonl` | 对手行为，带 `match` 和 `act` 标签，**做对手模型用这个，别从 turns.jsonl 反推** |

`turns.jsonl` 是多进程轮询日志，场次会交错；`opponent_actions.jsonl` 才有干净的场次边界。

## 队伍配置在数据库，不在代码

`my_team` 表（`data/roco.sqlite3`）的 `moves` 字段是技能 ID 数组，顺序 = 卡槽顺序。

```powershell
& $py src\team_db.py --set 3 --species 卡瓦重 --moves 筛管奔流,扬沙,虫击,冰雹
```

`--set` 的参数是**槽位号**，不是精灵名。`--species` 可省略（保留原精灵只改技能）。

**配置过期是最隐蔽的 bug**：`my_card_costs()` 按配置查耗能，配错会让能耗惩罚
算错几倍，表现为"策略选了低伤害技能"，很容易误判成策略 bug。
用 `scripts/team_audit.py` 对照实盘读数检查。

## 编辑导入语句后必跑 test_imports

见上文「测试」一节。

## 禁止用 PowerShell 往返改写含中文的文件

`Get-Content`（PS 5.1，无 BOM 的 UTF-8 文件）按 GBK 解码，遇到无法映射的字节
**直接丢字节填 `?`**，信息永久丢失。曾把 `robot.py` 的 588 字节毁掉。

用 `edit` / `write` 工具，或写 `.py` 脚本显式 `encoding='utf-8'`。
绝不用 `(Get-Content f) | Set-Content f`。

## 相关文档

- `docs/STARTUP.md` — 启动链、崩溃排查、BLE/USB 链路、msvcp140 坑
- `docs/SRC_INVENTORY.md` — 每个 src 文件的职责与依赖关系
- `docs/MULTI_SESSION.md` — 多会话协作约定（进程/端口/git）
- `docs/ARCHITECTURE.md`、`docs/DAMAGE_MODEL.md`、`docs/DATA_SOURCES.md`
- `README.md` 的目录树已过时：列的 `perception.py` / `policy.py` / `config/team.json` 都不存在
  （实际是 `perceive.py` / `policy_v2.py`，队伍在 `my_team` 表）；
  `damage.py` 是数据层，伤害公式在 `damage_v2.py`。
