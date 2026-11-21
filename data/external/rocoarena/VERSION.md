# 外部资产：ROCO ARENA 天梯样本库

> 来源：https://rocoarena.com（玩家独立维护，非官方）
> 拉取日期：2026-11-21
> 数据量：13 组 / 15 个阵容 / 63 种精灵 / 520 个技能 / 63 个特性
> 许可证：**未声明**（只作数据参考，不复制其代码）

## 为什么拉进来

这是目前唯一能拿到**真实玩家配法多样性**的来源。

我们的 `foe_moves.py` 目前用"该精灵全部可学技能均匀分布"作先验，
但实际上玩家只带 4 个技能，且有明显偏好。这份数据给出了真实分布。

## 接口

```
GET /api/teams?summary=1&page=1&pageSize=100&source=site
    -> {groups, items, pagination, siteCount, teamTypeOptions}

GET /api/teams/<team_id>
    -> {team: {payload: {members: [...]}}}
```

- **无需登录**
- `page` 参数实际无效（第 2 页返回同样数据），真实分页在
  `pagination.pageCount`。实测 `pageCount: 1`，一页拉完。
- 响应 `cache-control: no-store`，要自己存快照
- 需要 Clash 代理（脚本用 `ROCO_PROXY` 环境变量，默认 `127.0.0.1:7897`）

## 每个成员的数据

```json
{
  "name": "音速犬（看守麦田的样子）",
  "baseStats": {"hp":85,"attack":116,"defense":101,
                "magicAttack":38,"magicDefense":82,"speed":120},
  "abilityName": "专注力",
  "abilityEffect": "入场首回合，获得物攻+100%。",
  "nature": "急躁",
  "ivStats": ["attack","hp","speed"],
  "enhancement": "boss",
  "catalogIdentity": {
    "petId": 3871,
    "skills": [
      {"gameSkillId": 7040420, "name": "灼伤"},
      {"gameSkillId": 7180290, "name": "跌落"},
      {"gameSkillId": 7020620, "name": "力量增效"},
      {"gameSkillId": 7060100, "name": "闪光冲击"}
    ]
  },
  "skillPool": [  // 该精灵完整技能池（含效果/威力/耗能/属性）
    {"name":"借用","power":0,"energyCost":0,"type":"普通",
     "category":"状态","effect":"每回合随机变成己方队伍中其他精灵的技能。"}
  ]
}
```

## 实测价值

### 1. 配法多样性（PIMC 的信念来源）

我们队伍的精灵，站点上别人的配法：

| 精灵 | 配法数 | 例子 |
|---|---|---|
| 音速犬 | 3 | 灼伤/跌落/力量增效/闪光冲击 |
| 卡瓦重 | 3 | 速冻/筛管奔流/晒太阳/冰雹 |
| 恶魔狼 | 3 | 蝙蝠/力量增效/吞噬/火焰切割 |
| 冰钻布鲁斯 | 1 | 丢冰块/火焰冲锋/先发制人/力量增效 |
| 银月狼王 | 0 | 站点未收录 |

对比我们的实盘配法：

| 精灵 | 我们 | 站点主流 |
|---|---|---|
| 音速犬 | 借用/火云车/诋毁/闪光冲击 | 灼伤/跌落/力量增效/闪光冲击 |
| 卡瓦重 | 筛管奔流/扬沙/虫击/冰雹 | 速冻/筛管奔流/晒太阳/冰雹 |

**只有「筛管奔流」「冰雹」「闪光冲击」是共通的**，其余都不同。
这说明对手配法差异很大，不能假设"对手带最优解"。

### 2. 克制矩阵交叉验证

站点的 `typeMultipliers` 是完整 18×18 表，
语义是 `typeMultipliers[守][攻]`（**注意方向**）。

与我们 `statmodel.type_effect(攻, [守])` 逐条对比：

```
324/324 完全一致，0 处差异
```

这是**第三份独立实现给出相同答案**（前两份：roco-cal、9178）。
克制表可以彻底放心了。

### 3. 暴露了我们库的脏数据

对比 520 个技能池：

| 项 | 数量 |
|---|---|
| 我们库有 | 519 |
| 我们库没有 | 1（「远行」） |
| 威力不一致 | 89 |
| 耗能不一致 | 23 |
| 属性不一致 | 79 |

大部分是我们库的脏数据：

```
同名技能重复：106 个名字 / 347 条记录
  「应对！愿力冲击」19 条
  「愿力冲击」18 条
  「赋予效果用技能」15 条
power <= 0：1034 条
cost = 0 但有威力（可疑）：80 条
```

**关键教训**：按技能名查表有风险。例「蝙蝠」有两条：

```
id=7180090  power=65  cost=2   <- 正确
id=7200000  power=100 cost=0   <- 脏数据
```

我们队伍用的是正确的那条，但**代码里必须按 id 查，不能按名字查**。

## 站点其他接口（未使用）

```
/api/library/metadata     性格表、克制矩阵、队型定义
/api/site/metadata        站点配置
/api/game/merchant        远行商人（每日刷新）
/api/community/topics     社区帖子
/api/site-version         站点版本
```

`/api/library/metadata` 的 `typeMultipliers` 已用于交叉验证。

## 更新方式

```powershell
& $py scripts\fetch_rocoarena.py
```

脚本会自动读 `pagination.pageCount`，不会重复拉。

## 已知限制

- 样本量小（15 个阵容），但配法多样性真实
- 「岩脉崩毁」「月蚀」「撞鬼」不在站点技能池里（我们队伍用的）
- 无 LICENSE，**只作数据参考，不复制代码**

## 致谢

数据来自 [ROCO ARENA](https://rocoarena.com)，玩家独立维护的工具站。
本项目仅用于个人研究。
