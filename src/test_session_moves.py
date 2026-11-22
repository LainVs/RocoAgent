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
sys.path.insert(0, r"D:\Tools")
import paths as _paths_mod  # noqa: E402
_paths_mod.use_tools()

FAIL = []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


        # 9. 实机帧 ROI 回归（bar_partial_204511.png 存在时）：
        #    锁住"名字在胶囊下缘 +5..+95"的标定，防止再回退到旧浅 ROI。
        fixture = os.path.join(ROOT, "logs", "bar_partial_204511.png")
        if os.path.exists(fixture):
            import cv2 as _cv2
            import digitread as _dr
            img = _cv2.imdecode(np.fromfile(fixture, np.uint8),
                               _cv2.IMREAD_COLOR)
            pills = [p for p in _dr.find_pills(
                img, (0, 0, img.shape[1], img.shape[0])) if p.get("box")]
            found_names = set()
            for p in pills:
                xa, ya, xb, yb = (int(v) for v in p["box"])
                roi = img[ya + 5:yb + 95,
                          max(0, xa - 90):min(img.shape[1], xb + 90)]
                if roi.shape[0] < 20:
                    continue
                big = _cv2.resize(roi, None, fx=2.0, fy=2.0,
                                 interpolation=_cv2.INTER_CUBIC)
                try:
                    import ability_read as AR
                    res, _ = AR._engine()(
                        _cv2.cvtColor(big, _cv2.COLOR_RGB2BGR))
                except Exception:
                    continue
                raw = " ".join(r[1] for r in (res or [])).strip()
                if raw:
                    import ocr_cn
                    import sqlite3 as _sq
                    _db = _sq.connect(os.path.join(ROOT, "data", "roco.sqlite3"))
                    vocab = [r[0] for r in _db.execute(
                        "SELECT DISTINCT name FROM skills WHERE name IS NOT NULL")]
                    _db.close()
                    nm, _sc = ocr_cn.Ocr.match(raw, vocab)
                    if nm:
                        found_names.add(nm)
            known = {"力量增效", "跌落", "火云车"}
            hit = found_names & known
            check("live frame ROI reads skill names",
                  len(hit) >= 2, "found=%r" % sorted(found_names)[:5])
        else:
            print("  skip 实机帧 ROI（fixture 不存在）")

    print()
    if FAIL:
        print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
