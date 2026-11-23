# -*- coding: utf-8 -*-
"""test_ident_gate.py —— 换人后身份未确认时禁止 BAND 的回归。

## 实测背景（2026-11-23 09:4x，logs/band_probe.out.log）

```
09:46:14 PET -> 音速犬 (ONNX 1.00)      <- 最后一次成功识别
09:46:39 BAND -> 音速犬 槽1 raw='叠浪'   <- 连拒 3 次
09:46:55 BAND -> 音速犬 槽1 raw='叠浪'
09:46:58 BAND -> 音速犬 槽1 raw='叠浪'
09:48:58 FORCED SWITCH -> panel is up   <- 场上换过精灵
09:49:05 PET -> 恶魔狼 (ONNX 1.00)
```

「叠浪」属主仅 9 只、全是水系精灵，音速犬（火系）学不了它。
dump 图 `band_音速犬_1_1795398392_ctx.png` 里卡面清楚是「叠浪」，
美术是蓝色水系 —— 与同一槽位早前读到的「反击拳」（橙红拳套）
完全不同。

同窗口 `unknown bust` 出现 **9 次**，ONNX 持续失败。

**结论：场上精灵换过，my_name 卡在旧值。BAND 用音速犬的词表校验
新精灵的卡面，必然失败。** 更险的是槽2/槽4 "碰巧"读到了音速犬也有
的技能（「诋毁」111 属主、「闪光冲击」33 属主），差点把错配数据
写进 session_moves 污染策略。

## 为什么用换人信号而不是时间

实测我方身份事件：成功 40 / 失败 22（35% 失败率），
成功识别之间的间隔**中位 103s、均值 262s、90% 超过 20s**。
ONNX 在战斗中本就稀疏，时间门禁会误伤 90% 的正常 BAND 机会。

## 检查项

1. 初始化时 `_ident_confirmed` 存在且为 True（允许首帧就工作）
2. 三处换人信号都把它置 False
3. 只有"真正的身份采纳"才置 True —— 旧模板兜底通道**不能**置 True
4. `_maybe_band_scan` 里有该门禁
"""
import ast
import io
import os
import sys

ROOT = r"D:\Projects\RocoAgent"
SRC = io.open(os.path.join(ROOT, "src", "robot.py"), encoding="utf-8").read()
TREE = ast.parse(SRC)

FAIL = []


def check(name, cond, detail=""):
    print("  %s %s %s" % ("ok  " if cond else "FAIL", name, detail))
    if not cond:
        FAIL.append(name)


# ---- 1) 初始化存在 ------------------------------------------------
ctx = SRC[SRC.find("class Bot:"):]
init_seg = ""
for node in ast.walk(TREE):
    if isinstance(node, ast.FunctionDef) and node.name == "__init__":
        seg = ast.get_source_segment(SRC, node) or ""
        # Bot.__init__ 是带 self 参数的那个
        if "self.screen" in seg:
            init_seg = seg
            break
check("Bot.__init__ 有 _ident_confirmed",
      "_ident_confirmed" in init_seg)
check("初始为 True（首帧即可工作，不必等换人）",
      "_ident_confirmed = True" in init_seg)

# ---- 2) 换人信号置 False ------------------------------------------
print("\n== 换人信号 ==")
check("主动换人处置 False", "self._ident_confirmed = False" in SRC)
count_false = SRC.count("self._ident_confirmed = False")
check("至少有 3 处置 False（主动换人/强制换人/新对局）",
      count_false >= 3, "实际 %d 处" % count_false)

# ---- 3) 只有真身份采纳才置 True -----------------------------------
print("\n== 置 True 的位置 ==")
lines = SRC.splitlines()
true_lines = [(i + 1, l.strip()) for i, l in enumerate(lines)
              if "_ident_confirmed = True" in l]
for ln, l in true_lines:
    print("     line %-5d %s" % (ln, l))
# ONNX 通道 + avatar 通道各一处（初始化那条不算 True 赋值检查之外）
real_true = [t for t in true_lines if "self._ident_confirmed = True" in t[1]]
check("置 True 的位置 >= 2（ONNX + avatar）", len(real_true) >= 2,
      "%d 处" % len(real_true))

# 关键：旧模板兜底通道（_trusted 那段）里不能有 True
i_trusted = SRC.find("_trusted = _upper >= 0.85")
if i_trusted > 0:
    tail = SRC[i_trusted:i_trusted + 2500]
    # 截到下一个顶层 return 之后
    check("旧模板兜底通道不置 True",
          "self._ident_confirmed = True" not in tail,
          "该通道标签已证伪，置 True 会让错身份重新开放 BAND")

# ---- 4) BAND 里有门禁 ---------------------------------------------
print("\n== _maybe_band_scan 门禁 ==")
fn = None
for node in ast.walk(TREE):
    if isinstance(node, ast.FunctionDef) and node.name == "_maybe_band_scan":
        fn = node
        break
check("_maybe_band_scan 存在", fn is not None)
if fn is not None:
    seg = ast.get_source_segment(SRC, fn) or ""
    check("有 _ident_confirmed 判断", "_ident_confirmed" in seg)
    # 门禁必须在"真正读卡"之前
    i_gate = seg.find("_ident_confirmed")
    i_read = seg.find("read_band")
    check("门禁在读卡之前", 0 < i_gate < i_read,
          "gate@%d read@%d" % (i_gate, i_read))
    check("新对局重置 _ident_confirmed", "_ident_confirmed = False" in SRC)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")