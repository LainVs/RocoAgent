# 模拟器实现状态（截至 2026-11-23）

> 这份文件是"我做到哪了"的唯一事实来源。每批改动后必须更新。
> 计划见 `docs/PLAN_FULL_SIM.md`，机制出处见那里。

## S20 校准表接进预测链路（2026-11-23）

- [x] `src/foe_calib.py`：k(foe_name) 加载 S19 校准表，未知对手 1.0
- [x] policy_v2.decide：skills dmg × k(foe)——kill/trade 阈值收紧
      （寂灭骨龙 300 伤害 ×0.34=102 不再误判必杀）
- [x] policy_v2.best_damage_from 同步缩放（换人路径估算）
- [x] 验证：decide why 字符串可见缩放（"102 dmg" = 300×0.34）；
      未知对手行为完全不变
- 测试：`src/test_s20_calib.py`（8 断言）；基线 15/15 零变化
- 测试坑备忘：`"kill" in why` 会命中 "s**kill**"——用 "kills"

## S19 实战伤害校准表（2026-11-23，commit e9a9242）

- [x] per-foe 承伤系数：40 观测（卡牌预览 V × foe_pct 差分）→
      21 对手校准（data/foe_calibration.json）
- [x] 精度：**残差中位 0%**、p75 29%——对比 S18 静态对账 MAE 87
- [x] 寂灭骨龙 k=0.34 印证 S18b 的 ~66% 减伤结论
- [x] 机器人用法：`预测 = 卡牌预览 × k(对手)`（docs/FOE_CALIBRATION.md）
- [x] 局限：k 是乘积校准（max_hp×减伤×防御，不可分离）；单样本无置信度

## S18 实战对账（2026-11-23，commit ea3cb47 / 6e143c3）

- [x] 静态对账：61 回合（卡牌预览 vs calc_damage）——比值聚集在
      干净倍数（1.0/1.25/2.0/4.0）→ **伤害公式验证通过**
- [x] 卡牌预览含实时战斗状态（毛毛减伤 13× 偏差）→ 精确对账需
      战斗状态回放
- [x] 回放骨架：`scripts/replay_validate.py`（1v1 样本 5 回合），
      剩余误差源 = 对方面板/状态未知 → S19 校准表解决

## S17 手操 UI 接入（2026-11-23，commit 2a4aa38）

- [x] **evolve 动作**：act({"kind":"evolve","i":N})——leader_forms
      血脉分支校验，payload 技能照常出手
- [x] **state 展示**：evolution {used, usable}、magic.usable 排除
      首领血脉、_pet_json 加 bloodline/allowed_slots/cond_mods
- 测试：`src/test_s17_ui.py`（10 断言）；基线 15/15 零变化
- 计划：docs/PLAN_S17_UI.md

## S16 条件型效果迷你引擎（2026-11-23，commit 43e4aa0）

- [x] link 层解析 HP 阈值条件：云行雨施（hp>50% 伤害+30% / hp<50%
      受伤-30%）、入梦（hp<50% 全属性+20%）——buff_desc 新旧口径都支持
- [x] Battle 消费：增益技挂 `pet._cond_mods`，calc_damage 按 hp 比例
      动态乘算（攻方 dealt / 守方 taken）
- [x] **全机制集成回归**：6v6 30 回合多机制混跑（血脉/魔法/应对/
      脱离/首领血脉）无崩溃——`test_s16_cond.py`
- [x] 计划：docs/PLAN_S16_COND.md
- 基线 15/15 零变化；15 个测试文件全 PASS
- 延后：强化等级阈值特性（恶龙的奢欲 3 条，等级口径需实测）

## S15 应对技效果引擎（2026-11-23，commit 4646c21）

- [x] 153 个应对技效果子句解析（按**命中类别**取子句——desc 可含
      多个应对子句，跨子句串扰已修复：放晴"永久+50% vs 改为永久+100%"）
- [x] 9 个 ctx 原语：self_buff / enemy_debuff / power_mult(乘法) /
      perm_power / enemy_energy_loss / refund_cost / force_enemy_leave /
      interrupt / enemy_cost_up
- [x] Battle 消费：add_buff 负值直写（add_buff 钳 ≥0 的坑）、
      应对威力乘法通道（与闪焰爆发共用）、能量返还、敌方脱离
