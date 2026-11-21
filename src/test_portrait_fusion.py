# -*- coding: utf-8 -*-
"""test_portrait_fusion.py —— 对面身份两个通道的融合策略。

为什么值得测
------------
`identify_fused()` 决定 `foe_name`，而 `foe_name` 决定：
  · 悬浮窗显示的对面是谁
  · `_incoming()` 用哪套伤害模型算"这回合会掉多少血"
  · policy_v2 的属性克制系数

它一旦抖，整场决策全错。而它的策略依据曾经是**误解**：日志里把模板匹配
通道叫 "hud"，作者以为是读游戏界面上的名字（"绝对正确"），实际是对着
自己收集的头像库做模板匹配 —— 留一验证 Top-1 只有 42.1%。

2026-11-21 定标（src/portrait_calib.py --compare）：
    立绘 ONNX  Top-1 82.8%   conf>=0.90 时 93.2%（覆盖 81%）
    模板匹配    Top-1 42.1%   score>=0.85 时 94.6%（只覆盖 26%）
所以策略改成"立方高置信 + 稳定 -> 优先"。

用法：python src/test_portrait_fusion.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import portrait_onnx as PO  # noqa: E402

fails = 0


def check(cond, name, detail=""):
    global fails
    if not cond:
        fails += 1
    print("  %-4s %-46s %s" % ("OK" if cond else "FAIL", name, detail))


def fuse(hud, hud_conf, port, port_conf, prev=None, stable=2):
    pr = [(port, port_conf)] if port else []
    return PO.identify_fused(hud, pr, hud_conf, prev=prev, stable_count=stable)


def main():
    print("[1] 立方高置信 + 稳定 -> 压过模板通道")
    r = fuse("迪莫", 0.99, "绿草精灵", 0.98, stable=2)
    check(r["name"] == "绿草精灵", "conf 0.98 时用立绘", r["source"])
    check(r["conflict"] is True, "记下冲突供复盘")

    print()
    print("[2] 立方不够稳 -> 退回模板通道")
    r = fuse("迪莫", 0.99, "绿草精灵", 0.98, stable=1)
    check(r["name"] == "迪莫", "只稳定 1 帧时不压", r["source"])
    check(r["source"] == "hud", "来源是模板通道", r["source"])

    print()
    print("[3] 立方置信不够 -> 用模板")
    r = fuse("迪莫", 0.99, "绿草精灵", 0.80, stable=5)
    check(r["name"] == "迪莫", "0.80 不够压", "%s %.2f"
          % (r["name"], r["portrait_conf"]))

    print()
    print("[4] 模板没有 -> 立方 >=0.85 且稳定才用")
    r = fuse(None, 0, "吞噬", 0.90, stable=2)
    check(r["name"] == "吞噬", "模板缺失时用立绘", r["source"])
    r = fuse(None, 0, "吞噬", 0.90, stable=1)
    check(r["name"] is None, "不稳定时不用", str(r["name"]))
    r = fuse(None, 0, "吞噬", 0.70, stable=5)
    check(r["name"] is None, "置信不够时不用", str(r["name"]))

    print()
    print("[5] 已有认定时的防抖")
    # 已有 prev，立方 0.90 (< 0.92) 且不同 -> 保持 prev
    r = fuse(None, 0, "新答案", 0.90, prev="旧答案", stable=3)
    check(r["name"] == "旧答案", "不够显著时不改", r["source"])
    check(r["kept"] is True, "标记 kept")
    # 立方 >= 0.92 -> 允许改
    r = fuse(None, 0, "新答案", 0.95, prev="旧答案", stable=3)
    check(r["name"] == "新答案", ">=0.92 允许改", r["source"])

    print()
    print("[6] 两边都没有 -> 保持 prev / None")
    r = fuse(None, 0, None, 0.0, prev="旧答案", stable=0)
    check(r["name"] == "旧答案", "保持 prev", r["source"])
    r = fuse(None, 0, None, 0.0, prev=None, stable=0)
    check(r["name"] is None, "什么都没有 -> None", str(r["name"]))

    print()
    print("[7] 两边一致时不等于冲突")
    r = fuse("吞噬", 0.9, "吞噬", 0.99, stable=2)
    check(r["name"] == "吞噬", "一致时正常出结果")
    check(r["conflict"] is False, "一致不算冲突")

    print()
    print("[8] 模板低置信但仍然作为兜底")
    r = fuse("迪莫", 0.40, None, 0.0, prev=None, stable=0)
    check(r["name"] == "迪莫", "低置信兜底", r["source"])
    check(r["source"] == "hud-low", "标记 hud-low", r["source"])

    print()
    print("[9] 阈值可覆盖（便于回滚）")
    r = PO.identify_fused("迪莫", [("绿草精灵", 0.93)], 0.99,
                          stable_count=2, onnx_override=0.95)
    check(r["name"] == "迪莫", "门槛调到 0.95 后 0.93 不再压过", r["source"])

    print()
    if fails:
        print("%d FAILURE(S)" % fails)
        return 1
    print("all portrait fusion cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
