# 抄作业计划：从「洛克PVP助手」吸收什么

> 对象：`C:\Users\Lenovo\Downloads\洛克PVP助手\`（GitHub HZ-KMNO/roco-sandbox 衍生，MIT）
> 前提：已确认它就是《洛克王国世界》PC 版助手（进程/标题/资产/编号四重验证）
> 原则：**只抄数据与思路，不抄第三方代码**（`rkpp_codec.py`/`rkpp_opcode_payload.py`
> 归 RocoMITMServer 作者；我们已有约定不复制无授权代码进 `src/`）

---

## 一、两边是什么

| | RocoAgent（我们） | 洛克PVP助手（它） |
|---|---|---|
| **形态** | 全自动对战机器人（截图→识别→决策→硬件 HID 出招） | 半自动辅助 HUD（抓包→协议解析→屏幕叠加显示） |
| **数据来源** | 屏幕截图 OCR/模板/ONNX | 网络抓包（0x4013 AES 解密）+ 官方 PAK 解包数据 |
| **自动化** | 完全自动打完整局 | 只显示，不出招 |
| **核心难点** | 感知（看得准） | 协议（抓得住） |
| **已知短板** | 胜率 29%，我方识别 58%→84% | 不出招、链路脆（要管理员+Npcap+抓 key） |

**互补关系**：它解决了我们最难的问题（感知精度），我们解决了它没有的问题（自动执行）。

---

## 二、逐项差距清单（它的 vs 我们的）

| # | 能力 | 它 | 我们 | 结论 |
|---|---|---|---|---|
| 1 | 官方技能表（1655 条，含 damage_type/效果） | ✅ PAK 直读 | 部分（roco-cal 转录） | **已导入** `rkpp-official/` |
| 2 | 技能→buff_group→buffbase 效果链 | ✅ 1037 条有完整链 | ❌ 我们只有 skill_result 占位 | **P0 抄**：正是 opponent_model 缺的 a2/a3 细分 |
| 3 | 官方技能池（精灵→可学技能 16828 条） | ✅ | ❌ 用 169 套玩家阵容统计 | **P0 抄**：`pet_skill` id=species_id 已验证 |
| 4 | buffbase 名称（2164 条，「物攻等级提升10」） | ✅ | ❌ 完全没有 | **P0 抄**：buff 识别的翻译字典 |
| 5 | 官方 priority 语义（5=基准） | ✅ | 误判过（已修正） | **已完成** |
| 6 | 官方伤害公式（fround 语义） | ✅ k0-damage.js | 数值一致，浮点未对齐 | **P1**：极端值才会差 1 点 |
| 7 | 抓包实时数据 | ✅ | ❌ | **暂缓**（链路脆，已与用户确认） |
| 8 | 属性等级 buff 量化（物攻+1=+10%） | ✅ buffbase 编号即语义 | ❌ HUD 只显示图标 | **P0 抄**：连「读 buff 图标」都可能不用做了 |
| 9 | 官方图鉴 893 条（身高/体重/进化） | ✅ | 部分 | **P2**：对战斗无用，暂缓 |
| 10 | 防御值取 min/max 区间（B1/B2 的 range 分支） | ✅ | 我们有 def_range 但公式分支不全 | **P2** |
| 11 | HUD 显示优化（多模式/血条/伤害百分比） | ✅ 成熟 | 有悬浮窗，够用 | 不抄（我们的 UI 自己的路线） |
| 12 | 战报回放/模拟空间/胜率统计 | ✅ | 有 review_turns/winrate | 不抄（已有等价物） |

---

## 三、执行计划

### P0：官方效果链导入（本周期，最高收益）

1. **导出 buff 链** `scripts/export_rkpp_effects.py`
   - `skill → skill_result[] → effect_id → buff(Group) → buff_base_ids → buffbase.editor_name`
   - 产物 `data/external/rkpp-official/skill_effects.json`
   - 预计覆盖 1037 个技能
2. **接进 `opponent_model`**
   - `a2 防御 / a3 状态` 的细分依据：效果的 buffbase 语义
     （「物攻等级提升」「回复」「能量」分类）
   - `foe_moves.note_observation()` 记录的技能自动标注类别
3. **接进 `policy_v2`**
   - 对面技能的 buff/回复/能量效果 → 期望值计算里加权重
     （现在只有伤害，没有"这招会给自己加 buff"的评估）

### P1：官方技能池接入 `pvp_meta`

- `pet_skill`（id=species_id）→ 「这只精灵可能带什么技能」
- 现有 169 套玩家阵容统计降级为「使用率」权重，技能池做全集
- 前端「可能技能」行显示官方池 + 玩家使用率

### P2：伤害公式浮点对齐（低优先）

- `k0-damage.js` 用 `Math.fround`（float32）逐步舍入
- 我们用 float64。极端数值才会差 1 点，对决策影响极小
- 做法：把 `statmodel.damage` 的中间量包 `struct` 转 float32

### 暂缓（明确不做，写明原因）

- **抓包实时流**：6 项环境依赖，key 每次登录要重抓，与我们常驻 supervisor 冲突。
  若将来做，先用它的 relay server 被动读 `/latest`（不自己抓）
- **HUD/UI 形态**：它是给人看的，我们给机器看，路线不同
- **图鉴身高体重**：与战斗无关

---

## 四、已确认的技术事实（探测记录）

1. `skill` 实体：1655 条，`skill_result[].effect_id` 指向 `buff` 实体
   （= buff_group），其 `buff_base_ids[]` 指向 `buffbase`（含 editor_name）
2. `buffbase.editor_name` 是**语义化中文**：「物攻等级提升10」「击杀获得6能量」
   —— 这就是 buff 识别的翻译字典，编号即语义（+10/-10 对应等级±1）
3. `pet_skill.id == species_id`（3071 音速犬 13 技、3296 卡瓦重 14 技）✅
4. 官方 `skill_priority`：5=基准、>5 先手、<5 后手（推翻昨晚"脏数据"误判）
5. 官方伤害公式与我们逐位一致（122==122 验证过），仅 float32 舍入差异