- [x] 计划：docs/PLAN_S15_COUNTER.md
- 测试：`src/test_s15_counter.py`（10 断言）；基线 15/15 零变化
- 明确不做：下回合只能用被应对技（临时槽锁）、速度+160（口径未证）、
  冷却+1/能耗永久-N（CD 系统未建模）、立即使用被应对技（技能复制）

## S14 authority 链路第 4 层（2026-11-23，commit 660f7b1）

- [x] **desc_effects 4 层结构**：desc_fx(手写) > authority_fx(180技) >
      **link_fx(S14 新，214 技)** > 无
- [x] skill_links→buff_desc 结构化解析：物攻/魔攻/物防/魔防±X%
      （max 合并）、冻结/中毒/灼烧状态、能量回复/吸取；富文本剥标签
- [x] results 为空的技能解析自身 desc（徒长=回复10能量）
- [x] **净新增 108 技**（其中 85 个是此前零覆盖的 power=0 技能）
- [x] 零新增消费代码（dv 键全部复用 Battle 既有消费点）
- [x] 计划：docs/PLAN_S14_LINKS.md（含放弃 desc 直打正则的探测记录）
- 测试：`src/test_s14_links.py`（8 断言）；基线 15/15 零变化
- 明确不做：条件型（生命低于50%时…）、连击/眩晕/萌化/随机、
  应对技效果部分（需 counter 时点消费）

## S13 槽位锁/自动脱离/应对变身（2026-11-23，commit 8a078b7）

- [x] **SKILL_SLOT_LOCK**：正位宝剑 [0] / 宝剑王牌 [0,2]——锁外槽
      回落第一个允许槽（🟡 假设：游戏内不可选）
- [x] **AUTO_SWITCH**：警惕（能量0脱离）/防过载保护（每回合脱离）/
      奔波鬃（用防御技后脱离）/星地善良（替补 holder 替换能量耗尽者）
      ——复用 S6.10 脱离路径，turn-end 每侧一次
- [x] **COUNTER_ACCUMULATE_TRANSFORM**：保卫（防御技应对×2）/
      腾挪（攻击技应对×1）/好象坏象（状态技应对×1）→ 回满状态 +
      变身棋绮后（白/黑子按载体名），复用 Pet.rebuild_as
- [x] 语义澄清：category_filter 匹配**我方应对技**类别（ctx["my_category"]），
      与被应对方类别（ctx["category"]）分开
- [x] HANDLERS 35 个；ON_BE_KILLED 时机注册（S12 遗留补齐）
- 测试：`src/test_s13_slots.py`（13 断言）；基线 15/15 零变化
- 计划：docs/PLAN_S13_SWITCH.md

## S12 ABILITY_COMPUTE 开场技能改写（2026-11-23）

- [x] 6 特性：溶解扩散（毒系计数→水系附加中毒）、不移（纯攻击+30%）、
      快锤（能耗<3 迅捷）、暴食（龙系迅捷）、冻土（冰系计数→地系威力）、
      飓风（翼系共享迅捷 + 被击败魔力-1）
- [x] 幂等设计：begin_turn 重触发三时机，`_ability_done` 指纹去重
- [x] ON_BE_KILLED 时机新增；ENEMY_LOSE_ENERGY 支持 target=self_mp
- [x] HANDLERS 32 个
- [x] 计划：docs/PLAN_S12_COMPUTE.md
- 测试：`src/test_s12_compute.py`（12 断言）；基线 15/15 零变化

## S11 nrc 原语扩展第二批（2026-11-23）

- [x] 7 新原语 / 10 特性：向心力（威力+30）、坠星/观星（星陨印记
      威力+15%/层）、加个雪球/捉迷藏（冻结附加+2）、浸润/浪潮
      （水系能耗-1/-2）、煤渣草（灼烧不衰减）、蚀刻（中毒→印记）、
      付给恶魔的赎价（击杀/被击败魔力-1）
- [x] **引擎修复**：ON_FAINT 从未触发（诈死挂空）——已在击杀块补上
- [x] HANDLERS 30 个
- [x] 延后（PLAN_S11_NRC.md）：ABILITY_COMPUTE(6)、
      COUNTER_ACCUMULATE_TRANSFORM(3)、AUTO_SWITCH(4)、SKILL_SLOT_LOCK(2)、
      身经百练、乘风连击
- [x] **覆盖率口径修正**：之前 68% 混入"数据存在"（nrc 有条目≠已实现）。
      实现口径（trait_lib 支持 或 nrc timing+handler 全部可消费）：
      **82/289 = 28%**（S7.7 前为 65）。诚实数字，后续按此口径报告。
