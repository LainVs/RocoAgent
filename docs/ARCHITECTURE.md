# RocoAgent 架构与决策模型

> 最后更新：2026-11-20
> 目标：排位胜率 ≥ 60%，且可量化、可迭代

---

## 零、代码结构（2026-11-20 拆分）

`robot.py` 原本 4126 行，已按职责拆成 6 个模块。**拆分是纯搬移**，
118 个函数的反汇编逐项对比 0 差异（`scripts/verify_split.py`）。

| 模块 | 行数 | 职责 |
|---|---|---|
| `config.py` | 165 | 常量 / 日志 / 单实例锁 / 崩溃留证（共享层） |
| `winio.py` | 293 | 窗口查找 / 焦点 / 硬件点击 / `Screen` 截图与坐标换算 |
| `perceive.py` | 572 | 词表 / 头像指纹 / 卡片可放判定 / `Perc` / 技能栏解析 |
| `ui_probe.py` | 248 | 按钮 / VS 页 / 结算页 / 换人面板探测 |
| `policy_legacy.py` | 231 | 旧 `decide()`：治疗 / 聚能 / 最大伤害 |
| `robot.py` | 2640 | 门面 re-export + `Bot` 类 + 入口 |

依赖方向是单向的：

```
config  <-  winio  <-  perceive  <-  ui_probe
                              \        /
                               policy_legacy
                                     |
                                  robot.py (Bot)
```

`robot.py` 保留全部原有名字的显式 re-export，所以 20 多处 `import robot`
一处未改。**注意**：不能用 `import *`，下划线名字（`_card_lit` / `_read_bar`
等）不会被导出，而 `test_lit.py` / `test_skillbar.py` 确实在用它们。

### 伤害计算只有一套

| 模块 | 职责 |
|---|---|
| `statmodel.py` | **唯一**的数值模型：面板值 / 等级系数 / 伤害 / 克制 / 区间 |
| `damage.py` | 纯数据访问层（读 `roco-claude` 的 pokemon.json / skills.json） |
| `foe_hp.py` | 对手血量 / 承伤查询的唯一入口 |

已删除（依据实测）：`dmg_calc.py`（STAB 错）、`sim.py`（死代码）、
`pet_state.py`（死代码）、`foe_damage.py`（克制规则错）、`policy.py`（死代码）。
详见 `docs/DAMAGE_MODEL.md`。

---

## 一、衡量优先于优化

没有胜负标签，一切调参都是感觉。当前唯一可靠的胜负信号：

```
大厅段位徽章 "★N/3"  物理 ROI ≈ (2020, 615, 80, 36)
每局开始前读一次，结束后读一次
delta = +1 胜 / -1 负 / 0 保分
```

实测：10:57 那局之前 `1/3`，之后 `0/3` → 输。

样本量提醒：从 50% 区分 60% 需要 **~100 局**（二项检验 95% 置信）。
按 3 分钟一局约 5 小时，所以**迭代速度 = 胜率优化速度**。

---

## 二、数据源（已核实为 S5）

| 来源 | 内容 | 用途 |
|---|---|---|
| `<ROCO_RAW>\` | pokemon / skills / type_chart | 伤害与克制 |
| `...\roco-cal` | README 明示「已同步至 S5」的完整战斗模拟 | 伤害引擎参照 |
| `...\rocom-helper` | 协议解析 + 状态机 + 战术引擎 + buff_map | 精确状态 / 决策参照 |
| `...\game-automation\...\memory_reader.py` | 通用 ReadProcessMemory 封装（无洛克偏移） | 备选 |
| `..\myteam.md` | 我方 6 只的属性、特性、技能、实测血量 | 校准 |

已移植：`src/pvp_engine.py`（TTD/TKO）、`src/policy_v2.py`（期望值决策）。

---

## 三、伤害模型

**唯一实现**：`src/statmodel.py`（原样移植自 9178 伤害计算器，1:1 PvP 调参验证过）。
性格表 `config/nature_table.json`（31 项，从其 JS 解析导出）。

```
面板 HP    = round((sv*1.7 + iv*0.85 + 70) * nature + 100)
面板 非HP  = round(round(sv*1.1 + iv*0.55 + 10) * nature + 50)
            例外：sv==105 且 iv==0 且 nature==1.0 时中间值用 floor（float32 漂移）
