# -*- coding: utf-8 -*-
"""test_record_geometry.py —— 战绩面板坐标的分辨率无关性验证。

为什么需要
----------
`record.py` 的三个坐标常量都是**基准 2560x1600 下的客户区坐标**：

    OPEN_CLICK  = (200, 1042)          # 左下角"历史战绩"图标
    CLOSE_CLICK = (2012, 422)          # 面板右上角 X
    RECORD_BOX  = (1040, 525, 460, 70) # "胜利 N 失败 M" 那一行

实际运行时通过 `screen.phys_client()` / `screen.sbox_client()` 按当前
客户区尺寸换算。所以只要基准值对，任何分辨率都应该对。

但 2026-11-21 实测踩到：`OPEN_CLICK` 原来是 (191, 943)，在 2048x1536 下
点到图标上方的空地，**战绩面板根本没打开**，OCR 全是空字符串，
于是胜负从来没被记录（`results.jsonl` 里大量 `flat`）。

这个测试把"基准坐标 -> 各分辨率物理坐标"的换算钉死，防止再写错。

跑法：
    python src\\test_record_geometry.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import record


def phys(box_or_point, cw, ch):
    """按 Screen.scale() 的同一公式换算（基准 2560x1600）。"""
    sx, sy = cw / 2560.0, ch / 1600.0
    if len(box_or_point) == 2:
        x, y = box_or_point
        return (int(round(x * sx)), int(round(y * sy)))
    x, y, w, h = box_or_point
    return (int(round(x * sx)), int(round(y * sy)),
            max(1, int(round(w * sx))), max(1, int(round(h * sy))))


def main():
    fails = 0

    def check(cond, name, detail=""):
        nonlocal fails
        if not cond:
            fails += 1
        print("  %-4s %-52s %s" % ("OK" if cond else "FAIL", name, detail))

    print("=== 基准坐标（应全部落在 2560x1600 内）===")
    ox, oy = record.OPEN_CLICK
    cx, cy = record.CLOSE_CLICK
    bx, by, bw, bh = record.RECORD_BOX
    check(0 < ox < 2560 and 0 < oy < 1600, "OPEN_CLICK 在基准画布内",
          str(record.OPEN_CLICK))
    check(0 < cx < 2560 and 0 < cy < 1600, "CLOSE_CLICK 在基准画布内",
          str(record.CLOSE_CLICK))
    check(0 < bx and bx + bw <= 2560 and 0 < by and by + bh <= 1600,
          "RECORD_BOX 在基准画布内", str(record.RECORD_BOX))

    print()
    print("=== 历史 bug：OPEN_CLICK 的 y 必须 >= 1000 ===")
    # 旧值 (191, 943) 点在图标上方空地。实测图标中心 y=1042。
    check(oy >= 1000, "OPEN_CLICK.y >= 1000（图标在左下，不是 y=943 的空地）",
          "y=%d" % oy)

    print()
    print("=== 各分辨率下的换算（应全部在画布内）===")
    for cw, ch in ((2560, 1600), (2048, 1536), (1920, 1080), (1280, 800),
                   (1600, 900), (1024, 768)):
        px, py = phys(record.OPEN_CLICK, cw, ch)
        qx, qy = phys(record.CLOSE_CLICK, cw, ch)
        rx, ry, rw, rh = phys(record.RECORD_BOX, cw, ch)
        ok = (0 <= px < cw and 0 <= py < ch
              and 0 <= qx < cw and 0 <= qy < ch
              and rx >= 0 and ry >= 0 and rx + rw <= cw and ry + rh <= ch)
        check(ok, "%dx%d 三个框都在画布内" % (cw, ch),
              "click=(%d,%d) box=(%d,%d,%d,%d)" % (px, py, rx, ry, rw, rh))

    print()
    print("=== 换算后的宽高比应与基准一致（防止非等比变形）===")
    #
    # record.read() 现在用 `_grab_box_keep_ar`：位置按 (sx, sy) 各自缩放
    # （HUD 是贴边锚定的，位置该按各轴走），但**尺寸只按 sx 缩放**，
    # 再缩回基准尺寸 —— 这样文字比例始终不变。
    #
    # 所以这里验证的是"按 sx 缩放后宽高比是否保持"，而不是旧公式。
    for cw, ch in ((2048, 1536), (1920, 1080), (1280, 800)):
        sx = cw / 2560.0
        pw = max(1, int(round(bw * sx)))
        ph = max(1, int(round(bh * sx)))     # 注意：也用 sx
        base_ar = bw / float(bh)
        got_ar = pw / float(ph)
        ok = abs(got_ar - base_ar) / base_ar < 0.05
        check(ok, "%dx%d 按 sx 等比缩放，宽高比保持" % (cw, ch),
              "%.2f vs %.2f" % (got_ar, base_ar))

    print()
    print("=== 旧的非等比公式会变形（记录教训，断言它确实会变形）===")
    for cw, ch in ((2048, 1536), (1920, 1080)):
        sx, sy = cw / 2560.0, ch / 1600.0
        pw = max(1, int(round(bw * sx)))
        ph = max(1, int(round(bh * sy)))
        base_ar = bw / float(bh)
        got_ar = pw / float(ph)
        distorted = abs(got_ar - base_ar) / base_ar > 0.05
        check(distorted, "%dx%d 旧公式确实会变形（所以必须用 keep_ar）"
              % (cw, ch), "%.2f vs %.2f" % (got_ar, base_ar))

    print()
    if fails:
        print("%d FAILURE(S)" % fails)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