- 测试：`src/test_s11_traits.py`（17 断言）；基线 15/15 零变化

## S10 进化之力（首领化）（2026-11-23，commit 8e55f89）

- [x] **首领化**：首领血脉 + evolve 动作 → 当场换形态
      （species/特性/六维重算，血量比例保留，cfg max_hp 面板优先）
- [x] 映射表 `data/leader_forms.json`：迪莫（光/草血脉分支→圣光/圣草）、
      火神→烈火战神、喵喵→叶冕魔力猫（均核对 species 表）
- [x] 每场 1 次；非首领血脉落空；首领化=进场（ON_ENTER 重触发）
- [x] 愿力强化对首领血脉落空（rocoduel 原文）
- [x] 进化之力不占当回合行动（🟡 道具语义假设，见 PLAN_S10_EVOLVE.md）
- [x] **fix(nrc_fx)**：`_apply_buff_pair` 物攻+魔攻相加导致双重计数
      （0.3+0.3=0.6），改 max()——助燃/爆燃/鼓气类增益全部修正
- [x] 基线 6/15 行变化（火神助燃 0.4→0.2 修正，已重拍锁定）
- 测试：`src/test_s10_evolve.py`（17 断言）

## S9 共鸣魔法补全（2026-11-23，commit 09568e2）

- [x] **光合治愈**：草系或草血脉 → 回复 50% max_hp
- [x] **闪焰爆发**：火系或火血脉 → 本回合威力 ×2.5（乘法通道，
      与应对状态 +150% 加法通道并存——口径来自两处原文写法差异）
- [x] **节流术**：水系或水血脉 → 本回合免能耗
- [x] 行动模型：施放当回合精灵照常出手（payload = 技能下标）
- [x] 目标不符 → 落空不扣次数；CD3/2次与愿力强化对称（🟡 假设）
- [x] 手操 UI：magic 动作带 payload + team_magic_a 参数
- [x] 计划：docs/PLAN_S9_MAGIC.md（含 🟡 假设标注）
- 测试：`src/test_s9_magic.py`（17 断言）；基线 15/15 零变化

## S8 血脉系统完整链路（2026-11-23，commit 2fe6980）

- [x] **damage_type 四类伤害路径**：1 变化 / 2 物理(atk/def) /
      3 魔法(spa/spd) / 4 动态双攻取高（rkpp 权威数据，
      与 roco-cal 物攻/魔攻 346 技零冲突）
- [x] **血脉门控**：`species_skills.source='blood'`（533 精灵 × 18 系），
      blood 行 type_id = 学习所需血脉；cfg["bloodline"] 配置，
      不符的技能剔除（一种血脉一个技能，用户确认）
- [x] **愿力强化共鸣魔法**：替换第一格为血脉愿力冲击，CD 3、每场 2 次；
      手操 UI magic 动作；我方默认携带，血脉缺省回退主系
- [x] **应对状态威力 +150%**：desc 通用解析（80→200）
- [x] **裁决/滋养/点燃/净化**：克制伤害 → 攻防速+20%/层、能量+2、
      第一格替换为对应系愿力冲击
- [x] **my_team.bloodline 列** + `--bloodline` CLI
- [x] 基线 9/15 行变化（dt=3 魔攻路径 + 血脉门控，均保真度修正）
- 测试：`src/test_s8_blood.py`（15 断言）

## 已实现（能跑、有测试）

| 模块 | 内容 | 文件 |
|---|---|---|
| 伤害公式 | 官方 `effective_power × 攻 × 37 / (41 × 防)`；外部标定 114/91 已对上 | `simulator.calc_damage`、`damage_v2` |
| 属性克制 | 双克 3 倍（加算）、双抗 0.25（乘算） | `statmodel.type_effect` |
| 强化/增益 | 每层 +10% 线性叠加，10 层封顶 +100% | `simulator.add_buff` |
| 克制/等级项 | `level_mult` = 1+0.1n（用户实测） | `statmodel.level_mult` |
| 天气 | 5 种（雨天水系 ×1.75；其余为标记） | `battle_fx` |
| 印记 | 4 种（攻击/蓄势/风起/蓄电），手填层数 | `battle_fx` |
| 特性 | roco-cal 261 条里可用 18 条 + nrc-ai 169 条里可用的部分 | `trait_lib`、`nrc_fx` |
| 变化技能 | 攻击/防御/减伤百分比（53 个技能，官方数值） | `skill_buff_calc.json` |
| 异常状态 | 中毒/灼烧（数值原为占位） | `nrc_fx.status_tick` |
| 应对 | 类别判定 + 触发应对效果 | `nrc_fx.counter_fire` |
| 手操对战 | 双立绘 + 逐回合战报 + 字面伤害 + 记录分析 | `manual_battle`、`page-play` |
| 图形资源 | 639 头像 + 584 立绘 + 战斗背景 | `assets/pet_*`、`assets/battle` |

