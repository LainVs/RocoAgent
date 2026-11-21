# -*- coding: utf-8 -*-
"""test_standalone.py —— 独立感知（脱离机器人）的回归测试。

为什么值得测
------------
用户需求："伤害计算器能脱离机器人也可以使用吗，会自动识别吗？"

这是**产品承诺**，不是内部细节。它必须满足两条：
  1. 机器人没跑时，/api/state 仍然给出画面读数（不是 null）
  2. 游戏窗口不在时，明确返回 null（不要假装有数据）

第 2 条同样重要：如果游戏没开却返回一个"全空但不为 null"的状态，
前端会显示"战斗中 · 全部 —"，用户会以为识别坏了。

用法：python src/test_standalone.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"D:\Tools")

import numpy as np  # noqa: E402
import cv2           # noqa: E402

import standalone    # noqa: E402

fails = 0


def check(cond, name, detail=""):
    global fails
    if not cond:
        fails += 1
    print("  %-4s %-48s %s" % ("OK" if cond else "FAIL", name, detail))


def make_fake_observe(battle=True, bar=True):
    """替换 Perc.observe 的返回值，模拟一帧战斗画面。"""
    def _obs(self=None):
        return {
            "t": 0.0,
            "battle": battle,
            "my_ui": True, "foe_ui": True,
            "my_pct": 78.0, "my_color": "green",
            "foe_pct": 42.0, "foe_color": "green",
            "bar": bar, "npills": 4,
            "countdown": True,
            "energy": 3,
            "myturn": True,
            "skills": [
                {"index": 1, "value": 175, "text": "175",
                 "kind": "num", "lit": True},
                {"index": 2, "value": 114, "text": "114",
                 "kind": "num", "lit": True},
                {"index": 3, "value": None, "text": "--",
                 "kind": "dash", "lit": True},
                {"index": 4, "value": 102, "text": "102",
                 "kind": "num", "lit": False},
            ],
        }
    return _obs


class FakeScreen:
    BASE_CLIENT_W, BASE_CLIENT_H = 2560, 1600

    def __init__(self):
        self.rect = (0, 0, 2586, 1671)
        self.client_off = (13, 58)
        self.client_size = (2560, 1600)


def main():
    print("[1] 游戏窗口不在 -> available() False")
    real_find = None
    try:
        import winio
        real_find = winio.find_game
        winio.find_game = lambda: None
        check(standalone.available() is False, "没窗口时 available=False")
    finally:
        if real_find is not None:
            import winio
            winio.find_game = real_find

    print()
    print("[2] 有画面时能组装出前端契约")
    # 注入假 Screen / Perc
    old = (standalone._perc, standalone._screen, dict(standalone._cache))
    try:
        import perceive
        scr = FakeScreen()
        fake = perceive.Perc(scr)
        fake.observe = make_fake_observe().__get__(fake, type(fake))
        standalone.inject_screen(scr, fake)
        standalone._cache = {"at": 0.0, "state": None}

        st = standalone.observe(force=True)
        check(st is not None, "返回了状态（不是 None）")
        check(st.get("standalone") is True, "标了 standalone=True")
        check(st["mode"] == "battle", "mode=battle", st["mode"])
        check(st["me"]["hp_pct"] == 78.0, "我方血量", str(st["me"]["hp_pct"]))
        check(st["foe"]["hp_pct"] == 42.0, "对面血量", str(st["foe"]["hp_pct"]))
        check(st["system"]["detectors"]["skillbar"] is True, "技能栏检测器")
        check(st["system"]["detectors"]["countdown"] is True, "倒计时检测器")

        print()
        print("[3] 技能卡：伤害数字要有，名字必须是 None")
        sk = st["skills"]
        check(len(sk) == 4, "四张卡", str(len(sk)))
        check(sk[0]["dmg"] == 175, "卡1 伤害 175", str(sk[0]["dmg"]))
        check(sk[0]["name"] is None, "卡1 名字是 None（不编造）")
        check(sk[0]["_text"] == "175", "保留原始读数", sk[0]["_text"])
        check(sk[2]["usable"] is False,
              "dash 卡 usable=False（无伤害数字，不是伤害卡）",
              str(sk[2]["usable"]))
        check(sk[3]["usable"] is False, "未点亮的卡 usable=False")
        check(sk[0]["pct"] is None,
              "pct 是 None（没有对面最大血量，算不出百分比）")

        print()
        print("[4] 缓存：10Hz 内复用")
        a = standalone.observe()
        b = standalone.observe()
        check(a is b, "连续调用返回同一个对象")

        print()
        print("[5] 机器人不推数据时，ui_server 会回落到独立感知")
        import ui_server as U
        # 确保没有 live_state.json，并且"游戏窗口在"
        real_exists = os.path.exists
        real_avail = standalone.available
        try:
            os.path.exists = lambda p: (False if p == U.LIVE_STATE
                                        else real_exists(p))
            standalone.available = lambda: True
            got = U.live_state()
            check(got is not None and got.get("standalone") is True,
                  "live_state() 回落到独立感知",
                  "standalone=%s" % (got or {}).get("standalone"))

            print()
            print("[5b] 游戏窗口不在时仍然返回 None（不假装有数据）")
            standalone.available = lambda: False
            got2 = U.live_state()
            check(got2 is None, "没有窗口 -> None", str(got2))
        finally:
            os.path.exists = real_exists
            standalone.available = real_avail

    finally:
        standalone._perc, standalone._screen = old[0], old[1]
        standalone._cache = old[2]

    print()
    print("[6] 异常不能抛给调用方")
    old2 = (standalone._perc, standalone._screen)
    try:
        class Boom:
            def observe(self):
                raise RuntimeError("模拟抓屏失败")
        standalone.inject_screen(FakeScreen(), Boom())
        standalone._cache = {"at": 0.0, "state": None}
        r = standalone.observe(force=True)
        check(isinstance(r, dict) and "error" in r,
              "返回 error 而不是抛异常", str(r)[:60])
    finally:
        standalone._perc, standalone._screen = old2[0], old2[1]
        standalone._cache = {"at": 0.0, "state": None}

    print()
    print("[7] 身份识别：节流 + 置信度门槛 + 换人检测")
    old3 = dict(standalone._ident)
    try:
        standalone._ident.update({
            "at": 0.0, "foe": None, "foe_conf": 0.0,
            "self": None, "self_conf": 0.0, "foe_hp_at_id": None,
        })
        # 注入假的 portrait_onnx
        import portrait_onnx as PO
        real_avail, real_pred = PO.available, PO.predict_region
        calls = {"n": 0}

        def fake_pred(screen, side="foe", topk=3):
            calls["n"] += 1
            if side == "foe":
                return [("水灵", 0.97)]
            return [("音速犬", 0.95)]

        try:
            PO.available = lambda: True
            PO.predict_region = fake_pred
            # ★ 必须同时替换 _get()，否则 identify() 会拿真实 Screen
            # （游戏开着时真的去读屏，测试结果被真实识别污染 —— 实测踩过：
            #   期望"水灵"却得到"月牙雪熊"，因为读的是真屏幕）
            real_get = standalone._get
            standalone._get = lambda: (FakeScreen(), None)
            standalone._screen = FakeScreen()
            standalone._perc = standalone._perc  # 保持不变

            # 第一次：应该识别
            r1 = standalone.identify(now=1000.0, foe_hp_pct=100.0)
            check(r1.get("foe") == "水灵", "识别到对面", str(r1.get("foe")))
            check(r1.get("self") == "音速犬", "识别到我方", str(r1.get("self")))
            check(r1.get("foe_conf") == 0.97, "记录置信度",
                  str(r1.get("foe_conf")))

            # 节流：0.5 秒后再调，不应该重新识别
            n_before = calls["n"]
            standalone.identify(now=1000.5, foe_hp_pct=100.0)
            check(calls["n"] == n_before, "1.5s 内不重复识别",
                  "%d -> %d" % (n_before, calls["n"]))

            # 血量突然变高 -> 认为换人，清空重认
            standalone.identify(now=1002.0, foe_hp_pct=100.0)
            # 先降到 30 再跳回 100，模拟换人
            standalone._ident["foe_hp_at_id"] = 30.0
            r2 = standalone.identify(now=1004.0, foe_hp_pct=100.0)
            check(r2.get("foe") == "水灵", "换人后重新识别",
                  str(r2.get("foe")))

            # 低置信度不采用
            standalone._ident.update({"at": 0.0, "foe": None,
                                      "foe_conf": 0.0})
            PO.predict_region = lambda s, side="foe", topk=3: [
                ("乱猜的", 0.60)]
            r3 = standalone.identify(now=2000.0, foe_hp_pct=50.0)
            check(r3.get("foe") is None, "置信 0.60 不采用（低于 0.90）",
                  str(r3.get("foe")))
        finally:
            PO.available, PO.predict_region = real_avail, real_pred
            standalone._get = real_get
    finally:
        standalone._ident.clear()
        standalone._ident.update(old3)

    print()
    print("[8] 游戏窗口不在时 Screen 也不能缺属性（2026-11-21 修）")
    #
    # 踩到的坑：Screen.refresh() 在 find_game() 返回 None 时提前 return，
    # 于是新构造的对象没有 client_size。而 standalone / foe_ident /
    # portraits.box_for 都直接读 `screen.client_size`，游戏没开就崩：
    #     AttributeError: 'Screen' object has no attribute 'client_size'
    #
    # 这个错误只在"游戏没开"时触发 —— 也就是最常见的情况，必须钉死。
    import winio
    try:
        real_screen = winio.Screen()
        ok = hasattr(real_screen, "client_size")
    except Exception as e:
        real_screen = None
        ok = False
        print("       ", type(e).__name__, e)
    check(ok, "winio.Screen() 构造后 client_size 可访问")
    check(getattr(real_screen, "client_off", None) is not None,
          "client_off 也必须存在")

    import portraits
    try:
        box = portraits.foe_box_for(0, 0, margin=10)
        ok = isinstance(box, tuple) and len(box) == 4
    except Exception as e:
        ok = False
        print("       ", type(e).__name__, e)
    check(ok, "foe_box_for(0,0) 不崩，返回 4 元组")

    try:
        box = portraits.box_for(0, 0, margin=10)
        ok = isinstance(box, tuple) and len(box) == 4
    except Exception as e:
        ok = False
        print("       ", type(e).__name__, e)
    check(ok, "box_for(0,0) 不崩，返回 4 元组")

    class _EmptyScreen:
        rect = None
        hwnd = None
        client_size = (0, 0)
        client_off = (0, 0)

        def grab_client(self, box):
            return None

        def grab_frame(self):
            return None, (1.0, 1.0)

    import foe_ident
    try:
        r = foe_ident.identify(_EmptyScreen(), side="foe")
        ok = isinstance(r, dict) and r.get("name") is None
    except Exception as e:
        ok = False
        print("       ", type(e).__name__, e)
    check(ok, "foe_ident.identify(空屏) 返回 name=None 而不是抛异常")

    import hud_ident
    try:
        t = hud_ident.read_types(_EmptyScreen(), "foe")
        ok = isinstance(t, list)
    except Exception as e:
        ok = False
        print("       ", type(e).__name__, e)
    check(ok, "hud_ident.read_types(空屏) 返回 []")

    print()
    print("[9] 克制关系：有属性才算，缺属性不瞎算")
    #
    # 背景（2026-11-21）：`observe()` 里 matchup 原来是硬编码 None，
    # UI 明明有双方属性却显示「属性未知」。接上之后必须保证：
    #   · 双方属性齐 -> 数值正确
    #   · 任一方缺   -> None（不能拿默认 1.0 假装"互不克制"）
    m = standalone._matchup(["冰"], ["幻", "机械"])
    check(m is not None, "双方属性齐 -> 有 matchup")
    if m:
        check(m.get("out") == 0.5, "out=0.5（冰打幻/机械）", str(m.get("out")))
        check(m.get("in") == 2.0, "in=2.0（机械打冰）", str(m.get("in")))
        check(m.get("tag") == "被克制", "tag=被克制", str(m.get("tag")))

    check(standalone._matchup(["冰"], []) is None,
          "对面属性空 -> None（不瞎算）")
    check(standalone._matchup([], ["幻"]) is None,
          "我方属性空 -> None（不瞎算）")
    check(standalone._matchup(None, None) is None,
          "双方都 None -> None")

    # 互不克制也要给出 1.0/1.0 而不是 None（那是有效结论）
    m2 = standalone._matchup(["普通"], ["普通"])
    check(m2 is not None and m2.get("out") == 1.0,
          "互不克制 -> out=1.0（不是 None）",
          str(m2.get("out")) if m2 else "None")

    print()
    print("[10] 物防区间：查得到才给，查不到给 None")
    dr = standalone._foe_def_range("离心舞者")
    check(isinstance(dr, list) and len(dr) == 2,
          "离心舞者 -> [lo, hi]", str(dr))
    if isinstance(dr, list) and len(dr) == 2:
        check(dr[0] < dr[1], "lo < hi", str(dr))
        # 区间不能宽到没意义（正常应是 40~120 这个量级）
        check(0 < dr[0] and dr[1] < 500, "区间在合理范围", str(dr))

    check(standalone._foe_def_range("不存在的精灵名") is None,
          "查不到 -> None")
    check(standalone._foe_def_range(None) is None, "名字 None -> None")

    # 缓存必须真的命中（/api/state 是秒级轮询）
    import time as _t
    _t0 = _t.perf_counter()
    for _ in range(50):
        standalone._foe_def_range("离心舞者")
    _dt = (_t.perf_counter() - _t0) * 1000
    check(_dt < 20, "50 次缓存查询 < 20ms", "%.2fms" % _dt)

    print()
    print("[11] 战斗判定：任一边读到血量就算（不能要求两边都读到）")
    #
    # 背景（2026-11-21 实测 14 张真实战斗帧）
    # ---------------------------------------
    # `perceive.observe()` 的 battle 判定是：
    #     bars = my_pct and foe_pct
    #     battle = bars and my_ui and foe_ui     （或 bars and 技能栏可见）
    #
    # 实测白框检测 0% 命中（分辨率适配问题），导致 battle 恒 False：
    #     读到我方血量 5 (36%) / 对面 4 (29%) / 两边都读到 3 (21%)
    #     我方白框 0 (0%) / 技能栏 0 (0%) / battle=True 0 (0%)
    #
    # 独立计算器不需要"精确判断在战斗"，只需要"有可用数据"。
    # 所以 standalone 改用"任一边读到"的判据。
    #
    # 这里锁住三件事：
    #   1. 只有我方血量 -> battle + bar=True
    #   2. 只有对面血量 -> battle + bar=True
    #   3. 都没有       -> idle + bar=False
    old_bg = (standalone._perc, standalone._screen,
              dict(standalone._cache), dict(standalone._BG))
    try:
        import perceive

        def probe(my_pct, foe_pct):
            scr = FakeScreen()
            fake = perceive.Perc(scr)
            obs = make_fake_observe()
            base = obs()

            def patched(self=None):
                d = dict(base)
                d["my_pct"] = my_pct
                d["foe_pct"] = foe_pct
                # 模拟实测：白框检测失败、技能栏也看不到
                d["my_ui"] = False
                d["foe_ui"] = False
                d["bar"] = False
                d["battle"] = False
                return d

            fake.observe = patched
            standalone.inject_screen(scr, fake)
            standalone._cache = {"at": 0.0, "state": None}
            return standalone.observe(force=True)

        st = probe(92.6, None)
        check(st is not None and st.get("mode") == "battle",
              "只有我方血量 -> mode=battle",
              str(st.get("mode") if st else None))
        check(st is not None and st["system"]["detectors"]["bar"] is True,
              "只有我方血量 -> bar=True")
        check(st is not None and st["me"]["hp_pct"] == 92.6,
              "我方血量保留", str(st["me"]["hp_pct"] if st else None))

        st = probe(None, 35.1)
        check(st is not None and st.get("mode") == "battle",
              "只有对面血量 -> mode=battle",
              str(st.get("mode") if st else None))
        check(st is not None and st["foe"]["hp_pct"] == 35.1,
              "对面血量保留", str(st["foe"]["hp_pct"] if st else None))

        st = probe(None, None)
        check(st is not None and st.get("mode") == "idle",
              "都没读到 -> mode=idle",
              str(st.get("mode") if st else None))
        check(st is not None and st["system"]["detectors"]["bar"] is False,
              "都没读到 -> bar=False")
    finally:
        (standalone._perc, standalone._screen,
         standalone._cache, standalone._BG) = old_bg

    print()
    if fails:
        print("%d FAILURE(S)" % fails)
        return 1
    print("all standalone cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
