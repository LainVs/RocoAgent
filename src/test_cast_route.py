# -*- coding: utf-8 -*-
"""test_cast_route.py —— 播报敌我分流的回归（不需要游戏）。

覆盖 robot._cast_ocr 里新加的两段纯逻辑：
  1. _name_close：编辑距离 1 的敌我判定
  2. 播报 -> session_moves 确认的槽位归因（用 monkeypatch 打桩 hud）
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s %s" % (name, detail))
    else:
        print("  FAIL %s %s" % (name, detail))
        FAIL.append(name)


def main():
    import robot as R
    import session_moves as SM

    print("== cast routing ==")
    SM.STORE, SM._DATA, SM._DIRTY = \
        tempfile.mktemp(suffix=".json"), None, False

    try:
        # 1. _name_close
        check("exact", R._name_close("音速犬", "音速犬"))
        check("one typo", R._name_close("音速天", "音速犬"))
        check("different pets", not R._name_close("音速犬", "恶魔狼"))
        check("empty safe", not R._name_close("", "音速犬")
              and not R._name_close("音速犬", ""))
        check("length gap", not R._name_close("犬", "音速犬"))
        check("prefix-ish ok", R._name_close("寂灭骨龙", "寂灭骨尤"))

        # 2. 播报 -> 确认：打桩 hud.read_cast_text
        class FakeScreen:
            client_size = (2560, 1600)

            def grab_client(self, box):
                import numpy as np
                return np.zeros((box[3], box[2], 3), np.uint8)

        class Bot:
            screen = FakeScreen()
            my_name = "音速犬"
            foe_name = "白发路路"
            _last_pressed_slot = 2
            _last_pressed_at = time.time()
            _cast_at = 0.0
            _skv = None

            _skill_vocab = R.Bot._skill_vocab
            _cast_ocr = R.Bot._cast_ocr

        st = {"battle": True}
        real_hud = sys.modules.get("hud")
        try:
            import types
            fake_hud = types.ModuleType("hud")
            fake_hud.read_cast_text = lambda screen: "音速天 使出了 火云车！"
            fake_hud.skill_vocab = lambda db_path=None: ["火云车", "诋毁"]
            sys.modules["hud"] = fake_hud
            b = Bot()
            b._cast_ocr(st)
        finally:
            if real_hud is not None:
                sys.modules["hud"] = real_hud

        check("cast confirms slot2",
              SM.get("音速犬", 2) == "火云车",
              repr(SM.snapshot("音速犬")))
        check("source=session", SM.source("音速犬", 2) == "session")

        # 3. 对面播报不写我方缓存
        SM.clear("音速犬")
        try:
            import types
            fake_hud = types.ModuleType("hud")
            fake_hud.read_cast_text = lambda screen: "白发路路 使出了 诋毁！"
            fake_hud.skill_vocab = lambda db_path=None: ["火云车", "诋毁"]
            sys.modules["hud"] = fake_hud
            b = Bot()
            b._cast_ocr(st)
        finally:
            if real_hud is not None:
                sys.modules["hud"] = real_hud
        check("foe cast ignored for my slots",
              SM.get("音速犬", 2) is None)
    finally:
        try:
            os.remove(SM.STORE)
        except OSError:
            pass

    print()
    if FAIL:
        print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
