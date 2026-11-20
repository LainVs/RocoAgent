# -*- coding: utf-8 -*-
"""test_skill_reader.py —— 技能卡读数稳定化的回归测试。

用 2026-11-21 实盘抓到的真实抖动序列做用例：

    T4   s1=120  s2=350  s3=60
    T5   s1=-    s2=350  s3=60
    T6   s1=240  s2=350  s3=60
    T7   s1=150  s2=350  s3=60
    T8   s1=15?  s2=196  s3=420   <- 整组错位
    T9   s1=153  s2=120  s3=50

跑法：
    python src\test_skill_reader.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from skill_reader import SkillStabilizer

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


def card(v, kind="num"):
    return {"value": v, "kind": kind, "index": 1}


def vals(out):
    return [None if s is None else s.get("value") for s in out]


def test_real_jitter():
    """复现实盘抖动序列。"""
    print("=== 实盘抖动序列（音速犬）===")
    st = SkillStabilizer()

    frames = [
        ([card(120), card(350), card(60), None], [120, 350, 60, None]),
        ([card(None), card(350), card(60), None], [None, 350, 60, None]),
        ([card(240), card(350), card(60), None], [240, 350, 60, None]),
        ([card(150), card(350), card(60), None], [150, 350, 60, None]),
    ]
    for i, (frame, want) in enumerate(frames):
        out = st.update(frame, pet="音速犬")
        check(vals(out) == want, "帧%d -> %s" % (i + 1, vals(out)))

    # 第 5 帧整组错位：三个槽位同时偏离历史众数
    out = st.update([card(15), card(196), card(420), card(335)], pet="音速犬")
    got = vals(out)
    check(st.stats()["dropped"] == 1, "整组错位被丢弃（dropped=1）")
    check(got[1] == 350 and got[2] == 60,
          "slot2/slot3 回到历史真值 350/60（实际 %s）" % got[1:3])


def test_no_history_passthrough():
    """首帧没有历史，必须原样返回（不阻塞决策）。"""
    print()
    print("=== 首帧原样返回 ===")
    st = SkillStabilizer()
    out = st.update([card(123), card(456), None, None], pet="甲")
    check(vals(out) == [123, 456, None, None], "首帧 %s" % vals(out))


def test_pet_switch_resets():
    """换精灵必须清空历史，否则会把上一只的卡套到新精灵上。"""
    print()
    print("=== 换精灵清空历史 ===")
    st = SkillStabilizer()
    for _ in range(4):
        st.update([card(350), card(60), card(70), None], pet="音速犬")
    out = st.update([card(999), card(888), card(777), None], pet="恶魔狼")
    check(vals(out) == [999, 888, 777, None],
          "换精灵后不沿用旧值：%s" % vals(out))


def test_two_slot_drift_kept():
    """只有 2 个槽位偏离时不判整组错位（避免误伤真实变化）。"""
    print()
    print("=== 2 槽偏离不丢弃 ===")
    st = SkillStabilizer()
    for _ in range(4):
        st.update([card(100), card(200), card(300), card(400)], pet="甲")
    out = st.update([card(100), card(200), card(999), card(888)], pet="甲")
    check(st.stats()["dropped"] == 0, "未丢弃（dropped=0）")


def test_dash_slots():
    """变化技能（dash）不应被当成伤害值参与投票。"""
    print()
    print("=== dash 槽位 ===")
    st = SkillStabilizer()
    for _ in range(3):
        st.update([card(200), card(None, "dash"), card(150), None], pet="甲")
    out = st.update([card(200), card(None, "dash"), card(150), None], pet="甲")
    check(vals(out)[1] is None, "dash 槽位值为 None")
    check(out[1].get("kind") == "dash", "dash kind 保留")


def test_stabilized_flag():
    """被稳定化覆盖的槽位应打标记，便于复盘。"""
    print()
    print("=== 稳定化标记 ===")
    st = SkillStabilizer()
    st.update([card(350), card(60), card(70), None], pet="甲")
    st.update([card(350), card(60), card(70), None], pet="甲")
    out = st.update([card(120), card(60), card(70), None], pet="甲")
    s0 = out[0]
    check(s0.get("stabilized") is True, "slot1 打了 stabilized 标记")
    check(s0.get("value") == 350, "slot1 值被纠正为 350（实际 %s）"
          % s0.get("value"))
    check(s0.get("value_raw") == 120, "原始值保留在 value_raw（%s）"
          % s0.get("value_raw"))


def test_none_pet_no_reset():
    """pet 在名字和 None 之间抖动时不能清空历史。

    实测（2026-11-21 07:10）：识别抖动会让 pet 反复变成 None，
    如果 None 也清空历史，稳定化永远攒不到 2 帧，等于没生效。
    """
    print()
    print("=== pet 抖动成 None 不清空历史 ===")
    st = SkillStabilizer()
    st.update([card(350), card(60), card(70), None], pet="音速犬")
    st.update([card(350), card(60), card(70), None], pet=None)
    out = st.update([card(120), card(60), card(70), None], pet="音速犬")
    check(out[0].get("value") == 350,
          "None 抖动后仍能纠正 slot1 为 350（实际 %s）" % out[0].get("value"))


def test_stale_escape():
    """真实值持续变化时必须能突破历史锁定。

    实测（2026-11-21 07:40）：不加淘汰，第 1 帧的 [155,None,254,300]
    会把后面 28 帧全部锁死，即使真实值已变成 [240,None,152,150]。
    跨局时 pet 名字相同（同一只精灵打多局），历史不会因换精灵清空。
    """
    print()
    print("=== 过时淘汰（防止永久锁死）===")
    st = SkillStabilizer()
    for _ in range(3):
        st.update([card(155), None, card(254), card(300)], pet="甲")
    # 真实值变了，连续喂 5 帧（超过 STALE_LIMIT=4）
    outs = []
    for _ in range(6):
        out = st.update([card(240), None, card(152), card(150)], pet="甲")
        outs.append(vals(out))
    check(outs[-1] == [240, None, 152, 150],
          "第 6 帧放行新值（实际 %s）" % outs[-1])


def test_empty_input():
    print()
    print("=== 边界 ===")
    st = SkillStabilizer()
    check(st.update(None) is None, "None 输入返回 None")
    check(st.update([]) == [], "空列表返回空列表")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    test_real_jitter()
    test_no_history_passthrough()
    test_pet_switch_resets()
    test_two_slot_drift_kept()
    test_dash_slots()
    test_stabilized_flag()
    test_none_pet_no_reset()
    test_stale_escape()
    test_empty_input()
    print()
    if FAILS:
        print("FAILED %d:" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS")
