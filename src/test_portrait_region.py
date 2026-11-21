# -*- coding: utf-8 -*-
"""test_portrait_region.py —— 锁定「ONNX 默认输入是 HUD 头像框」。

为什么值得测
-----------
2026-11-21 实测发现 `portrait_onnx.region()` 用的固定百分比立绘框
准确率 **0/8 = 0%**：

    233445  REG-self 框住我方精灵的尾巴/身体中段
    233747  REG-self 框住我方精灵的后腿
    234212  REG-self 框住人物 NPC 的腰

框里的东西每帧都不一样（3D 精灵在场地里走动），但置信度仍然给出
0.52~0.99，所以它**看起来很正常，实际在污染决策**。

而 HUD 头像框 6/8 = 75%，命中时多为 1.00。

这个测试锁住三件事：
  1. `predict_region()` 默认走 HUD 框（source="hud"）
  2. 默认路径**不碰** `region()` 的百分比框
  3. `source="region"` 仍可显式启用（给 --selftest / 排查用）

第 2 条最容易回归：以后有人"顺手"把默认改回 region，识别会静默变瞎，
线上只表现为"偶尔认错"，很难发现。

用法：python src/test_portrait_region.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"D:\Tools")

import numpy as np  # noqa: E402

import portrait_onnx as PO  # noqa: E402

fails = 0


def check(cond, name, detail=""):
    global fails
    if not cond:
        fails += 1
    print("  %-4s %-52s %s" % ("OK" if cond else "FAIL", name, detail))


class FakeScreen:
    """记录被要求抓哪些框，并按框尺寸返回可辨认的纯色图。"""

    def __init__(self, client_size=(2560, 1600)):
        self.client_size = client_size
        self.calls = []

    def grab_client(self, box):
        self.calls.append(tuple(box))
        x, y, w, h = box
        w = max(1, int(w))
        h = max(1, int(h))
        return np.full((h, w, 3), 128, dtype=np.uint8)


def test_default_uses_hud():
    """默认必须走 HUD 头像框，且框的位置和 box_for(margin) 一致。"""
    import portraits

    scr = FakeScreen()
    PO.predict_region(scr, "foe", topk=1)

    check(len(scr.calls) >= 1, "默认路径至少抓了一次图",
          "calls=%d" % len(scr.calls))
    if not scr.calls:
        return

    got = scr.calls[-1]
    m = portraits.SEARCH_MARGIN
    want = portraits.foe_box_for(*scr.client_size, margin=m)
    check(tuple(int(v) for v in got) == tuple(int(v) for v in want),
          "默认抓的是 foe_box_for(margin=%d)" % m,
          "got=%s want=%s" % (got, want))

    # 关键：不能抓整屏（region 路径的特征）
    check(not (got[2] >= 2000 and got[3] >= 1000),
          "默认不抓整屏（说明没走 region 路径）", "box=%s" % (got,))


def test_self_side_box():
    """self 侧用 box_for，不是 foe_box_for。"""
    import portraits

    scr = FakeScreen()
    PO.predict_region(scr, "self", topk=1)
    got = scr.calls[-1]
    m = portraits.SEARCH_MARGIN
    want = portraits.box_for(*scr.client_size, margin=m)
    check(tuple(int(v) for v in got) == tuple(int(v) for v in want),
          "self 侧抓的是 box_for(margin=%d)" % m,
          "got=%s want=%s" % (got, want))


def test_region_still_available():
    """source="region" 必须仍能显式启用（--selftest / 排查用）。"""
    scr = FakeScreen()
    PO.predict_region(scr, "foe", topk=1, source="region")
    got = scr.calls[-1]
    check(got[2] >= 2000 and got[3] >= 1000,
          "source='region' 抓整屏", "box=%s" % (got,))


def test_region_cfg_not_used_by_default():
    """把 region 配置改成明显错误的值，默认路径不应受影响。

    这是最有价值的一条：如果以后有人把默认改回 region，
    这个测试会失败 —— 因为 region 的坐标被污染了。
    """
    old = PO.region("foe")
    try:
        PO._REGION = {"foe": {"x": 0.0, "y": 0.0, "w": 0.01, "h": 0.01},
                      "self": {"x": 0.0, "y": 0.0, "w": 0.01, "h": 0.01}}
        scr = FakeScreen()
        PO.predict_region(scr, "foe", topk=1)
        got = scr.calls[-1]
        # 如果走了 region，框会是 0.01 * 2560 = 25 像素宽
        check(got[2] > 50,
              "region 配置被污染后默认路径不受影响",
              "box=%s（走 region 的话宽只有 25）" % (got,))
    finally:
        PO._REGION = None if old else PO._REGION


def test_hud_crop_returns_none_on_dead_screen():
    """窗口没了要返回 None，不能抛异常（standalone 每秒都在调）。"""
    class Dead:
        client_size = (0, 0)

        def grab_client(self, box):
            return None

    try:
        got = PO.hud_crop(Dead(), "foe")
        check(got is None, "死窗口返回 None", repr(got))
    except Exception as e:
        check(False, "死窗口不抛异常", "%s: %s" % (type(e).__name__, e))


def test_hud_crop_returns_none_on_none_screen():
    check(PO.hud_crop(None, "foe") is None, "screen=None 返回 None")


def test_predict_region_no_screen():
    """没有 screen 时 predict_region 返回 []，不抛。"""
    try:
        r = PO.predict_region(None, "foe")
        check(r == [], "screen=None 返回 []", repr(r))
    except Exception as e:
        check(False, "screen=None 不抛异常", "%s: %s" % (type(e).__name__, e))


def test_margin_default_is_search_margin():
    """margin 默认必须取 portraits.SEARCH_MARGIN，不能硬编码别的数。"""
    import portraits
    scr = FakeScreen()
    PO.predict_region(scr, "foe", topk=1)
    got = scr.calls[-1]
    tight = portraits.foe_box_for(*scr.client_size, margin=0)
    # 带 margin 的框必须比紧框大 2*SEARCH_MARGIN
    check(got[2] - tight[2] == 2 * portraits.SEARCH_MARGIN,
          "宽比紧框大 2*SEARCH_MARGIN",
          "%d vs %d (margin=%d)" % (got[2], tight[2],
                                    portraits.SEARCH_MARGIN))


def main():
    print("=== portrait_onnx 输入框回归测试 ===")
    print()
    for fn in (test_default_uses_hud,
               test_self_side_box,
               test_region_still_available,
               test_region_cfg_not_used_by_default,
               test_hud_crop_returns_none_on_dead_screen,
               test_hud_crop_returns_none_on_none_screen,
               test_predict_region_no_screen,
               test_margin_default_is_search_margin):
        try:
            fn()
        except Exception as e:
            check(False, fn.__name__ + " 未捕获异常",
                  "%s: %s" % (type(e).__name__, e))
        print()

    if fails:
        print("FAILED %d" % fails)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
