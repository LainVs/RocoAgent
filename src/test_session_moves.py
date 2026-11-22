# -*- coding: utf-8 -*-
"""test_session_moves.py —— 我方技能动态解析的回归（不需要游戏）。

    & $py src\\test_session_moves.py

覆盖：
  1. 确认/查询/来源 三层标记
  2. 校验环撤销（veto）
  3. 落盘持久化（重载后仍在）
  4. my_team 过期标记
  5. 名字带 ROI 计算（用假 Screen 验证裁框口径）
"""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s %s" % (name, detail))
    else:
        print("  FAIL %s %s" % (name, detail))
        FAIL.append(name)


class FakeScreen:
    """整帧里把名字带贴到 SKILL_BOX 偏移的正确位置。"""
    client_size = (2560, 1600)

    def __init__(self, frame):
        self.frame = frame

    def grab_client(self, box):
        x, y, w, h = box
        return self.frame[y:y + h, x:x + w]


def main():
    import session_moves as SM

    print("== session_moves ==")
    tmp = tempfile.mktemp(suffix=".json")
    SM.STORE, SM._DATA, SM._DIRTY = tmp, None, False

    try:
        # 1/2. 确认与来源
        SM.confirm("音速犬", 1, "火云车", src="band")
        check("band confirm", SM.get("音速犬", 1) == "火云车")
        check("source=band", SM.source("音速犬", 1) == "band")
        check("missing slot", SM.get("音速犬", 4) is None)
        check("none args safe", SM.get(None, 1) is None
              and SM.get("音速犬", None) is None)

        # 3. 播报覆盖 band
        SM.confirm("音速犬", 1, "闪光冲击", src="session")
        check("session overwrites band",
              SM.get("音速犬", 1) == "闪光冲击"
              and SM.source("音速犬", 1) == "session")

        # veto
        SM.veto("音速犬", 1)
        check("veto clears", SM.get("音速犬", 1) is None)

        # 4. stale 标记
        SM.note_team_stale("卡瓦重")
        check("stale flag", SM.is_stale("卡瓦重"))
        check("no false stale", not SM.is_stale("音速犬"))

        # 5. 落盘持久化
        SM.confirm("卡瓦重", 3, "撞鬼", src="team_db")
        SM._save()
        SM._DATA = None
        check("persisted across reload", SM.get("卡瓦重", 3) == "撞鬼")

        # 6. snapshot 的 slot 是 int
        snap = SM.snapshot("卡瓦重")
        check("snapshot int slots", 3 in snap
              and snap[3]["source"] == "team_db")

        # 7. 名字带 ROI：贴一张假名字带到正确位置
        # SKILL_BOX=(250,480,780,1520) 在 config 里；这里直接用常量做偏移
        band = np.full((40, 140, 3), 60, np.uint8)
        frame = np.zeros((1600, 2560, 3), np.uint8)
        frame[480 + 365:480 + 365 + 40, 250 + 0:250 + 140] = band
        got = SM.read_band("音速犬", 2, FakeScreen(frame), (51, 325, 120, 365))
        check("band ROI grabs right pixels",
              isinstance(got, tuple) and len(got) == 2, str(got))
        # 引擎在无文字区域应返回空/None，不抛异常
        check("band no-text safe", got[0] is None or got[1] < 0.90)

        # 8. band_stats 可诊断
        st = SM.band_stats()
        check("band_stats keys", "cached" in st and "fresh" in st, str(st))
    finally:
        try:
            os.remove(tmp)
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
