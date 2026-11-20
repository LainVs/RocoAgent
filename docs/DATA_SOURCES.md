# 数据源与清洗规则

> 最后更新：2026-11-21

## 一、数据源优先级

| 优先级 | 来源 | 位置 | 用途 |
|---|---|---|---|
| **1** | **roco-cal（S5）** | `data/external/roco-cal/` | 特性 / 属性 / 种族值 / 识别模型 / 伤害引擎 |
| 2 | RokePvP-Calculator | `D:\GameData\洛克王国世界\RokePvP-Calculator` | 交叉验证（与 roco-cal 一致 440/441） |
| 3 | 游戏快照（2026-04） | `D:\GameData\roco_data\game` | 技能池细节 / 特性描述 |
| 4 | 9178 计算器 | `data/spirits_9178.json` | 第三方仲裁 |
| **5** | **实测**（游戏里读到的） | `logs/` | **最高优先，覆盖一切** |

**为什么 roco-cal 排第一**：它 2026-11-20 刚更新、已同步 S5，
而且与 RokePvP 在 441 只可比精灵上互相一致 440 只 —— 两个独立项目
给出相同答案，可信度远高于我们的旧快照。

## 二、清洗规则

### 特性

```
roco-cal 有 且 与 RokePvP 一致  -> 覆盖
roco-cal 有 但 RokePvP 不同      -> 记 logs/data_clean_report.json，不覆盖
roco-cal 没有                    -> 保持原样
```

### 属性

```
roco-cal 有 -> 覆盖
```

### 种族值

分三类，保守处理：

| 情况 | 处理 |
|---|---|
| 我们是占位值（全 0 / 全 5 / 全 100 / 非数字） | **直接覆盖** |
| 我们与 roco-cal 不同，且 RokePvP 支持 roco-cal | **覆盖** |
| 其余差异 | **只记录**，人工裁决 |

### 缺失精灵

```
纯新精灵（名字不含括号）  -> 直接插入
形态变体（名字含括号）    -> 也插入，但视为独立条目（属性可能与本体不同）
```

> 例：`001圣光迪莫 [光]` vs `001圣水迪莫 [光,水]` —— 形态变体属性真的不同，
> 不能简单继承本体。

## 三、同名多条记录怎么选"真身"

**这是清洗时最容易出错的地方。**

实测同名会有 2-3 条（不同赛季 / 形态 / 占位）：

```
圣光迪莫  id=3048(最好伙伴) / 5025(伙伴的力量) / 103005(裁决)
千棘海针  id=4020(hp=0 占位) / 5012(hp=118) / 5029(hp=118)
冥古龙    id=3626(冥古龙) / 8013(怨恨) / 8017(恶龙的奢望)
```

`scripts/clean_gamedb_from_rocal.py::pick_best()` 的评分：

1. **种族值不是占位**（hp>0 且不全相同）—— 最重要
2. `implemented=1`
3. `total` 更大（数据更全）

按这个规则选出来的才和 roco-cal 对得上。

## 四、清洗结果（2026-11-21）

| 项 | 修正数 | 说明 |
|---|---|---|
| 特性 | 94 | 109 处差异中 15 处是形态变体比较，不算错 |
| 属性 | 22 | |
| 种族值 | 140 | 占位值 1 + 三方支持 139 |
| 存疑 | 21 | 只记录，见 `logs/data_clean_report.json` |
| 补精灵 | 218 | 纯新 50 + 形态 168 |

库内精灵总数：1024 → **1242**

特性反查覆盖率：57.0% → **62.6%**

### 典型修复

| 精灵 | 清洗前 | 清洗后 |
|---|---|---|
| 圣光迪莫 | 最好的伙伴 | **裁决** |
| 小丑豆豆 | 共鸣 | **戏耍** |
| 小雪人 | 共鸣 | **大雪球** |
| 凡雀 | 先锋 | **展翅** |
| 冥古龙 | 冥古龙（精灵名） | **恶龙的奢望** |
| 公平鸽 | 种族值全 0 | **[113,93,93,125,125,100]** |
| 千棘海针 | hp=0 | **hp=118** |
| 上锁 | 查不到 | **叮叮卯 / 飞飞钥（机械+虫）** |

## 五、审计与清洗脚本

```powershell
# 全库体检（只读）
python scripts\audit_gamedb.py

# 三方一致率矩阵
python scripts\audit_three_sources.py

# 克制表校验（结果应为 324/324 一致）
python scripts\audit_type_chart.py

# 清洗（先预览，再 --apply）
python scripts\clean_gamedb_from_rocal.py
python scripts\clean_gamedb_from_rocal.py --apply

# 补缺失精灵
python scripts\add_missing_species.py --apply
```

## 六、备份与回滚

清洗前备份：`data/roco.sqlite3.bak-20261121`（已 gitignore）

回滚：
```powershell
Copy-Item data\roco.sqlite3.bak-20261121 data\roco.sqlite3 -Force
```

或从 git 恢复：`git checkout <清洗前的提交> -- data/roco.sqlite3`

## 七、已知遗留问题

1. **21 处种族值存疑**：roco-cal 与 RokePvP 不一致，未覆盖。
   见 `logs/data_clean_report.json` 的 `stat_conflict`。
2. **1 处特性三方冲突**：同样在报告里。
3. **形态变体的属性**：已按 roco-cal 独立插入，但我们的头像/立绘识别
   只能给出形态名（如"丢丢（沙地附近的样子）"），需要确认
   `portrait_onnx` 的 620 类是否覆盖这些形态。
4. **`features` 表新增 27 个特性**来自 roco-cal，`desc` 字段是
   "（来自 roco-cal）"占位，需要后续补真实描述。
