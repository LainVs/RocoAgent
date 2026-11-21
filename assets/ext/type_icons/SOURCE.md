# type_icons —— 18 属性图标

## 来源

| 项 | 值 |
|---|---|
| 仓库 | `chy5301/roco-kingdom-world-coverage-calc` |
| 路径 | `assets/type/*.png` |
| 抓取方式 | GitHub Contents API（base64），`raw.githubusercontent.com` 本机不通 |
| 抓取时间 | 2026-11-20 |
| 原始命名 | 中文属性名（草/火/水/…），已改成 ASCII，原名记在 `manifest.json` |
| 许可 | 仓库标 "Other"；图标本身抓自 `wiki.biligame.com/rocom`，版权归原 wiki 与腾讯 |

## 文件

18 张，统一 **198×198 PNG**，白色字形 + 纯色底：

```
type_bug.png      type_electric.png  type_ground.png   type_machine.png  type_poison.png  type_wing.png
type_cute.png     type_fighting.png  type_ice.png      type_normal.png   type_water.png
type_dark.png     type_fire.png      type_illusion.png type_light.png
type_dragon.png   type_grass.png     type_ghost.png
```

`manifest.json`：`[{file, code, name_cn, w, h, kb}, ...]`

## 怎么用（这是关键，别直接拿整图去匹配）

游戏里 HUD 上的属性徽章是**圆形色底 + 浅色字形**，而这些图标是**方形色底 + 白色字形**。
底色和形状都不同，所以：

```
✔ 只用"字形"做模板：先按徽章内切圆裁剪，再按 底色→字形 的差异提取，匹配前做灰度归一化
✘ 不要整图直接 matchTemplate —— 方形 vs 圆形、底色也不同，分数会很差
```

## 实测：字形确实完全一致，但预处理决定成败（2026-11-20）

已验证：`icon_grass` 的叶子 与 游戏里 `草` 徽章的叶子**是同一片**（放大并排比对）。

但直接匹配失败，两次尝试都记录在此，避免重复踩：

| 尝试 | 做法 | 结果 | 原因 |
|---|---|---|---|
| ① 全图白色掩膜 | `V>200 & S<70` → bbox → 32×32 → NCC | 分数 ~0.10（几乎无相关） | 徽章有一圈**高光边**被算进掩膜，字形被缩小、位置偏移 |
| ② 最大连通域 | 取掩膜最大块再归一化 | 分数为负且几乎并列（-0.45~-0.54） | 徽章字形在 31×31 的盘里只有 ~14px，抗锯齿后掩膜**碎成轮廓**；图标那边是**实心**，形状对不上 |

**要成立必须做对三件事：**

```
1. 按徽章内切圆裁剪（半径 ~0.92×半宽），排除高光边和背景
2. 用灰度而不是二值：半透明圆盘 + 场景透出，二值化阈值极不稳定
3. 尺度对齐到"字形高度"而不是"整个裁剪尺寸"——
   图标里字形占满画面，徽章里字形只占约 45%
```

## 已知坑

- `cv2.imread` 打不开中文路径 —— 所以文件名已经改成 ASCII（原版是中文名）
- 图标 PNG 可能是 4 通道（BGRA），读进来先判通道数再转 BGR
- **部分属性底色色相重叠**，不能只靠颜色分类：
  `cute/dark/dragon` 都在 H 169~175，`ice/light/normal` 都在 H 101。
  颜色只能当**候选筛选**，必须叠加字形匹配才能定论。

## 用途

1. **HUD 属性徽章识别**：现在完全没做（`PROJECT.md` 里标的是"buff/属性徽章 🔴 未读"）。
   有了这套图标，我方/敌方的属性可以直读，不用靠"卡面颜色反解"。
2. **交叉验证**：和 `gamedb.type_chart` 一起用，可以校验"对面物种 → 属性"的推断。

## 已知坑

- `cv2.imread` 打不开中文路径 —— 所以文件名已经改成 ASCII（原版是中文名）
- 图标有 4 通道（BGRA）的可能，读进来先判通道数再转 BGR
