# 本轮完成记录（2026-11-23 02:xx）

## 1. standalone warmup 40s → 3s

**根因**：`warmup()` 把 `_ident["at"]=0` 强制重跑完整 OCR，而新加的
后台采样线程同时在调 `identify()` —— 两条线程抢 `_lock` + GIL。

**修法**：warmup 期间暂停采样线程；只预热**引擎**（OCR/ONNX/头像银行/
数据层），不强制跑识别；分段计时打日志。

实测：`3042ms (_get=679 perceive=615 ocr_engine=15 onnx=814 avatar_bank=858 foe_hp=62)`

## 2. ui_server 空闲 CPU 111% → 0%

补齐上一轮的三个修复后实测空闲 0%（无人看就完全停采样）。

## 3. 我方身份识别链路重构

**实测数据（31 帧我方 HUD）**：

| 通道 | 表现 |
|---|---|
| 旧模板库 `assets/portraits` | **标签是错的**（音速犬.png 实为冰钻布鲁斯，互比 0.93；银月狼王#3/#6 是同一张蓝狼 0.96） |
| avatar 模板（pet_avatars 池内） | score **中位 0.413**，high 档只 3/31 |
| ONNX（HUD 头像） | conf≥0.90 占 70%，中位 0.99 |

**新顺序**（robot 和 standalone 统一）：
```
1. 名字 OCR（命中 my_team 即采信）      ← standalone 有
2. ONNX HUD 头像（conf>=0.85 且命中 my_team；>=0.97 允许表过期）
3. avatar 模板（池内 score>=0.50 + margin>=0.02）
4. 血条绝对值 / 旧模板库（兜底）
```

**错标模板隔离**（`assets/portraits/_quarantine/`，6 张）：
- 音速犬.png / 音速犬#4.png（实为蓝狼/金毛狼，与标签不符）
- 银月狼王#3 / #4 / #6 / #7（#3/#6 是冰钻布鲁斯，#4/#7 来源不明）

保留在库：冰钻布鲁斯、恶魔狼、白紫狼三张、冥古龙、卡瓦重、无垠之王。

## 待做

1. 实盘验证 ONNX 主通道在「火狗 vs 冰钻布鲁斯」上的实际表现
2. 技能识别（BAND）仍未跑通，等进战斗看日志