等级系数   = lv>=0 ? 1+lv*0.1 : 1/(1+|lv|*0.1)
伤害       = floor(攻/防 × 37/41 × 威力 × 克制 × 本系(1.25) × 特性 × 其他 × 减伤 × 连击)
星陨印记   = 威力 x²+24x−24（幻系，不吃本系）
克制合成   克制加法（被克两次 ×3），抵抗乘法（被抵抗两次 ×0.25）
```

四只实测血量全部对上：

| 精灵 | 种族值 | 个体 | 性格 | 模型 | 实测 |
|---|---|---|---|---|---|
| 音速犬 | 85 | 60 | 无 | **366** | 366 |
| 冰钻布鲁斯 | 73 | 60 | 无 | **345** | 345 |
| 寂灭骨龙 | 120 | 60 | +生命 | **490** | 490 |
| 巨鼓象 | 130 | 60 | +生命 | **510** | 510 |

> 前两只"公式对不上"其实是没点满 HP；后两只是**生命性格 +20%**；
> 巨鼓象还牵出库里一个错值（hp=66 是共享占位模板，真值 130）。

**数据源不能合并**：`data/roco.sqlite3`（1024 只，含 S5 新宠）是主库；
`damage.py` 读的 `roco-claude/pokemon.json`（499 只）有 99 只宠物的技能池
我们的库没有。两者互有优势，所以 `damage.py` 保留为纯数据层。

> **删除了哪些重复实现、依据是什么** → `docs/DAMAGE_MODEL.md`
> **外部项目对照与待移植资产** → `docs/REFERENCE_PROJECTS.md`


---

## 四、决策模型

出处：`rocom-helper/src/analysis/tactical/scoring.py` + `roco-claude/data/mechanics/decision_value.md`。

### 4.1 期望值打分

```
score(a) = Σ_o P(o) · [ 0.25·我打他% + 0.30·能否击杀
                      − 0.15·他打我% − 0.20·他能否击杀我
                      + 0.05·属性对位 + 0.03·剩余能量 ]
```

### 4.2 顺序决定一切

```
能应对 → 应对方先手
否则   → 速度高者先手
先手且能斩杀     → 直接打
后手且会被斩杀   → 换人 / 防御 / 接受交换
```

### 4.3 回合数

```
完成斩杀所需回合 = ceil(对方剩余血量 / 单次伤害)
```

### 4.4 强化（状态技能）的门槛

原始实现（rocom-helper）只给"stat_up"加固定分，**不判断能不能活**。
我们改成必须先通过存活检查：

```
用强化 ⟺ 预计承伤 < 当前血量 × (1 − 0.35)
否则扣 0.25 分
```

### 4.5 换人估值

```
换人得分 = 换上那只的伤害/承伤收益 − 换人成本(0.12)
```
不是"轮着试"，是"换上来的明显更好才换"。

权重全部外置在 `config/policy_weights.json`，后续用日志拟合。

---

## 五、三条数据通路

| 通路 | 精度 | 风险 | 状态 |
|---|---|---|---|
| **视觉**（现状） | 技能栏 60~70%，血量准 | 无 | 在用，卡片格点有 bug |
| **抓包**（rocom-helper） | 100%，含 buff 与胜负 | 中（装抓包驱动） | 已确认 8195 在连，待提权 |
| **内存** | 100% | 高（ACE 可能检测句柄） | 暂缓 |

抓包链路（纯被动，密钥取自握手 ACK，不读内存）：

```
TCP:8195 → Scapy → TCP 重组 → BE21 帧 → AES-128-CBC → Protobuf
        → 状态机（双方 HP/能量/buff/天气/回合/胜负）
