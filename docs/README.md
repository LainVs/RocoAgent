# RocoAgent 文档索引

10 份活跃文档 + 11 份归档。**先看这里，再决定看哪份。**

---

## 按目的找

| 我想… | 看这份 |
|---|---|
| 了解整体架构 / 代码分层 | `ARCHITECTURE.md` |
| **改伤害/数值相关的东西** | `DAMAGE_MODEL.md`（必读，含删除依据） |
| 启动机器人、看日志、排障 | `STARTUP.md` |
| 搞清"为什么卡住不出招" | `TURN_FLOW.md` |
| 改检测（血条/技能卡/数字） | `DETECTION_V2.md`（含 59 条人工真值与失败记录） |
| 重新设计 UI | `UI_TODO.md`（含旧 UI 功能清单） |
| 找某个 src 文件是干什么的 | `SRC_INVENTORY.md` |
| 了解外部项目 / 待移植资产 | `REFERENCE_PROJECTS.md` |
| 项目定位与目标 | `PROJECT.md` |

---

## 文档之间的事实关系

```
ARCHITECTURE.md          整体结构（§零 代码分层 / §三 伤害模型速查）
  ├─ DAMAGE_MODEL.md     伤害：为什么只留一套、删了什么、依据
  ├─ DETECTION_V2.md     检测：材质优先、数字通道、真值评估
  ├─ TURN_FLOW.md        回合流转：三次卡顿的根因与修复
  └─ REFERENCE_PROJECTS.md  外部项目对照（9178 / roco-cal / …）

STARTUP.md               怎么跑起来（含崩溃排查）
SRC_INVENTORY.md         每个 src 文件的职责与归属
UI_TODO.md               UI 已移除，这是重做时的需求清单
PROJECT.md               定位与目标
README.md                本文件
```

**注意**：`ARCHITECTURE.md §三` 只放公式速查表，删除依据和实测证据在
`DAMAGE_MODEL.md`。两份不重复。

---

## 代码结构速查

```
src/
  # 核心循环
  robot.py           门面 + Bot 类 + 入口
  config.py          常量 / 日志 / 锁
  winio.py           窗口 / 焦点 / 点击 / Screen
  perceive.py        Perc / 技能栏 / 血条
  ui_probe.py        按钮 / VS 页 / 结算页 / 换人面板
  policy_legacy.py   旧 decide()
  policy_v2.py       期望值决策（当前主用）

  # 数值（只有一套）
  statmodel.py       唯一数值模型
  damage.py          纯数据层
  foe_hp.py          对手血量 / 承伤
  pvp_engine.py      TTD/TKO

  # 识别
  digitmat.py        血条数字字形
  matdet.py          材质检测
  foe_panel.py       特性面板
  ability_read.py    特性名 OCR
  trait_db.py        特性 → 物种 → 属性

scripts/
  verify_split.py    验证 robot.py 拆分是纯搬移
  fingerprint.py     字节码指纹
  backtest.py        离线回测
  eval_det.py        检测精度（唯一可信的精度报告）
  audit/             一次性数值审计（5 个，已跑完）
  repair/            编码事故修复（12 个，含 verify_equiv.py）
```

完整清单见 `SRC_INVENTORY.md`。

---

## 验证命令

```powershell
# 数值模型（不需要游戏）
python src\test_statmodel.py
python src\test_trait_db.py

# 拆分是否纯搬移（0 差异才算过）
python scripts\verify_split.py

# 检测精度（需要 logs/annot 里的人工真值）
python scripts\eval_det.py

# 队伍查看 / 编辑（原管理面板的功能）
python src\team_db.py
python src\team_db.py --set 1 --species 音速犬 --nature 固执
```

---

## 归档文档

`docs/archive/` 里有 11 份历史设计稿（换人决策四份研究稿、早期总体方案等）。
**不要照着归档文档改代码** —— 它们的结论已被实测取代或已落地。
为什么归档见 `docs/archive/README.md`。
