# 修复计划：把"变化技能"从"buff"里拆出来

## 问题（2026-11-21 实测确认）

### 现象
实盘日志每一局都在 T1/T2 打 `SKILL 4`：

```
05:19:33 T2   SKILL 4 (status 4: buff first (early turn, safe))
05:21:41 T1   SKILL 4 (status 4: buff first (early turn, safe))
05:27:23 T1   SKILL 4 (status 4: buff first (early turn, safe))
...  共 7 局，全部如此
```

### 真因
真实队伍（`my_team` 表）24 个技能位里 **0 个 buff 技能**。
所有 6 只精灵的 slot 4 都是：

| 技能 | 真实效果 |
|---|---|
| 借用 | 每回合随机变成**己方队伍其他精灵**的技能 |
| 取念 | 每回合随机变成**敌方任意精灵**的技能，能耗 -2 |

两个都是 **power=0 的复制技能**，不是 buff。

### 错误链路
```
perceive._fix_status_kinds:  power<=0  ->  kind = "dash"
robot._policy_state:         kind=="dash" -> 传给 policy_v2 的 kind="dash"
policy_v2:                   kind=="dash" -> 当成 buff，前两回合无脑优先
```

"变化技能"（dash）在代码里等于"无伤害技能"，而 policy_v2 又把它等于"buff"。
这个等号是错的 —— 无伤害技能有 6 类：

| 类别 | 数量 | 例子 |
|---|---|---|
| other（天气/印记/控制） | 114 | 放晴、霜降 |
| damage_reduction（减伤/护盾） | 41 | 冰墙 减伤80% |
| self_buff（自身增益） | 41 | 钧势 物防+140% |
| heal（治疗/回能） | 19 | 休息回复 +30% 生命 |
| enemy_debuff（给敌减益） | 18 | 霜冻 魔防-100% |
| copy（复制技能） | 3 | **借用、取念** |

## 修复方案

### 1. 新增 `src/skill_kind.py`（单一事实来源）

从 roco-cal `skills/*.json` 读真实效果，给每个技能名一个分类：

```python
KIND_DAMAGE     = "damage"      # skill_power > 0
KIND_SELF_BUFF  = "self_buff"   # target=self 的攻/防/速增益
KIND_ENEMY_DEBUFF = "enemy_debuff"
KIND_REDUCTION  = "reduction"   # damage_reduction > 0
KIND_HEAL       = "heal"
KIND_COPY       = "copy"        # 借用/取念/复写
KIND_OTHER      = "other"       # 天气、印记、控制
KIND_UNKNOWN    = "unknown"     # 查不到
```

对外 API：
```python
classify(skill_name) -> str          # 分类
is_buff(skill_name) -> bool          # 只有 self_buff 才是 buff
is_copy(skill_name) -> bool          # 借用/取念/复写
```

### 2. `perceive._fix_status_kinds` 不再抹掉技能身份

现在它把 `power<=0` 一律标 `dash`，导致下游无法区分。
改为：额外写入 `s["skill_name"]`（从队伍配置查），保留 `kind` 供 HUD 显示。

### 3. `robot._policy_state` 传真实分类

```python
kind = s.get("kind")
if kind == "dash":
    name = s.get("skill_name")          # 新增
    cls = SK.classify(name) if name else None
    skills.append({
        "i": ..., "dmg": None, "cost": ...,
        "kind": cls or "unknown",        # 不再统一传 "dash"
        "name": name,
        "lit": ...,
    })
```

### 4. `policy_v2` 只对真正的 self_buff 走"优先叠"分支

- `self_buff` -> 保持现有"前两回合优先"逻辑
- `copy` -> **明确排除**，按得分为 0 参与打分（绝不优先）
- `reduction` -> 按减伤比例折算收益（可后续做）
- `heal` / `enemy_debuff` / `other` -> 得分为 0 参与打分

### 5. 加测试

`src/test_skill_kind.py`：
- 借用/取念/复写 -> `copy`
- 钧势/分光/不动如山 -> `self_buff`
- 霜冻/雪球 -> `enemy_debuff`
- 冰墙/镜像反射 -> `reduction`
- 升龙咆哮/坟场搏击 -> `damage`
- 不存在的名字 -> `unknown`

## 验收标准

1. 单测全过
2. 模拟器里 `copy` 技能不再被优先（同队胜率不应因此下降）
3. 实盘日志不再出现 `SKILL 4 (buff first)` 而 slot4 是借用/取念
4. 如果队伍里真有 self_buff 技能，仍然优先叠

## 不做的事

- 不删除 `dash` 概念（HUD 层面"无伤害卡"仍需要）
- 不改 `_fix_status_kinds` 的 kind 赋值（避免影响 HUD 显示与其他调用方）
- 不在本轮实现 reduction/heal 的收益折算（先修错误的 buff 判定）
