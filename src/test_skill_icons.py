# -*- coding: utf-8 -*-
"""test_skill_icons.py —— 技能图标解析 + 前端契约（不需要游戏）。

    & $py src\\test_skill_icons.py

覆盖：
  1. 技能名 -> URL（有图标的、没图标的）
  2. 直接传技能 id 也能解析
  3. 未知技能/空值 -> None（绝不编造）
  4. live_state.skill_block 的 icon 字段透传
  5. 图标文件真的存在且可解码（防"URL 指向 404"）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s %s" % (name, detail))
    else:
        print("  FAIL %s %s" % (name, detail))
        FAIL.append(name)


def main():
    print("== skill_icons ==")
    import skill_icons as SI
    import live_state as LS

    check("icons imported", SI.available())

    # 1. 已知技能（my_team slot1 的四招）
    cases = {"火云车": "/skill_icons/7040320.png",
             "诋毁": "/skill_icons/7180270.png",
             "闪光冲击": "/skill_icons/7060100.png",
             "借用": "/skill_icons/7020840.png"}
    for nm, want in cases.items():
        got = SI.icon_url(nm)
        check("name->url %s" % nm, got == want, "%r" % (got,))

    # 2. 直接传 id
    check("id->url", SI.icon_url(7040320) == cases["火云车"])

    # 3. 无图标 / 未知
    check("known-missing -> None", SI.icon_url("月蚀") is None)
    check("unknown -> None", SI.icon_url("这个技能不存在") is None)
    check("empty -> None", SI.icon_url(None) is None
          and SI.icon_url("") is None)

    # 4. 契约透传
    blk = LS.skill_block(1, name="火云车", dmg=175, pct=42.0,
                         icon=cases["火云车"])
    check("skill_block carries icon", blk["icon"] == cases["火云车"],
          str(blk["icon"]))
    blk2 = LS.skill_block(2, dmg=100)
    check("skill_block icon default None", blk2["icon"] is None)

    # 5. 文件真实存在 + 可解码
    try:
        import cv2
        import numpy as np
        p = os.path.join(SI.ICON_DIR, "7040320.png")
        img = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_UNCHANGED)
        check("icon file decodable", img is not None,
              str(None if img is None else img.shape))
    except Exception as e:
        check("icon file decodable", False, "%s: %s" % (type(e).__name__, e))

    print()
    if FAIL:
        print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
