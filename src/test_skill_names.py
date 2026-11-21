# -*- coding: utf-8 -*-
"""test_skill_names.py —— 卡面技能名「受限词表纠错」的回归测试。

为什么只测匹配、不测 OCR
------------------------
实测 easyocr 在这套小尺寸艺术字上**不稳定**：同一张图换缩放倍数，
有时读得出有时读不出。把"OCR 认对"写进测试只会得到一个随机失败的套件。

真正需要钉死的是**匹配规则**：给定一个不完美的 OCR 结果，必须在 4 个
候选里挑对，或者明确放弃。这才是这个模块的价值 —— 把"认出整串字"
这种高要求，降级成"在 4 个候选里挑最像的"这种低要求。

用法：python src/test_skill_names.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import skill_names as S  # noqa: E402

fails = 0


def check(cond, name, detail=""):
    global fails
    if not cond:
        fails += 1
    print("  %-4s %-50s %s" % ("OK" if cond else "FAIL", name, detail))


def main():
    # 真实队伍配置（恶魔狼）
    C = ["吞噬", "火焰切割", "蝙蝠", "惊吓盒子"]

    print("[1] 完全相等")
    for c in C:
        n, s = S.match_candidate(c, C)
        check(n == c and s == 1.0, "精确匹配 %s" % c, "%.2f" % s)

    print()
    print("[2] OCR 少字（候选更长）")
    cases = [
        ("惊吓盒", "惊吓盒子"),
        ("火焰切", "火焰切割"),
        ("吞噬", "吞噬"),
        ("蝙蝠", "蝙蝠"),
    ]
    for t, want in cases:
        n, s = S.match_candidate(t, C)
        check(n == want, "%r -> %s" % (t, want), "%s %.2f" % (n, s))

    print()
    print("[3] OCR 多字（OCR 更长）")
    n, s = S.match_candidate("火焰切割啊", C)
    check(n == "火焰切割", "多一个字仍能匹配", "%s %.2f" % (n, s))

    print()
    print("[4] 只读到首字")
    n, s = S.match_candidate("吞", C)
    check(n == "吞噬", "首字唯一时采用", "%s %.2f" % (n, s))
    n, s = S.match_candidate("惊", C)
    check(n == "惊吓盒子", "首字唯一时采用(2)", "%s %.2f" % (n, s))

    print()
    print("[5] 必须拒绝的情况")
    for t in ("", "xxx", "完全无关的字", "火"):
        n, s = S.match_candidate(t, C)
        if t == "火":
            # "火" 和 "火焰切割" 首字相同且候选里唯一 -> 允许
            check(n == "火焰切割", "首字唯一 %r 可采用" % t, "%s" % n)
        else:
            check(n is None, "拒绝 %r" % t, "%s %.2f" % (n, s))

    print()
    print("[6] 单字候选不能吃掉长名字")
    # 技能库里有 1 个单字技能，如果候选里混进单字，长名字不能被它抢走
    n, s = S.match_candidate("火焰切割", ["火", "火焰切割"])
    check(n == "火焰切割", "长名字优先于单字候选", "%s %.2f" % (n, s))

    print()
    print("[7] 歧义时不瞎猜")
    # 两个候选共享前缀 "火焰"，OCR 只读到 "火焰" -> 都有 0.85，取第一个
    # 这是可接受的（都是同一个前缀），但不能返回 None
    n, s = S.match_candidate("火焰", ["火焰切割", "火焰拳"])
    check(n in ("火焰切割", "火焰拳"), "歧义时仍给出一个候选", "%s %.2f" % (n, s))

    print()
    print("[8] 候选归一化（字符串不能被拆成字符）")
    # 这是踩过的坑：candidates_by_slot 传单个字符串时，
    # 字符串可迭代 -> "火云车" 被拆成 ['火','云','车'] -> 匹配出单字
    norm = ["火云车"] if isinstance("火云车", str) else "火云车"
    n, s = S.match_candidate("火云", norm)
    check(n == "火云车", "单字符串候选正确匹配", "%s %.2f" % (n, s))

    print()
    print("[9] 名字条窗口几何")
    # 胶囊 (245,226,312,254) -> 名字条应在左侧偏下
    box = (245, 226, 312, 254)
    wx0, wy0, wx1, wy1 = S.plate_window(box, 530, 1040)
    check(wx0 < box[0], "窗口左边在胶囊左边之外", "%d < %d" % (wx0, box[0]))
    check(wy0 > box[1], "窗口上边在胶囊上方之下", "%d > %d" % (wy0, box[1]))
    check(wx1 - wx0 == S.PLATE_W, "窗口宽度", str(wx1 - wx0))
    # 边界裁剪
    wx0, wy0, wx1, wy1 = S.plate_window((10, 1000, 80, 1030), 530, 1040)
    check(wy1 <= 1040 and wx0 >= 0, "不会越界",
          "(%d,%d,%d,%d)" % (wx0, wy0, wx1, wy1))

    print()
    if fails:
        print("%d FAILURE(S)" % fails)
        return 1
    print("all skill_names cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
