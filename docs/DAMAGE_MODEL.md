# 伤害模型：只留一套（2026-11-20 定案）

> 结论先行：**`statmodel.py` 是唯一的数值模型**。其他实现已删除，
> 删除理由都有实测数据支撑，不是"看着重复就删"。

---

## 一、之前有几套，各自错在哪

拆分前仓库里有 6 套伤害/面板实现，其中 4 套还在被调用：

| 模块 | 面板值 | 伤害公式 | STAB | 属性克制 | 结论 |
|---|---|---|---|---|---|
| `statmodel.py` | ✅ 对过 4/4 实测 | `攻/防×37/41×威力×…` | 参数传入 | 参数传入 | **保留** |
| `damage.py` | ✅ 与上等价 | 同上 | 参数传入 | 加法规则 ✅ | 降级为数据层 |
| `pvp_engine.py` | 调用 damage | 调用 damage | — | — | 保留（TTD/TKO 逻辑） |
| `foe_damage.py` | ✅ | 独立一套 | 1.25 ✅ | ❌ **相乘** | **删除** |
| `dmg_calc.py` | — | `威力×k×…` | ❌ **1.2（错）** | ✅ 加法 | **删除** |
| `sim.py` | — | 与 dmg_calc 同源 | 参数传入 | — | **删除**（0 引用） |
| `pet_state.py` | — | `damage_ratio()` | — | — | **删除**（0 引用） |
| `policy.py` | — | — | — | — | **删除**（0 引用） |

---

## 二、删除依据（都是实测）

### 2.1 STAB 真值是 1.25，不是 1.2

两张已知卡面同时成立才算数：

```
隼鳞   龙 140 本系 → 卡面 175     140 × 1.25 = 175    ✅
                                  140 × 1.20 = 168    ❌
蝙蝠   恶  65 本系 → 卡面 154      65 × 1.8954 × 1.25 = 154  ✅
                                  65 × 1.897  × 1.20 = 148  ❌
```

`dmg_calc.STAB = 1.2` 会让 隼鳞 预测成 332（卡面 175，**+89.7% 误差**）。
它自己的注释里也承认 `蝙蝠 154` 有 +4% 偏差 —— 那个偏差正是 STAB 用错造成的。

### 2.2 克制是"加法"，不是"相乘"

三份独立实现一致（9178 `getTypeEff`、roco-cal `ele_advantage.py`、
loyshowdown 实机验证）：

```
克制相加：被 1 个属性克 ×2，被 2 个 ×3（不是 ×4）
抵抗相乘：被 1 个抵抗 ×0.5，被 2 个 ×0.25
一克一抗 ×1
```

`foe_damage.type_mult` 写的是 `m *= mult`，双弱点会算成 **4.0**，
对双弱点高估 **33%**。这直接影响"我会不会被秒"的判断（高估被秒风险）。

### 2.3 卡面数字是"伤害"，不是"有效威力"

用 `logs/turns.jsonl` 里 98 个干净回合（同一对手、回合序号 +1、只出手一次、
血量确实下降）做判别：

| 假设 | 检验方法 | 命中 |
|---|---|---|
| 卡面 = 伤害 | 掉血% ≈ 卡面 / 对面最大血 | **9** |
| 卡面 = 有效威力 | 掉血% ≈ 卡面 × 1.897 / 对面最大血 | **0** |

所以卡面就是伤害。这否定了 `dmg_calc` 的"卡面是威力×k"的模型。

> 顺带修正一个假阳性：`pvp_engine` 的 selftest 用
> `D.estimate("隼鳞", "寂灭骨龙", "水灵")["shown"] == 175` 来验证，
> 但 `shown` 是**有效威力**（140×1.25=175），恰好等于卡面，属于巧合通过。
> 同一个函数的 `dmg` 字段是 196，与卡面 175 差 12%。

---

## 三、`statmodel.py` 为什么可信

它是**原样移植**自 9178 伤害计算器（朋友的，1:1 PvP 调参验证过），
只做移植不改公式。验收标准是四只实测血量：

| 精灵 | 种族值 | 个体 | 性格 | 模型 | 实测 |
|---|---|---|---|---|---|
| 音速犬 | 85 | 60 | 无 | 366 | 366 |
| 冰钻布鲁斯 | 73 | 60 | 无 | 345 | 345 |
| 寂灭骨龙 | 120 | 60 | +生命 | 490 | 490 |
| 巨鼓象 | 130 | 60 | +生命 | 510 | 510 |

回归脚本：`python src/test_statmodel.py`（不需要游戏）。

> 这四只以前被写成"公式不可信、必须实测"，其实是误判：
> 前两只是没点满 HP，后两只是生命性格 +20%。其中巨鼓象还牵出库里
> 一个占位值（`hp=66`，应为 130）。

---

## 四、数据源为什么保留两套

`statmodel` 从 `data/roco.sqlite3` 读，`damage.py` 从
`D:\GameData\...\roco-claude\data\raw\pokemon.json` 读。**两者不能合并**：

| 对比项 | 实测结果 |
|---|---|
| 种族值 | 290 只一致，**74 只冲突**（9178 仲裁：支持 DB 21 / 支持 json 30 / 都不对 23） |
| 技能池 | json 有 **99 只**宠物的技能 DB 里没有（`均势` 这个技能 DB 完全没有） |
| 覆盖 | DB 缺 json 的 135 条（其中 99 条是形态变体） |

所以 `damage.py` 保留为纯数据访问层（只读 json），公式全部走 `statmodel`。
`foe_hp.py` 是唯一的"对手血量/承伤"查询入口，它也只调 `statmodel`。

---

## 五、本轮顺带修的 bug

`pvp_engine.best_damage_from` **根本不存在**，但 `robot._best_damage`
一直在调用它：

```python
# robot.py（修复前）
def _best_damage(self, name, foe):
    import pvp_engine as E
    return E.best_damage_from(name, foe)     # AttributeError
```

调用方的 `except Exception: continue` 把异常吞了，导致
`_pick_switch_slot` 的 `best` 永远是 `None`，**换人永远退化成"第一个活的"**。

线上证据（`logs/bot_stdout.log`，6 次）：

```
10:48:51 FAINT -> switch slot 1 (no model data, first living, attempt 0)
10:49:06 FAINT -> switch slot 2 (no model data, first living, attempt 2)
20:44:47 FAINT -> switch slot 1 (no model data, first living, attempt 0)
```

修复后换人评分能区分不同宠物：

```
音速犬      vs 水灵     打=215 挨=430 hp=366  分数=-0.100
寂灭骨龙    vs 水灵     打=252 挨=462 hp=490  分数=+0.317
冰钻布鲁斯  vs 卡瓦重   打=204 挨=  0 hp=345  分数=+1.020
```

---

## 六、核对命令

```powershell
# 数值模型回归（不需要游戏）
python src\test_statmodel.py

# 克制规则与承伤分组
python -c "import sys;sys.path.insert(0,'src');import statmodel as S;print(S.type_effect('草',['水','地']))"   # 期望 3.0
python -c "import sys;sys.path.insert(0,'src');import statmodel as S;print(S.defense_profile(['水']))"

# 拆分是否纯搬移（0 差异才算过）
python scripts\verify_split.py
```