## 本轮（S3）已做

- [x] 印记全集 13 种（正 8 / 负 5），同类最多一个（新顶旧）
- [x] 威力/能耗/速度 修正（攻击+10%、蓄势+30%且能耗+1、湿润-1、减速-10）
- [x] 回合末触发（光合 +1 能量、中毒印记 3%）
- [x] 入场触发（棘刺 -6% 生命、降灵 -1 能量、暗涌 5 层减益）
- [x] 用技能后触发（龙噬：3 能耗技能后双攻 +30%）
- [x] 旧 UI 的 `xxx_stacks` 手填接口自动翻译到新印记 id

## 本轮（S4，进行中）已做

- [x] 技能描述文本接入（`simulator._skill_desc`，懒加载全表）
- [x] **吸血**：从描述解析比例（「吸血100%」「吸血30%」），与特性吸血相加，
      按实际伤害回血 —— 14 个吸血技能全部生效
- [x] **先手**：解析「先手 +N」，出手排序第一级应对、第二级先手度、第三级速度
      （实测低速方带「先发制人」+1 可以抢在高速度方前面出手）

## 本轮（S6 第二层修正）

- [x] **悲悯/悼亡按原文修正**（用户指出「+60%」可疑）：
      timing=current 持续状态（不是入场一次性）、每只力竭 +30%（双攻是
      物攻/魔攻两个面向，不是相加）、回到 roco 层不再走 nrc
- [x] 新增 `trait_lib.dead_buffs()` + Battle 每回合重算
- [x] S6 第二层 handler：ON_KILL_BUFF / FAINTED_ALLIES_BUFF /
      ENEMY_LOSE_ENERGY / FREEZE / PARASITE / COUNTER_SUCCESS_DOUBLE_DAMAGE
- [x] 名字模糊匹配（恶魔的晚餐 vs nrc 恶魔的晚宴）

## 本轮（S6 审计：用户「一个一个弄好」要求，22 条已生效规则逐条核对）

- [x] **全神贯注**（风暴战犬）：原文「入场物攻+100%，每次行动后-20%」——
      原来只给 +20% 且无衰减。已修：入场 tmp_atk=1.0，每次行动 -0.2
- [x] **光度换算**：roco-cal 把数值挂在了 fire_skill（方向反了）。
      按原文修正：光系威力 +30（flat）、火系使用后自伤 15%（新通道）
- [x] **变形活画**：敌方每层增益 → 威力+10%/本次、速度+5/层（已实现）
- [x] **烈冰钻**：敌方总能耗 >25 时速度 +75（已实现，走 _trait_speed）
- [ ] 贪得无厌的过量回复转化：低频场景，记录在未实现表
- [x] 新增 `_trait_speed`（特性速度加成通道，eff_spe 里计入）

## 本轮（S6.5 补充）

- [x] **条件型状态施加**（灵魂灼伤/溶解扩散/溶解腐蚀）：按技能系别选状态
- [x] **萌化（第 5 状态）**：化茧免疫致命伤+1层、自由自在连击+2、
      每层-10%全属性（社区口径近似，docstring 记录假设）
- [x] **蓄力技能集合**（24 技，跨回合状态机前置）
- [x] 灼烧完整生命周期验证（挂4层→-27→2层→-13→1层）

## 本轮（S6.6：用户实测三问题）

- [x] **灵魂灼伤是特性**（尖嘴狐仙家族）不是技能 —— skills 表 252 条
      features id 混入行，241 条移除并备份
- [x] **专注力「入场」可轮转**：换下再换上第一回合照样翻倍
      （clear_buffs 重置入场标记）
- [x] **状态播报中文化**（poison→中毒 等）
- [x] **手操：精灵死亡不自动换人**，等玩家手动选（forced_switch 状态机）
- [x] 悲悯/光度换算特判被编辑冲突覆盖，test_s6_audit 抓到后补回

## 本轮（S6.7）

- [x] **蓄力状态机**（24 技）：charging=1 本回合不攻击，下回合释放；
      蓄力期间非完成技不上手
