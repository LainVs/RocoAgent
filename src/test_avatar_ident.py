# -*- coding: utf-8 -*-
"""test_avatar_ident.py —— 头像通道的回归测试（不需要游戏、不需要 BLE）。

    & $py src\\test_avatar_ident.py

检查的是**行为契约**，不是精度数字（精度见 scripts\\bench_avatar_ident.py）：

  1. 银行能构建/加载，形状自洽
  2. 已标注实机裁图能认对（带属性候选池）
  3. 高置信票不许出错（这是线上唯一的自动定名依据）
  4. 候选池里没有正确答案时，返回空而不是瞎猜
  5. 空白图 / 未知图不能给出高置信
  6. 名字归一化（括号、首领前缀）
  7. identify_screen 的适配层不抛异常

失败退出码 1，末行打印 ALL PASS / FAILED N。
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import avatar_ident as A          # noqa: E402
import foe_ident                  # noqa: E402
import portrait_db as P           # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOE_DIR = os.path.join(ROOT, "assets", "portraits_foe")

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s %s" % (name, detail))
    else:
        print("  FAIL %s %s" % (name, detail))
        FAIL.append(name)


def imread_c(path):
    buf = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR) if buf.size else None


def pool_of(name):
    types = foe_ident.types_of(name) or []
    return foe_ident.candidates_by_types(types) if types else None


class FakeScreen:
    """只要 grab_client / client_size，identify_screen 就能跑。"""
    client_size = (2560, 1600)

    def __init__(self, frame):
        self.frame = frame

    def grab_client(self, box):
        x, y, w, h = box
        return self.frame[y:y + h, x:x + w]


def main():
    print("== avatar_ident ==")

    # 1. 银行
    A._BANKS.clear()
    A._SIG["sig"] = None
    b = A.bank("foe")
    if b is None:
        print("  SKIP  no assets/pet_avatars on this machine")
        return 0
    check("bank has >600 ids", len(b["ids"]) > 600, "%d" % len(b["ids"]))
    check("bank dim == S*S*3", b["AT"].shape[0] == A.S * A.S * 3,
          str(b["AT"].shape))
    check("views per id >= 8", b["AT"].shape[1] >= 8 * len(b["ids"]),
          "%d views / %d ids" % (b["AT"].shape[1], len(b["ids"])))
    check("owner index in range",
          int(b["owner"].min()) >= 0 and int(b["owner"].max()) < len(b["ids"]))
    check("cache file written", os.path.exists(A._bank_path("foe")))

    # 2/3. 已标注裁图：认对 + 高置信不出错
    cases = ("寂灭骨龙.png", "冰钻布鲁斯.png", "彩蝶鲨.png", "水灵.png",
             "恶魔狼.png", "音速犬.png")
    hit = high = 0
    high_wrong = []
    for stem in cases:
        p = os.path.join(FOE_DIR, stem)
        if not os.path.exists(p):
            continue
        img = imread_c(p)
        sid = P.species_of(stem, "foe")
        if img is None or not sid:
            continue
        want = P.species_name(sid)
        res = A.identify(img, side="foe", candidates=pool_of(want),
                         is_rgb=False, topk=2)
        got = res[0] if res else {}
        ok = A.normalize(got.get("name")) == A.normalize(want)
        hit += 1 if ok else 0
        if got.get("conf") == "high":
            high += 1
            if not ok:
                high_wrong.append((stem, got.get("name")))
        print("       %-16s -> %-16s %.3f %s"
              % (stem, got.get("name"), got.get("score") or 0,
                 got.get("conf")))
    check("known crops identified", hit >= 4, "%d/%d" % (hit, len(cases)))
    check("high-confidence votes exist", high >= 2, "%d" % high)
    check("no high-confidence mistakes", not high_wrong, str(high_wrong))

    # 4. 候选池排除真值 -> 不猜
    img = imread_c(os.path.join(FOE_DIR, "寂灭骨龙.png"))
    if img is not None:
        res = A.identify(img, side="foe", candidates=["不存在的精灵"],
                         is_rgb=False)
        check("absent candidates -> empty", res == [], str(res[:1]))

    # 5. 空白图不能高置信
    blank = np.full((96, 104, 3), 128, np.uint8)
    res = A.identify(blank, side="foe", is_rgb=True)
    check("blank image not high",
          not res or res[0]["conf"] != "high",
          str(res[:1]))

    # 6. 名字归一化
    check("normalize paren", A.normalize("冥古龙（首领形态）") == "冥古龙")
    check("normalize leader", A.normalize("首领-恶魔狼") == "恶魔狼")
    check("normalize plain", A.normalize("音速犬") == "音速犬")

    # 7. identify_screen：贴一张真实裁图到整帧的正确位置，验证抓框口径
    if img is not None:
        import portraits
        box = portraits.foe_box_for(2560, 1600, margin=0)
        frame = np.zeros((1600, 2560, 3), np.uint8)
        x, y, w, h = box
        # Screen.grab_client 返回 RGB，imread 给的是 BGR
        frame[y:y + h, x:x + w] = img[:, :, ::-1]
        got = A.identify_screen(FakeScreen(frame), side="foe",
                                candidates=pool_of("寂灭骨龙"))
        check("identify_screen returns list", isinstance(got, list),
              str(got[:1]))
        check("identify_screen gets the right pet",
              bool(got) and A.normalize(got[0]["name"]) == "寂灭骨龙",
              str(got[:1]))

        # 8. 融合链：名字/模板都失效时，avatar 通道要能定名
        #
        # 直接打桩，避免在这条测试里跑 5 秒 OCR；重点是验证
        # foe_ident 的调用顺序和属性否决真的生效。
        import foe_ident
        real_types = foe_ident.hud_ident.read_types
        real_name = foe_ident.hud_ident.read_name
        real_av = A.identify_screen
        try:
            foe_ident.hud_ident.read_types = lambda *a, **k: ["幽", "龙"]
            foe_ident.hud_ident.read_name = lambda *a, **k: ("", 0.0)
            A.identify_screen = lambda *a, **k: [{
                "id": 3269, "name": "寂灭骨龙", "score": 0.9,
                "margin": 0.3, "conf": "high"}]
            r = foe_ident.identify(FakeScreen(frame), side="foe")
            check("foe_ident uses avatar channel",
                  r.get("source") == "avatar" and r.get("name") == "寂灭骨龙",
                  "%s/%s" % (r.get("source"), r.get("name")))
        finally:
            foe_ident.hud_ident.read_types = real_types
            foe_ident.hud_ident.read_name = real_name
            A.identify_screen = real_av

    print()
    if FAIL:
        print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
