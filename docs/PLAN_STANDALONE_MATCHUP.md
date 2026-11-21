# 独立模式补齐克制关系与数值推断

**版本**：`ui-m77`
**日期**：2026-11-21
**前置**：`ui-m76`

---

## 一、现状（实机 API 读数）

```
me.name       = 冰钻布鲁斯    types = [冰]        hp_pct = null   ← 缺
foe.name      = 离心舞者      types = [幻, 机械]   hp_pct = 80.4
matchup       = null          ← 缺（双方属性都有！）
verdict       = null          ← 缺
foe.def_range = null          ← 缺
```

身份识别已经跑通了（`[standalone] FOE -> 离心舞者 (onnx, 1.00)`），
但**算出来的东西没往上走**。

`src/standalone.py` 第 782-783 行是硬编码：

```python
"matchup": None,
"verdict": None,
```

`robot.py` 第 888-908 行有一份完整实现，独立模式从来没接上。

---

## 二、要补的三件事

### 2.1 克制关系（`matchup`）

`robot.py` 已有逻辑，直接复用：

```python
outs = [S.type_effect(t, foe_types) for t in my_types]
ins  = [S.type_effect(t, my_types)  for t in foe_types]
out_m = max(outs) if outs else 1.0
in_m  = max(ins)  if ins  else 1.0
```

标签规则：`>=3 双重克制` / `>=2 克制` / `in>=3 被双重克制` /
`in>=2 被克制` / 否则 `互不克制`。

**实测验证**（`statmodel.type_effect`）：
```
冰 打 [幻,机械] = 0.5
[幻,机械] 打我冰 = max(1.0, 2.0) = 2.0
-> out=0.5, in=2.0, tag="被克制"
```

和截图里悬浮窗显示的「我打 ×0.5 / 打我 ×2.0 / 被克制」一致
（那次是机器人模式推的数据）。

### 2.2 敌方血量/物防区间（`foe.hp_range` / `def_range`）

`statmodel.hp_range(...)` / `def_range(...)` 已存在。需要：
- 种族名（已有 `foe_name`）
- 等级（独立模式读不到，用 60 兜底 —— 排位满级）
- 性格（读不到，传 None 表示"不确定"）

**这一项要谨慎**：区间是基于"未知性格/个体值"的上下界，
显示出来用户会当成确定值。所以：
- 只在 `foe_name` 已知时算
- 性格未知时区间会很宽，这是**正确**的（诚实表达不确定性）

### 2.3 结论（`verdict`）

`robot.py` 的实现依赖伤害预测（需要技能名 + 数据库），
独立模式**拿不到技能名**，所以 `kill_turns` 算不出来。

**决策：不补 `verdict`。** 硬编一个基于血量的粗估会误导用户
——"2 回合击杀"这种结论一旦不准，比不显示危害大得多。

保持 `verdict: null`，前端已经会显示「数据不足」。

---

## 三、实施

1. `src/standalone.py`
   - 抽一个 `_matchup(my_types, foe_types)` 函数
   - 抽一个 `_foe_ranges(foe_name)` 函数
   - 在 `observe()` 里接上
2. `src/test_standalone.py` 加用例：
   - 双方属性齐 -> matchup 非空且数值正确
   - 任一方属性空 -> matchup 为 None（不能瞎算）
   - 种族名未知 -> ranges 为 None
3. 实机验证

---

## 四、验证

- 实机 API：`matchup.out/in/tag` 有值且与 `statmodel` 手算一致
- 属性缺失时仍为 `null`（不猜）
- `test_standalone.py` 全过
- `test_ui.py` 120 项不回归

---

## 五、不做

- **不补 `verdict`**（缺技能名，硬估会误导）
- **不补 `me.hp_pct`**（那是血条检测器的问题，属感知层，
  和本次"把已知数据往上走"是两件事，分开做）
- **不改 `robot.py`**（它那份是对的，只是没被复用）