- [x] **迸发**（10 技）：首行动加成（威力+30 / ×1.5 倍），实测差 33≈+30
- [x] **打断**（15 技）：技能集合就绪，应对联动下一步
- [x] cond_fx.py 合并 S6.5+S6.7（覆盖事故修复）

## 本轮（S6.9）

- [x] **迅捷**（8 技）：换人入场自动出第一个能量满足的迅捷技
      （实测冰钻布鲁斯换上带翼击立即出手）
- [x] 脱离技集合就绪（13 技），引擎路径下一批

## 本轮（S6.10）

- [x] **脱离**（13 技）：使用后走换人路径（clear_buffs + ia 切替补 +
      mark_switch_in 联动），播报 tag 在 owner 替换前记录
- [x] `battle_fx.leave_skills()`：按 desc 特征构建集合（不做名字字面量
      匹配——潜水遁是 4 字名，字面量易漏字）
- [x] 引用测试：脱离者不掉血、替补吃攻击 ✓

## 本轮（S6.11）

- [x] **能耗操纵**（骑士敕令/瞬间零度 4 技）：应对成功 → 对方下回合
      技能能耗+N（跨回合持久化，eff_cost 计入，实测升龙 3→6）
- [x] counter_fire desc 回退（这类技不在 nrc 表，类别和数值在 desc）
- [x] cond_fx.py 重建（S6.7 覆盖事故，S6.5+6.7 合并）

## 本轮（S6.12）

- [x] **返场**（3 技）：使敌方返场 → 敌方增益/减益清空（实测 0.5→0.00）
- [x] **衡量**（特性）：入场复制敌方增益（实测 0.6/0.3 ✓）
- [x] **传动槽位模型**：Pet.skill_slots + 循环移动 ✓；位置相关威力
      需 Battle 出手查槽位，下一批
- [x] **听桥**原文语义确认（威力镜像），实现下一批

## 本轮（S6.13）

- [x] **传动位置威力**（5 技）：械斗1号位+60 / 离子震荡3号位+40 /
      磁暴1,3号位+30 全部命中
- [x] **听桥威力镜像**：应对攻击成功 → 威力=对方技能威力
      （实测镜像升龙咆哮 200 ✓）
- [x] **奉献队伍计数**：devotion 计数器 + 威力+20/次（飞断实测）

## 本轮（S7）

- [x] **authority_effects.json** 入库：官方完整效果链路
      （buff 3352 条 / skill 2032 条 / 有链路的技能 1205 个 /
      10 种状态类 buff：中毒/灼烧/冻结/寄生/萌化/眩晕/引电/禁足/湿润/星陨）
  · 来源：authority/buff.json + skill.json（rocom-parse JSON 子集）
  · skill_links 技能名 → effect_id → buff 完整链路
  · status_buffs 状态类 buff 的 id/desc 清单
  · 数据入库（445 KB），后续引擎消费不需要 Downloads 目录

## 本轮（S6.14）

- [x] **星陨印记**补入 MARK_TABLE（14 种全集）
- [x] **龙噬印记**双攻 +30% → **+40%**（authority 权威值修正）
- [x] **引电/禁足**补入 STATUS_RULES（authority 权威定义）
- [x] test_battle_fx 印记数和龙噬数值断言对齐

## 本轮（S7.2）

- [x] **authority_consumable.json**（180 技新增效果）：从 authority
      buff_desc 正则抽取，与 desc_fx（176 技）合并后覆盖 356 技
- [x] desc_effects 合并（authority 优先，desc 补充）
- [x] heal_pct/buff_atk/buff_def 新 handler 字段（引擎待接）

## 本轮（S7.3）

- [x] **heal_pct**（38 技）接入引擎：desc_effects 消费路径直接回血
- [x] **buff_atk/buff_def**（67+31 技）接入引擎：add_buff 线性叠加
- [x] **虚拟技能条目**：resolve 失败但 desc_fx 有效果的技能构造
      power=0 虚拟条目（氧循环 heal 回复 0 根因修复）

## 本轮（S6.15）

- [x] **180 技全量分类**：真技能 113 / 真特性 50 / 两表都有 4 / 都没有 13
- [x] **50 条特性** → `data/traits_text_derived.json`（trait_lib 补充表）
- [x] **113 条真技能**保留 authority_consumable（走 desc_effects）
- [x] `trait_lib._load()` 读两个文件（主表 + 补充表）
- [x] 确认 species() **没有 bug**（是 PowerShell 字面量编码陷阱：魔力喵
      U+732B vs 魔力咪 U+55B5 是不同精灵）

