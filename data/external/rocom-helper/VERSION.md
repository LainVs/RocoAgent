# 外部资产：rocom-helper（仅数据）

> 来源：https://github.com/innovationb1ue/rocom-helper
> 拉取日期：2026-11-21
> 上游数据生成时间：**2026-04-13**（manifest 里的 `generated_at`）
> 许可证：**上游仓库未声明 LICENSE**
> 体积：24.71 MB / 19 个文件

## 许可边界（重要）

上游**没有 LICENSE 文件**，因此：

- ✅ 可以作为**数据参考**在本地研究使用
- ❌ **不要复制它的代码进 `src/`**
- ❌ 不要二次分发

只拉取了 `data/game/`，没拉 `src/`。如果将来需要参考它的伤害 Hook 设计，
读代码可以，移植要重写实现。

## 为什么拉进来

这份数据里有我们**最缺**的几个维度：

| 文件 | 大小 | 我们之前有吗 |
|---|---|---|
| `buff_map.json` | 871 KB | ❌ 只有 `effects` 数字编号 |
| `buffbase_map.json` | 1.1 MB | ❌ 没有 |
| `special_move_map.json` | 841 KB | ❌ 没有 |
| `innate_skills.json` | 140 KB | ❌ 没有（先天技能） |
| `battle_config.json` | 71 KB | ❌ 没有（战斗参数） |
| `weather_map.json` | 6 KB | ❌ 没有（天气定义） |
| `type_chart.json` | 5 KB | ✅ 有，可交叉校验 |
| `pet_map.json` | 3.5 MB | ✅ 有，可交叉校验 |
| `skill_map.json` | 1.4 MB | ✅ 有，可交叉校验 |

数据来自**游戏 BinData 解包**（manifest 里 `source_kind: offline-import`），
不是爬 wiki，所以字段结构更接近游戏内部实现。

## 目录

```
data/external/rocom-helper/data/game/
├── attr_map.json             12 KB    属性定义（86 条）
├── battle_config.json        71 KB    战斗参数
├── buff_map.json            871 KB    Buff 定义（1925 条）
├── buffbase_map.json       1133 KB    Buff 基础定义（1879 条）
├── data_manifest.json        0.5 KB   清单 + 生成时间
├── evolution_map.json       384 KB    进化链
├── innate_skills.json       140 KB    先天技能
├── monster_map.json        7133 KB    怪物定义（11176 条）
├── nature_map.json           26 KB    性格表
├── opcode_pb_map.json       357 KB    协议 opcode（抓包用，我们不需要）
├── pb_message_index.json   1794 KB    协议消息索引（抓包用）
├── pet_map.json            3509 KB    精灵（6575 条）
├── pet_skill_map.json      3498 KB    精灵×技能（881 条映射）
├── pet_species.json        1722 KB    物种
├── proto_schema.json       2403 KB    Protobuf schema（抓包用）
├── skill_map.json          1397 KB    技能（1378 条）
├── special_move_map.json    841 KB    特殊招式（1443 条）
├── type_chart.json            5 KB    属性克制
└── weather_map.json           6 KB    天气
```

## 已知缺失

`data_manifest.json` 里列了 `monster_skillbank_map.json`（14588 条），
但**上游仓库里没有这个文件**（raw 返回 404），manifest 是过期的。

`opcode_pb_map.json` / `pb_message_index.json` / `proto_schema.json`
是抓包协议用的，我们不监听流量，**用不上**，共约 4.5 MB。
如果仓库体积紧张，这三个可以删。

## 和 roco-cal 的关系

| | roco-cal | rocom-helper |
|---|---|---|
| 数据来源 | 游戏逻辑模拟 + wiki | BinData 解包 |
| 版本 | **S5**（新） | 2026-04-13（较旧） |
| 强项 | 伤害引擎、S5 精灵、262 特性分类 | buff 定义、先天技能、特殊招式 |
| 许可 | MIT | 未声明 |

**冲突时以 roco-cal 为准**（用户口径：存疑的以 roco-cal 为准）。
rocom-helper 用来补 roco-cal 没有的维度（buff / innate / special_move）。

## 更新方式

```powershell
& $py scripts\gh_fetch.py innovationb1ue/rocom-helper data\external\rocom-helper data/game
```

脚本走 `api.github.com` contents 接口 + Clash 代理（`127.0.0.1:7897`）。

## 致谢

数据来自 [innovationb1ue/rocom-helper](https://github.com/innovationb1ue/rocom-helper)，
项目声明"仅被动分析、不修改客户端"。本项目仅用于个人研究。
