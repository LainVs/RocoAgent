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
        standalone._screen = FakeScreen()
        fake = perceive.Perc(standalone._screen)
        fake.observe = make_fake_observe().__get__(fake, type(fake))
        standalone._perc = fake
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
        check(sk[2]["usable"] is True, "dash 卡可用")
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
        standalone._perc = Boom()
        standalone._screen = FakeScreen()
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
    finally:
        standalone._ident.clear()
        standalone._ident.update(old3)

    print()
    if fails:
        print("%d FAILURE(S)" % fails)
        return 1
    print("all standalone cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
