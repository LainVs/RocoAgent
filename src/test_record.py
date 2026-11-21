# -*- coding: utf-8 -*-
"""test_record.py —— 战绩记录的回归测试。

用户报的问题（2026-11-21）
--------------------------
"对局记录还是没有成功记录输赢"：logs/results.jsonl 全是 flat。

根因（已确认）
--------------
record.load_state() 返回 dict：

    {"wins": 113, "losses": 106, "at": "2026-11-19 20:06:41"}

但 record.diff() 用整数下标访问：

    if after[0] > before[0] ...      # <== KeyError: 0

dict 没有 0/1 键，每次 diff 都抛 KeyError，被 robot.py 的
except Exception 吞掉，results.jsonl 永远写不进去。

证据：record_state.json 停在 2026-11-19（基线建立那天），
之后 5 天再没更新过 —— 因为 save_state 只在 diff 成功后才调用。

跑法：
    python src\\test_record.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import record

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


def test_load_state_type():
    """load_state 必须返回 tuple，不能是 dict。"""
    print("=== load_state 返回类型 ===")
    prev = record.load_state()
    if prev is None:
        print("  （没有 record_state.json，跳过类型检查）")
        return
    check(isinstance(prev, tuple), "返回 tuple（实际 %s）" % type(prev).__name__)
    check(len(prev) == 2, "长度 2（实际 %d）" % len(prev))
    check(all(isinstance(x, int) for x in prev),
          "两个元素都是 int（实际 %s）" % (prev,))


def test_diff_basic():
    """diff 的基本判定。"""
    print()
    print("=== diff 基本判定 ===")
    cases = [
        ((123, 149), (124, 149), "win"),
        ((123, 149), (123, 150), "loss"),
        ((124, 153), (124, 154), "loss"),
        ((124, 153), (125, 153), "win"),
    ]
    for before, after, want in cases:
        got = record.diff(before, after)
        check(got == want, "%s -> %s 得 %s（want %s）"
              % (before, after, got, want))


def test_diff_ambiguous():
    """胜负同时变 / 没变 -> None（宁可不判也不能判错）。"""
    print()
    print("=== diff 模糊情况 ===")
    cases = [
        ((123, 149), (124, 150)),      # 两个都变（跨局）
        ((123, 149), (123, 149)),      # 没变
        ((123, 149), (122, 149)),      # 赢数减少（读数错乱）
    ]
    for before, after in cases:
        got = record.diff(before, after)
        check(got is None, "%s -> %s 返回 None（实际 %s）"
              % (before, after, got))


def test_diff_accepts_dict():
    """diff 应兼容 dict 输入（防止调用方传原始 load_state 结果）。"""
    print()
    print("=== diff 兼容 dict ===")
    got = record.diff({"wins": 123, "losses": 149}, (124, 149))
    check(got == "win", "dict 输入判 win（实际 %s）" % got)
    got2 = record.diff({"wins": 123, "losses": 149},
                       {"wins": 123, "losses": 150})
    check(got2 == "loss", "两个 dict 判 loss（实际 %s）" % got2)


def test_diff_none_safe():
    """None / 缺字段不应崩。"""
    print()
    print("=== diff 空值安全 ===")
    check(record.diff(None, (1, 2)) is None, "before=None 返回 None")
    check(record.diff((1, 2), None) is None, "after=None 返回 None")
    check(record.diff({}, (1, 2)) is None, "空 dict 返回 None")
    check(record.diff({"wins": None, "losses": 1}, (1, 2)) is None,
          "缺 wins 返回 None")


def test_save_load_roundtrip():
    """save_state -> load_state 往返一致。"""
    print()
    print("=== save/load 往返 ===")
    orig = record.STATE
    tmp = os.path.join(tempfile.gettempdir(), "test_record_state.json")
    try:
        record.STATE = tmp
        record.save_state(88, 99)
        got = record.load_state()
        check(got == (88, 99), "往返一致（实际 %s）" % (got,))
        # 文件内容应是 dict（人类可读）
        raw = json.load(open(tmp, encoding="utf-8"))
        check(isinstance(raw, dict) and raw.get("wins") == 88,
              "文件里存的是 dict（%s）" % raw)
    finally:
        record.STATE = orig
        try:
            os.remove(tmp)
        except OSError:
            pass


def test_legacy_list_format():
    """兼容更早的 list 格式。"""
    print()
    print("=== 兼容旧 list 格式 ===")
    orig = record.STATE
    tmp = os.path.join(tempfile.gettempdir(), "test_record_list.json")
    try:
        record.STATE = tmp
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([55, 66], f)
        got = record.load_state()
        check(got == (55, 66), "list 格式也能读（实际 %s）" % (got,))
    finally:
        record.STATE = orig
        try:
            os.remove(tmp)
        except OSError:
            pass


def test_real_state_file():
    """真实的 record_state.json 能被正确解析（这是修复前会崩的地方）。"""
    print()
    print("=== 真实 record_state.json ===")
    path = os.path.join(os.path.dirname(HERE), "logs", "record_state.json")
    if not os.path.exists(path):
        print("  （文件不存在，跳过）")
        return
    raw = json.load(open(path, encoding="utf-8"))
    print("  文件内容: %s" % raw)
    prev = record.load_state()
    check(prev is not None, "load_state 能解析（%s）" % (prev,))
    if prev:
        # 关键：这个调用以前会抛 KeyError: 0
        try:
            got = record.diff(prev, (prev[0] + 1, prev[1]))
            check(got == "win", "diff 不抛异常且判 win（实际 %s）" % got)
        except Exception as e:
            check(False, "diff 抛异常: %s: %s" % (type(e).__name__, e))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    test_load_state_type()
    test_diff_basic()
    test_diff_ambiguous()
    test_diff_accepts_dict()
    test_diff_none_safe()
    test_save_load_roundtrip()
    test_legacy_list_format()
    test_real_state_file()
    print()
    if FAILS:
        print("FAILED %d:" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS")