```

前置：Npcap 或管理员权限的 `pktmon`（Windows 自带，无需装驱动）。

```powershell
# 管理员 PowerShell
pktmon filter remove
pktmon filter add -p 8195
pktmon start --capture --pkt-size 0 --file "$env:TEMP\roco.etl"
# 打一局，然后
pktmon stop
pktmon etl2pcap "$env:TEMP\roco.etl" -o "$env:TEMP\roco.pcap"
```

---

## 五之二、多线路并行 + 交叉验证

单通道不可信：视觉对某些精灵的 1 号位读 112/112 失败；协议精确但要提权；
OCR 慢但稳。所以改成**多通道同时读、按字段投票**，分歧本身就是错误信号。

```
vision  ─┐
proto   ─┼─→ state_bus（按字段取优先级最高者）
ocr     ─┤         ├─ 其他通道不一致 → 该字段置信度降到 0.3~0.4 并记冲突
memory  ─┘         └─ 没人给的字段 = 未知（绝不填 0）
```

跨通道一致性检查（已实现）：

```
foe_hp_pct  vs  100 × foe_hp_abs / foe_hp_max
若相差 > 4 个百分点 → 判为冲突，本回合不允许"闭眼出招"
```

落盘：`logs/state_conflicts.jsonl`，每条记字段、取值、分歧来源。
这份数据同时是**感知错误的训练集**——哪一列错、错多少、跟谁冲突。

工具：

```powershell
python src\verify_channels.py --seconds 60
# 输出每字段的读取次数、一致率、冲突率
```

模块：`state_bus.py`（融合与冲突）、`providers.py`（视觉/协议/合成）、
`test_state_bus.py`（9 条融合用例）。

---

## 五之三、链路启动顺序

每一步都独立，前一步挂了不影响后一步退化为"单通道"。

| 顺序 | 组件 | 命令 | 权限 |
|---|---|---|---|
| 0 | 自检 | `python src\pipeline_check.py` | 无 |
| 1 | BLE 常连 | `python <tmp>\me_daemon.py` | 无 |
| 2 | 视觉+决策 | `python -W ignore -u botsup.py --seconds 0` | 无 |
| 3 | 协议后端（可选） | `cd rocom-helper && py -m src.main` | **管理员** |
| 4 | 协议桥（可选） | `python src\proto_bridge.py` | 无 |
| 5 | 并行校验 | `python src\verify_channels.py --seconds 60` | 无 |

`pipeline_check.py` 输出 8 条链路的状态，当前预期：

```
game window        OK
game data          OK
damage engine      OK        worst incoming 462 dmg
policy weights     OK        15 keys from config
vision channel     OK        observe 328 ms
BLE daemon :9999   OK
protocol helper    DEGRADED  api not running（离线测试已过 352 项）
proto state file   DOWN      没启动 bridge
```

协议那两条 DOWN/DEGRADED 是正常状态——**它是可选增强，不是必需**。

### 协议层的离线验证结果（不需要游戏、不需要管理员）

```
tests/test_type_chart     105 passed
tests/test_crypto         （同批）
tests/test_frame          （同批）
tests/test_opcodes        （同批）
tests/test_damage_calc    199 passed
tests/test_tactical_engine（同批）
tests/test_battle_state   （同批）
tests/test_battle_replay   48 passed
------------------------------------
合计                     352 passed
```

也就是说 BE21 组帧、AES 解密、opcode 分发、状态机、伤害计算、回放在本机全部验证通过。
只差"实时抓包"这一步（需要管理员）。

---

## 六、路线图

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 | 胜负标签 + `winrate.py` | 能报出总胜率和分小时胜率 |
| P1 | 修卡片格点错位（1 号位 112/112 漏读） | 四张卡识别率 > 90% |
| P2 | `policy_v2` 接进 `robot.py` | 按 TTD/TKO 出招，不再"打不过还打" |
| P3 | 对面名字 OCR → 启用完整 TTD | 换人决策有数据依据 |
| P4 | 权重拟合（turns.jsonl 重放） | 胜率高于 P2 基线 |
| P5 | 抓包作为正式数据源 | 感知失败率 → 0 |

---

## 七、已知坑（踩过的）

1. **卡片格点错位**：`find_pills` 漏掉深色卡面的胶囊时，整列上移一格，
   1 号位读到的是 2 号位。必须改成"先找卡片、再找胶囊"。
2. **换人要按 E**：战斗中 `E → 数字 → 空格`，漏了 E 就等于按技能。
   强制换人面板不需要 E（已经开着）。
3. **换人面板只有 5 行**（不含当前出战），战斗队伍是 6 行，两套编号。
4. **聚能被断**：连按 F 会被对面打断，新宠满能量入场，连断两次就该换人。
5. **焦点**：按键发给前台窗口。丢焦点时必须丢弃动作并重试，
   否则会往浏览器里打字（09:38 实际发生过十分钟）。
6. **血量公式不可信**，必须用 HUD 实测值校准。
