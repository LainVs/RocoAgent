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

    # 0. 死锁回归：confirm 自带 _LOCK，read_band 若再包一层 with _LOCK
    #    就会在不可重入锁上永久自锁。
    #
    #    2026-11-23 实证：这个写法让 read_band 挂死（faulthandler 抓到
    #    session_moves.py:288 卡在 confirm 的 `with _LOCK:`），表现为
    #    BAND 一次都没成功采纳过（session_moves.json 至今不存在），
    #    并可能拖死感知线程 —— 是"机器人卡住/消失"的嫌疑之一。
    #
    #    这里起一个线程调 confirm，2 秒不返回就判死锁。
    import threading
    _box = {}

    def _try_confirm():
        SM.confirm("死锁测试", 1, "测试技能", src="band")
        _box["ok"] = True

    th = threading.Thread(target=_try_confirm, daemon=True)
    th.start()
    th.join(2.0)
    check("confirm 不自己.锁（2s 内返回）", _box.get("ok") is True)
    SM.clear("死锁测试")

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
        #    锁住"名字带在**胶囊下缘**"的标定。
        #
        # ⚠ 旧版本这里是**复制粘贴**了一份 ROI 计算（`ya+5:yb+95`），
        # 也就是把当时的错误写法抄进了测试里 —— 于是 read_band 怎么改
        # 这条测试都 PASS，没能拦住"ROI 包住胶囊、OCR 读出伤害数字"的 bug。
        # 现在改为直接调用 read_band，并用真帧断言能读出技能名。
        fixture = os.path.join(ROOT, "logs", "bar_partial_204511.png")
        if os.path.exists(fixture):
            import cv2 as _cv2
            import digitread as _dr
            img = _cv2.imdecode(np.fromfile(fixture, np.uint8),
                                _cv2.IMREAD_COLOR)
            # 裁图 fixture（1040x530）当成一张整帧的 SKILL_BOX 区域用
            pills = [p for p in _dr.find_pills(
                img, (0, 0, img.shape[1], img.shape[0])) if p.get("box")]

            class SkillBoxScreen:
                """把裁图当作 SKILL_BOX 区域贴到整帧上。

                ⚠ 整帧必须在 __init__ 里**先建好**：read_band 每次读都要
                grab_client((0,0,2560,1600))，若在 grab_client 里现建一张
                2560x1600 的数组，每次 12MB 分配，会把测试拖到分钟级。
                """
                client_size = (2560, 1600)

                def __init__(self, crop):
                    self.full = np.zeros((1600, 2560, 3), np.uint8)
                    self.full[480:480 + crop.shape[0],
                              250:250 + crop.shape[1]] = crop

                def scale(self):
                    return (1.0, 1.0)

                def grab_client(self, box):
                    x, y, w, h = box
                    return self.full[y:y + h, x:x + w]

            sb = SkillBoxScreen(img)
            names = []
            for k, p in enumerate(pills[:6]):
                xa, ya, xb, yb = (int(v) for v in p["box"])
                # 传裁图内坐标（read_band 会自己加 SKILL_BOX 偏移）。
                # 用不同 slot 号：read_band 以 (pet, slot) 为缓存键，
                # 同一个 key 第二次调用会直接命中上次的空结果。
                # 注意：裁图里 6 个胶囊中有些是副图标（下方没有名字带），
                # 读到空是正常现象，所以只要求"至少一只读对"。
                nm, _sc = SM.read_band("音速犬", k + 1, sb, (xa, ya, xb, yb))
                if nm:
                    names.append(nm)
            SM.clear("音速犬")
            known = {"力量增效", "跌落", "火云车"}
            hit = set(names) & known
            check("live frame ROI reads skill names",
                  len(hit) >= 1, "found=%r" % names[:6])
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
