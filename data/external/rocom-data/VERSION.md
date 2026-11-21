# 外部资产：rocom-data（数据 + 战术 YAML）

> 来源：https://github.com/AofeiLi-code/rocom-data
> 拉取日期：2026-11-21
> 许可证：**上游仓库未声明 LICENSE**
> 体积：10.28 MB / 37 个文件（其中 `.png` 不入库，实际入库约 8 MB）

## 许可边界

无 LICENSE，因此：

- ✅ 数据可作为参考/交叉校验
- ✅ **战术 YAML 可以借鉴思路**（但注明出处）
- ❌ 不要复制代码进 `src/`
- ❌ 不要二次分发

## 拉到了什么

```
data/external/rocom-data/
├── data/
│   ├── sprites.json        5.5 MB   精灵图鉴（全量）
│   ├── sprites.csv         1.8 MB   同上，CSV 版
│   ├── lineups.json        425 KB   169 套玩家阵容（含作者/简介/成员）
│   ├── lineups.csv         113 KB
│   ├── skills_all.csv       45 KB   技能表
│   ├── skills.csv           36 KB
│   ├── teams.json          5.8 KB   预设队伍（毒队/翼王队/…）
│   ├── urls.csv            181 KB   图鉴页面 URL 索引
│   ├── experience/         3 个     实战经验（沙暴队/狼王队/预设毒队）
│   └── strategies/         3 个     人工写的战术 YAML（见下）
├── battle.py                50 KB   终端战斗模拟器（**主要是菜单 UI，效果引擎很浅**）
├── db_manage.py            5.1 KB
└── docs/                            爬虫设计文档
```

**没拉到的**（GitHub API 限流 403）：`sim/`、`pvp/`、`game_control/`、
`import_images/`、`tests/`、`tools/`、`win_util/`、`lineup_scraper.py`、
`train.py`、`viewer.py`、`requirements.txt`。这些是代码，无 LICENSE 本来也不该复制。

## 最有价值的部分：战术 YAML

两条战术正好覆盖我们的精灵：

### `狼王队.yaml`

> 音速犬游走打点（**专注力特性使入场首回合物攻+100%**）
> 卡瓦重优先速冻挂减速印记
> 恶魔狼作为保底（**悲悯特性：队友倒下越多自身双攻越高**）

```
skill_priority:
  卡瓦重:   [速冻, 冰雹, 筛管奔流, 风矢]
  恶魔狼:   [先发制人, 火焰切割, 吞噬, 蝙蝠]
```

**这条"专注力入场首回合 +100%，打完即换"的游走思路，是我们没有用上的战术。**
我们的音速犬带的是 借用/火云车/诋毁/闪光冲击，正好可以套用"入场打一发高威力就换"。

### `平衡狼王队.yaml`

提到 卡瓦重、恶魔狼，思路是"先叠 buff 再传递"。

### `data/experience/*.json`

实战经验记录（沙暴/狼王/预设毒），可作为策略先验。

## `lineups.json`（169 套玩家阵容）

字段：

```json
{"id", "title", "type", "author", "author_link", "intro",
 "blood_magic", "uploaded_at", "last_updated", "source",
 "members": [...], "parse_status"}
```

用途：
- 统计**常见精灵/技能组合** → 对手行为模型的先验
- 反查"某精灵通常带什么技能" → `foe_moves.py` 的先验

## 和另外两份数据的关系

| | roco-cal | rocom-helper | rocom-data |
|---|---|---|---|
| 版本 | S5（最新） | 2026-04 | 2026-05 |
| 数据源 | 逻辑模拟 + wiki | BinData 解包 | BWIKI 爬取 |
| 强项 | 伤害引擎、特性分类 | buff/先天技能 | **玩家阵容 + 战术** |
| 许可 | MIT | 无 | 无 |

**冲突时以 roco-cal 为准。** rocom-data 的独特价值是**战术层面**
（阵容组合、技能优先级、换人规则），不是数值。

## 更新方式

```powershell
& $py scripts\gh_fetch.py AofeiLi-code/rocom-data data\external\rocom-data data docs
```

注意 GitHub API 未认证限额 60 次/小时，大仓库会中途 403。
缺的文件用 `scripts/raw_get.py` 单独补（raw 限额宽松得多）：

```powershell
& $py scripts\raw_get.py AofeiLi-code/rocom-data data\external\rocom-data <路径>
```

## 致谢

数据来自 [AofeiLi-code/rocom-data](https://github.com/AofeiLi-code/rocom-data)，
从洛克王国世界 BWIKI 爬取。本项目仅用于个人研究。