## 本轮（S6.16）

- [x] **traits_text_derived 特性规则端到端生效**：
      氧循环 heal_pct 回合末回复（魔力喵水刃 → hp 3000→3500 ✓）
- [x] trait_lib._load 覆盖条件修正（主表无**规则**时用补充表，非"无条目"）
- [x] Battle.step 回合末加 heal_pct 消费
- [x] SUPPORTED 白名单加 (turn_end,always)/heal_pct + (ON_USE_SKILL,always)/stat_buff

## 本轮（S7.4）

- [x] **buff_fx_index.json**（177 条 buff 效果索引）：buff.json desc
      正则抽取，任何 effect_id 都能查到具体效果数值

## 本轮（S7.5）

- [x] **手操开局选首发**（pick_starter）：new() 不自动 begin_turn，
      state 返回 pick_starter=True，玩家 act({"kind":"starter","i":N})
      后进入第一回合。skip_starter=True 跳过（批量测试用）。

## 本轮（S6.13 补丁）

- [x] **听桥镜像块位置修正**：从 acts.append 之后挪到之前——
      镜像威力 200 现在正常产生伤害（-431 而不是占位 1 点）

## 本轮（S7.6）

- [x] **buff_fx_index 42 条特性 → trait 层**（traits_text_derived +20 条）
- [x] trait_lib 覆盖更新：traits 268 / with_rules 128 / usable 30
- [x] authority_consumable 移除特性条目 3 条（剩 127 技）

## 本轮（S7.7）

- [x] **STEAL_ALL_ENEMY_ENERGY**：按 nrc 数据 amount=N 每回合偷 N 点
      （毒蘑菇 amount=1，幻影灵菇端到端验证；修掉了早期"偷全部"的错误语义）
- [x] **ON_SKILL_ELEMENT_BUFF**：ctx["mv_type"] 过滤系别
      （助燃/爆燃：火系技能后双攻 +20%/+30%；非火系不触发）
- [x] **ENERGY_COST_CONDITION_BUFF**：ctx["mv_cost"] 过滤能耗
      （鼓气/三鼓作气：能耗3技能后双攻双防 +20%；能耗2不触发）
- [x] Battle.step 的 ON_USE_SKILL ctx 携带 mv_type/mv_cost
- [x] nrc HANDLERS 25 个

## 未实现（按批次）

| 批次 | 缺什么 | 卡在哪 |
|---|---|---|
| S3 | 印记 13 种全集、正/负面各 1 条约束、印记触发时机 | ✅ 完成（test_battle_fx） |
| S4 | 吸血 ✅ / 先手 ✅；迸发/蓄力/打断/传动/过载/巧变/选择 | 需要"跨回合状态机"（蓄力）、技能槽位模型（传动） |
| S5 | 4 格生命制胜负 ✅、场地亲和【强】±50 ✅（【弱】档无公开数据，未硬造） | 剩【弱】档 + 「无畏」已实现 |
| S6 | 特性全量 | 🔄 17 个原语 handler；roco 18 条 + nrc 悲悯/晚宴/小偷小摸/圣火骑士等约 40 条特性已生效 |
| S7 | 解包数据接驳 | 需要跑 rocom-parse（dotnet + CUE4Parse 分支） |

## 数据源速查

```
data/external/nrc-ai/            手写技能/特性语义（MIT）
data/external/roco-cal/          结构化特性、天气/印记、伤害引擎、S5 技能表
data/external/rocom-data/        169 套真实阵容（无 LICENSE，只取数据）
data/external/rocoarena/         15 套天梯快照
data/external/rkpp-official/     官方数据包（技能/buff/使用率）
data/nrc_effects.json            nrc-ai 导出（139 技能 / 169 特性）
data/traits.json                 roco-cal 特性 + 3 条文本推导
data/s5_skills.json              S5 每只精灵的完整技能表
data/skill_buff_calc.json        53 个变化技能的官方增益
```

## 测试基线（每次改完必须全绿）

```
test_imports / test_statmodel / test_decide / test_policy_cost
test_perceive / test_switch_panel / test_sim_engine
test_damage_model / test_traits / test_nrc_effects
```

黄金基线：`data/fixtures/sim_baseline.json`（15 行，引擎行为锁死）。
**改引擎后如果基线变了，必须先解释每一行变化，再重拍。**
