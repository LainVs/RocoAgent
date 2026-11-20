# RocoAgent 文档索引

只保留 **7 份活跃文档**，其余在 `archive/`（历史设计稿，不要照着改代码）。

## 必读

| 文档 | 什么时候看 |
|---|---|
| `ARCHITECTURE.md` | 想了解整体结构、代码分层、数据源 |
| `DAMAGE_MODEL.md` | 想改伤害/数值相关的东西**之前必须看** |
| `STARTUP.md` | 启动机器人、看日志、排障 |

## 专题

| 文档 | 内容 |
|---|---|
| `DETECTION_V2.md` | 检测方案：材质优先、数字通道、实测精度 |
| `TURN_FLOW.md` | 回合流转：卡住不出招 / 第二手很久 / 大世界乱点的根因 |
| `REFERENCE_PROJECTS.md` | 外部项目调研（9178 / roco-cal / RocoNatureCalc）与待移植清单 |
| `PROJECT.md` | 项目定位与目标 |

## 代码结构速查

```
src/
  config.py          常量 / 日志 / 锁（所有模块共享）
  winio.py           窗口 / 焦点 / 点击 / Screen
  perceive.py        词表 / 指纹 / 卡片判定 / Perc / 技能栏
  ui_probe.py        按钮 / VS 页 / 结算页 / 换人面板
  policy_legacy.py   旧 decide()
  robot.py           门面 + Bot 类 + 入口

  statmodel.py       唯一数值模型（面板 / 伤害 / 克制 / 区间）
  damage.py          纯数据层（读 roco-claude 的 json）
  foe_hp.py          对手血量 / 承伤查询

  foe_panel.py       特性面板读取
  ability_read.py    特性名 OCR
  trait_db.py        特性 -> 物种 -> 属性反查
  digitmat.py        血条数字字形匹配
  matdet.py          材质检测器

scripts/
  audit/             一次性数值审计（5 个，已跑完）
  repair/            编码事故修复（12 个，含 verify_equiv.py）
  verify_split.py    验证 robot.py 拆分是纯搬移
  fingerprint.py     字节码指纹
  backtest.py        离线回测
  eval_det.py        检测精度评估（唯一可信的精度报告）
```

## 验证命令

```powershell
# 数值模型（不需要游戏）
python src\test_statmodel.py
python src\test_trait_db.py

# 拆分是否纯搬移
python scripts\verify_split.py

# 检测精度（需要 logs/annot 里的人工真值）
python scripts\eval_det.py
```
