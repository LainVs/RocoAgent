# 外部资产：roco-cal

> 来源：https://github.com/180sans/roco-cal
> 拉取日期：2026-11-21
> 上游提交：`40417aa`（2026-11-20 push）
> 许可证：见上游 `LICENSE`

## 为什么把数据拷进来

这些是**固定资产**，不是运行时依赖：

1. 外部路径（`D:\GameData\...`）随时可能被移动或删除
2. 模型文件不会变（除非上游更新），拷进来才能保证换机器可用
3. 数据要参与版本管理，改了什么必须能追溯

总体积 **5.8 MB / 959 个文件**，其中模型 2.4 MB。

## 目录结构

```
data/external/roco-cal/
├── models/
│   ├── all_classes.onnx          1.4 MB  精灵识别模型 [1,3,86,76] -> [1,620]
│   ├── classes.json               14 KB  620 个类别名（"001迪莫" 这种）
│   ├── number.onnx               1.0 MB  血条数字 OCR [1,3,48,W] -> [1,T,14]
│   └── charset.txt                13 字符（0-9 + . % -）
├── pets/                         665 个  S5 精灵数据
├── traits/                       262 个  特性定义（含 battle_effects）
├── skills/                        18 个  按属性的技能库
└── engine/                        10 个  伤害引擎（纯 Python）
```

## 模型怎么用（已实测）

### 精灵识别 `all_classes.onnx`

```
输入: [batch, 3, 86, 76]  float32  通道优先，/255 归一化
输出: [batch, 620]        logits
```

预处理（逐行翻译自上游 `crates/desktop-native/src/inference.rs::classifier_tensor`）：

1. 等比缩放到不超过 76×86（Lanczos）
2. 居中填充到 76×86，空白处填黑
3. RGB /255，通道优先排列

**关键：必须喂"角色立绘区域"，不是 HUD 小头像。**

实测（`logs/shot/` 真实战斗帧）：

| 输入区域 | 结果 |
|---|---|
| HUD 头像 84×76 | `000无 1.00`（框里没东西） |
| 角色立绘 62-83%, 34-64% | `326燃薪虫 0.94~1.00` ✅ |
| 我方立绘 23-39%, 54-80% | `470冥古龙 0.80` ✅ |

### 数字 OCR `number.onnx`

```
输入: [batch, 3, 48, width]   height 固定 48，width 按宽高比自适应（48~320）
输出: [batch, timesteps, 14]  CTC 解码，14 = 13 字符 + blank
```

## 特性数据分类

上游把 260 个特性按机制分成 4 类：

| 目录 | 数量 | 含义 |
|---|---|---|
| `generic_stack/` | 55 | 通用可叠加（如三鼓作气：用 3 费技能后双攻双防 +20%） |
| `ignored/` | 159 | **不影响伤害计算**（如共鸣：只加虫鸣威力） |
| `passive/` | 14 | 被动常驻 |
| `special_condition/` | 32 | 特殊条件触发 |

**`ignored` 占 61%** —— 这解释了为什么"共鸣"被 27 只属性各异的精灵共享：
它本来就跟属性无关。

## 与我们的数据对比（实测）

| 项 | 一致 | 我们错 | 错误率 |
|---|---|---|---|
| 克制表 18×18 | 324/324 | 0 | 0% |
| 特性（441 只可比） | 331 | **109** | 24.8% |
| 属性 | 418 | **23** | 5.2% |
| 种族值 | 260 | **160** | 38.1% |

> roco-cal 与 RokePvP-Calculator 在 440/441 只上互相一致，
> 两个独立项目给出相同答案 → 以它们为准。

## 更新方式

上游更新后重新拉取：

```powershell
# 1. 拉 tarball（git clone 走不通，用 API）
python -c "
import urllib.request, ssl
proxy='http://127.0.0.1:7897'
op=urllib.request.build_opener(
    urllib.request.ProxyHandler({'http':proxy,'https':proxy}),
    urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
req=urllib.request.Request(
    'https://api.github.com/repos/180sans/roco-cal/tarball/main',
    headers={'User-Agent':'curl'})
open('roco-cal.tar.gz','wb').write(op.open(req).read())
"
# 2. 解压（跳过 images，中文路径 tar 会报错）
python scripts/import_roco_cal.py
```

`scripts/import_roco_cal.py` 会重新拷贝并打印差异。

## 许可与致谢

数据与模型来自 [180sans/roco-cal](https://github.com/180sans/roco-cal)，
作者明确说明"不同步游戏文件、只模拟游戏逻辑"。
本项目仅用于个人研究，遵循上游 LICENSE。
