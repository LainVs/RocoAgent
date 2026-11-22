# 本轮计划：warmup 40 秒 + 错标头像（2026-11-23 02:xx）

## 现象

1. `logs/bot_stdout.log` 反复出现 `standalone warmup: 40885ms` / `38869ms`
   —— 正常应 ~2 秒（见 `standalone.warmup` docstring 的实测：冷启动
   23958ms、第 2 次 6804ms、第 3 次 450ms）。
2. 我方「火狗（音速犬）」被识别成「银月狼王」（已定位是模板库错标）。

## 任务 1：warmup 40 秒

### 怀疑点（按可能性排序）

| # | 假设 | 验证方法 |
|---|---|---|
| 1 | `_snap_loop` 与 `warmup` 同时抢占同一个 mss 句柄 / GIL | 看 warmup 期间采样线程是否在跑 |
| 2 | `warmup()` 里 `identify(now=time.time())` 跑完整 OCR（5~10s）× 2 侧 | 给 identify 加计时日志 |
| 3 | 后台采样线程每 0.45s 一次 observe，与 warmup 排队抢 `_lock` | warmup 里清 `note_demand` 让它停 |
| 4 | `avatar_ident.bank()` 首次构建（2.8s）+ ONNX 冷加载 | 分段计时 |

### 修法（预设）

- `warmup()` 期间**暂停采样线程**（把 `_SNAP_DEMAND["at"]` 置 0 让它 idle）
- `warmup()` 分段计时并打日志（哪一步慢一目了然）
- 若 identify 真的跑完整 OCR，改为"只预热引擎，不跑识别"

## 任务 2：错标头像

### 已知事实

- `assets/portraits/银月狼王#3.png` / `#6.png` 与 `冰钻布鲁斯.png` 相似度
  **0.72 / 0.67**，而两者彼此 **0.96**（同一张美术）
- `#4` / `#7` 是橙红狼，与所有队友官方头像都低于 0.4（来源不明）
- `#2` / `#5` / 无后缀 是白紫狼（真银月狼王）

### 修法

1. 用 `pet_avatars/<id>.png`（官方 639 类，权威标签）当基准，
   对 `assets/portraits` 每张模板全库打分 —— **分批跑**（每次只看
   32 张，避免 OpenBLAS 内存爆，之前一次性 639 类失败过）
2. 置信度 ≥0.6 且与声明不符 → 重命名为正确物种名
3. <0.6 → 移入 `assets/portraits/_quarantine/`（不参与匹配，保留备查）
4. 修正后跑 `test_avatar_ident` + 用实机帧验证

## 任务 3：技能识别（等进战斗）

- 观察 `BAND ->` 是否出现、conf 多少
- 若仍 0.00，用实机帧重标 ROI（上一轮已标定一次，需复核）

## 版本管理

每个任务一个提交；本文档随进展更新。