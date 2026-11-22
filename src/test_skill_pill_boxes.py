# -*- coding: utf-8 -*-
"""test_skill_pill_boxes.py —— 名字带 ROI 几何的回归（离线合成帧）。

验证 perceive.skill_pill_boxes 的两条约定：
  1. 4 个胶囊按 y 格子分槽，坐标平移回整帧客户区（SKILL_BOX 偏移）
  2. session_moves.read_band 用该 box 抓的带子正确落在名字区域

不需要游戏、不需要真卡面；合成帧严格遵守 digitread.find_pills 的
检测约束（BG 色 61/61/61、44<=w<=130、22<=h<=52、内部白字块）。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"D:\Tools")

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s %s" % (name, detail))
    else:
        print("  FAIL %s %s" % (name, detail))
        FAIL.append(name)


class FakeScreen:
    client_size = (2560, 1600)

    def __init__(self, frame):
        self.frame = frame

    def grab_client(self, box):
        x, y, w, h = box
        return self.frame[y:y + h, x:x + w]


def build_frame(with_names=True):
    """4 个胶囊 y=300/480/660/840（技能栏裁图内），可选贴名字带。"""
    frame = np.full((1600, 2560, 3), 30, np.uint8)
    crop = frame[480:1520, 250:780]
    names = {}
    for k, y in enumerate((300, 480, 660, 840)):
        crop[y:y + 34, 200:265] = (61, 61, 61)
        crop[y + 6:y + 28, 212:250] = (240, 240, 240)
        if with_names:
            band = np.full((40, 140, 3), 90, np.uint8)
            x0, y0 = 250 + 130, 480 + y + 34
            frame[y0:y0 + 40, x0:x0 + 140] = band
            names[k + 1] = (x0, y0)
    return frame, names


def main():
    print("== skill_pill_boxes ==")
    import perceive as P
    import session_moves as SM

    frame, names = build_frame()
    fs = FakeScreen(frame)
    boxes = P.skill_pill_boxes(fs)
    check("4 boxes found", len(boxes) == 4 and all(boxes), str(boxes))
    if len(boxes) == 4 and all(boxes):
        exp = [(250 + 200, 480 + y, 250 + 265, 480 + y + 34)
               for y in (300, 480, 660, 840)]
        check("lattice geometry", all(tuple(g) == w for g, w in zip(boxes, exp)),
              str(boxes[0]))

        # read_band 的 ROI 必须落在我们贴的名字带上
        SM.STORE, SM._DATA, SM._DIRTY = \
            os.path.join(os.environ.get("TEMP", "/tmp"), "sm_test.json"), \
            {"pets": {}, "stale": {}}, False
        try:
            got = SM.read_band("测试精灵", 1, fs, boxes[0])
            check("band roi safe (no text -> None)",
                  got[0] is None, str(got))
            st = SM.band_stats()
            check("band cache touched", st["cached"] >= 1, str(st))
        finally:
            try:
                os.remove(SM.STORE)
            except OSError:
                pass

    # ---- 裁图范围回归（缺陷 1：角点当宽高）-------------------------
    #
    # 旧实现把 SKILL_BOX 的角点当宽高传给 grab_client，实机 4:3 下
    # 多裁掉大片技能栏下方区域（基准坐标 y 1520~2000），find_pills
    # 在空白处检出垃圾胶囊，实测整帧坐标 (250,1401,334,1442)。
    #
    # 合成帧测不出这一条：真胶囊有 4 个时旧实现也能收齐（诱饵被挤掉）。
    # 所以这里直接断言**请求给 Screen 的矩形**必须是 w=x1-x0、h=y1-y0。
    class SpyScreen:
        client_size = (2560, 1600)

        def __init__(self, frame):
            self.frame = frame
            self.asked = []

        def grab_client(self, box):
            self.asked.append(tuple(box))
            x, y, w, h = box
            return self.frame[y:y + h, x:x + w]

    spy = SpyScreen(frame)
    P.skill_pill_boxes(spy)
    import config as C
    x0, y0, x1, y1 = C.SKILL_BOX
    check("grab rect is w/h (not corners)",
          spy.asked == [(x0, y0, x1 - x0, y1 - y0)], str(spy.asked))

    # ---- 缺卡时必须保留 None 占位，不能压缩列表 ---------------------
    #
    # 压缩会让索引错位：pending={2,4} 时 boxes 只剩 2 项，调用方拿
    # slot=2 的索引读到的其实是槽 3 的盒子。旧实现返回 2 项，这里必须 4 项。
    partial = np.full((1600, 2560, 3), 30, np.uint8)
    for y in (300, 660):                       # 只画槽 1、3
        partial[480 + y:480 + y + 34, 450:515] = (61, 61, 61)
        partial[480 + y + 6:480 + y + 28, 462:500] = (240, 240, 240)
    pboxes = P.skill_pill_boxes(FakeScreen(partial))
    check("partial keeps slot index", len(pboxes) == 4, str(pboxes))
    check("partial slot2 missing", len(pboxes) == 4 and pboxes[1] is None,
          str(pboxes))
    check("partial slot1 present", len(pboxes) == 4 and pboxes[0] is not None,
          str(pboxes))

    # 没画胶囊时必须返回空（不抛异常）
    empty = np.zeros((1600, 2560, 3), np.uint8)
    check("empty frame safe", P.skill_pill_boxes(FakeScreen(empty)) == [])

    print()
    if FAIL:
        print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
