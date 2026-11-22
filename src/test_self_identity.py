# -*- coding: utf-8 -*-
"""test_self_identity.py —— 我方身份通道优先级回归。

## 锁住的规则

ONNX 主通道给 1.00 时，avatar 次通道**不得**用 0.51 覆盖它。

## 实测依据（2026-11-23 07:0x，logs/band_probe.out.log）

    07:04:24 PET -> 恶魔狼      (ONNX 1.00)     <- 正确
    07:06:11 PET -> 冰钻布鲁斯   (avatar 0.51)   <- 被低分覆盖
    07:07:20 PET -> 卡瓦重       (avatar 0.59)   <- 又被覆盖
    07:07:32 PET -> 音速犬       (ONNX 1.00)     <- 又纠回来

身份在抖的直接后果：BAND 用**错精灵的技能池**去校验真实卡面。
实测那一刻场上是恶魔狼，卡面写着"火焰切割"（恶魔狼技能），
但 `my_name` 是音速犬，于是 conf 0.55 被拒 —— 用户看到的是
"技能识别不准"，实际是身份在抖。

用户此前的反馈"实机是冰钻布鲁斯，被误认成银月狼王"同源。

## 检查项

1. avatar 通道的硬门槛 >= 0.62（不是 0.50）
2. avatar 只能在"ONNX 未高分确认"或"avatar 自己很高"时覆盖
3. ONNX 通道会记录 `_self_onnx_conf`
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


fn = None
for node in ast.walk(TREE):
    if isinstance(node, ast.FunctionDef) and node.name == "_check_active_pet":
        fn = node
        break
check("_check_active_pet 存在", fn is not None)

if fn is not None:
    seg = ast.get_source_segment(SRC, fn) or ""
    check("记录 ONNX 置信度", "_self_onnx_conf" in seg)

    # 找到 avatar 硬门槛那个比较：形如 `a0["score"] >= 0.62`
    # 注意不能误抓 `_may_overwrite` 里的 0.75 —— 那是"允许覆盖"的阈值，
    # 不是采纳门槛。判据：该比较必须是 if 测试里的**第一个**比较项
    # （采纳条件），且所在 if 的 body 里出现 my_name 赋值。
    gate = None
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if not isinstance(t, ast.BoolOp) or not t.values:
            continue
        first = t.values[0]
        if not isinstance(first, ast.Compare):
            continue
        s = ast.get_source_segment(SRC, first) or ""
        if '["score"]' not in s:
            continue
        c = first.comparators[0]
        if not isinstance(c, ast.Constant) or not isinstance(c.value, (int, float)):
            continue
        body = "\n".join(ast.get_source_segment(SRC, b) or ""
                         for b in node.body)
        if "my_name" not in body:
            continue
        gate = (c.value, s)
        break
    check("找到 avatar 硬门槛（采纳条件）", gate is not None)
    if gate:
        v, s = gate
        check("门槛 >= 0.62（旧值 0.50 太低）", v >= 0.62,
              "实际 %.2f  (%s)" % (v, s))

    check("有'不得覆盖'的保护逻辑", "_may_overwrite" in seg)
    if "_may_overwrite" in seg:
        check("保护看的是 ONNX 置信度", "0.85" in seg)
        check("avatar 自身很高时仍可覆盖", "0.75" in seg)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")