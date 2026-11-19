"""rank.py - read the ladder badge and turn match results into win/loss.

The lobby shows "★N/3" next to the division name (中级决斗家 III). Winning adds
a star, losing removes one, so the delta across a match IS the result. That is
the only reliable win/loss signal we have, and without it no amount of tuning
can be called an improvement.

Badge geometry (physical pixels, window-relative):
    star counter   ~(2010, 600, 110, 40)
    division name  ~(1900, 640, 400, 60)

usage:
    python rank.py --now           print what is on screen
    python rank.py --selftest      run the parser on known strings
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, r"D:\Projects\RocoAgent\src")
sys.path.insert(0, r"D:\Tools")

RANK_ROI = (2010, 625, 120, 32)      # the "N/3" digits only
DIV_ROI = (1890, 636, 430, 62)       # "中级决斗家 III"

# 结算页的段位卡：★N/M 在上一行，段位名在下一行。大厅和结算页的徽章位置
# 不一样，所以胜负判定统一从"结算页的卡"读 —— 每次都在同一个位置读同一个
# 元素，才不会把大厅的徽章读成结算页的。
RESULT_STAR_ROI = (1975, 553, 130, 42)
RESULT_DIV_ROI = (1910, 596, 250, 42)

_STAR_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


def _ocr(img_path):
    """Windows OCR via the existing long-lived worker in announce.py."""
    try:
        sys.path.insert(0, r"D:\Tools")
        import announce
        worker = _worker()
        if worker is None:
            return ""
        out = worker.ocr([img_path])
        return out[0] if out else ""
    except Exception:
        return ""


_WORKER = None


def _worker():
    global _WORKER
    if _WORKER is None:
        try:
            import announce
            _WORKER = announce._Ocr()
        except Exception:
            return None
    return _WORKER


def parse_stars(text):
    """"★0/3" or "0/3" or "1/3" -> (1, 3). None when unreadable."""
    if not text:
        return None
    m = _STAR_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1)), int(m.group(2))
    except ValueError:
        return None


def _glyph_to_int(crop, blob):
    """Identify a single digit blob by its shape signature.

    The badge font is fixed, so a handful of cheap invariants separate 0-3 (the
    only counts this division can show). Falls back to None rather than guessing.
    """
    import cv2
    import numpy as np

    x, y, w, h, area = blob
    m = _white_mask(crop)[y:y + h, x:x + w]
    if m.size == 0:
        return None
    fill = float(m.mean())
    # holes: a "0" has one, "3" has none in this font, "1" is a bare stroke
    cnts, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    holes = sum(1 for i in range(len(cnts))
                if hier is not None and hier[0][i][3] != -1)
    if holes >= 1:
        return 0
    if fill < 0.42 and w <= 8:
        return 1
    if fill < 0.62:
        return 2
    return 3


def _white_mask(crop):
    import numpy as np
    mx = crop.max(axis=2).astype(np.int16)
    mn = crop.min(axis=2).astype(np.int16)
    return (((mx - mn) < 40) & (mx > 200)).astype(np.uint8)


def read_star_count(crop):
    """Read 'N/3' from the badge crop by counting white blobs before the slash.

    The badge always renders N and the slash and the total as three separate
    white blobs left to right, so the count of blobs is enough - no OCR, no
    templates, and it works for the art font.
    """
    import cv2
    import numpy as np

    m = _white_mask(crop)
    cnts, _hier = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blobs = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        area = int(cv2.contourArea(c))
        if area < 60 or h < 14 or h > 34:
            continue
        if w > 20:              # 右边那个奖杯图标，不是数字
            continue
        blobs.append((x, y, w, h, area))
    blobs.sort(key=lambda t: t[0])
    if len(blobs) < 3:
        return None
    # 从左到右：[计数] [/] [上限]。计数永远是最左边那个 blob。
    # 旧写法取"斜杠左边那个"，实际取到了斜杠本身（细长、fill 0.32），
    # 被判成 2 —— 这就是"0/3 读成 2/3"的根源。
    n = _glyph_to_int(crop, blobs[0])
    total = _glyph_to_int(crop, blobs[-1])
    if n is None:
        return None
    return n, (total if total else 3)


def read_badge(screen=None):
    """Return {'stars': n, 'of': m, 'raw': text} or None."""
    import cv2
    import numpy as np
    import robot

    scr = screen or robot.Screen()
    if scr.rect is None:
        return None
    crop = scr.grab(RANK_ROI)
    if crop is None or crop.size == 0:
        return None
    got = read_star_count(crop)
    if not got:
        return None
    stars, blobs = got
    return {"stars": stars, "of": 3, "raw": "%d/3 (%d blobs)" % (stars, blobs)}


def _div_sig(crop):
    """段位名的小灰度图（不是哈希）：同名两次渲染会有 1-2 像素抖动，
    用精确相等比对会把它当成换段位。"""
    import cv2
    import numpy as np
    if crop is None or crop.size == 0:
        return None
    g = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    return cv2.resize(g, (32, 10), interpolation=cv2.INTER_AREA).astype(np.float32)


def _div_same(a, b, tol=7.0):
    import numpy as np
    if a is None or b is None:
        return False
    try:
        return float(np.abs(np.asarray(a, np.float32) - np.asarray(b, np.float32)).mean()) < tol
    except Exception:
        return False


def _purple_frac(crop):
    """段位名是紫色粗体。实测：结算页约 25% 像素是紫字，战斗/大厅 ≤0.1%。"""
    import numpy as np
    if crop is None or crop.size == 0:
        return 0.0
    r = crop[:, :, 0].astype(np.int16)
    g = crop[:, :, 1].astype(np.int16)
    b = crop[:, :, 2].astype(np.int16)
    m = (b > 120) & (r > 60) & (b - g > 40) & (r - g > 10)
    return float(m.mean())


def read_card(screen=None):
    """读结算页段位卡：{'stars': n, 'of': m, 'sig': 段位灰度图}。

    星级每场加 1 或 2 颗，升段时清零，所以只看星数差是不够的 —— 必须把
    "段位名有没有变"也带上，才能区分"赢了一场但升段清零"和"输了一场"。

    这张卡只出现在结算页，所以它本身就是"现在是不是结算页"的判据：
    段位名那片紫色不够、或者星级读不出来，就返回 None。
    """
    import robot

    scr = screen or robot.Screen()
    if scr.rect is None:
        return None
    div = scr.grab(RESULT_DIV_ROI)
    got = read_star_count(scr.grab(RESULT_STAR_ROI))
    if not got:
        return None
    stars, of = got
    return {"stars": stars, "of": of, "sig": _div_sig(div)}


def read_ladder(screen=None, ocr=None):
    """读大厅/结算页右侧那张段位卡：星数 + 段位名（OCR）。

    星级读法改成 OCR：那张卡里的"0/3"是美术字，用连通域数形状的分法
    实测会把 0 认成 2（背景白块混进来了）。一场只读一次，2 秒可接受。
    """
    import cv2
    import ocr_cn
    import robot

    scr = screen or robot.Screen()
    if scr.rect is None:
        return None
    if ocr is None:
        ocr = ocr_cn.Ocr()
    star_img = scr.grab(RESULT_STAR_ROI)
    div_img = scr.grab(RESULT_DIV_ROI)
    if star_img is None or div_img is None:
        return None
    star_big = cv2.resize(star_img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    div_big = cv2.resize(div_img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    star_txt = " ".join(t for t, _c in ocr.read_region(star_big))
    div_txt = " ".join(t for t, _c in ocr.read_region(div_big))
    got = parse_stars(star_txt)
    if not got:
        return None
    return {"stars": got[0], "of": got[1], "div": div_txt.strip()}


def ladder_result(before, after):
    """两次读段位卡 -> 1 胜 / -1 负 / 0 没变 / None 判不出。"""
    if not before or not after:
        return None
    same_div = (before.get("div") or "") == (after.get("div") or "")
    if same_div:
        d = after["stars"] - before["stars"]
        return 1 if d > 0 else (-1 if d < 0 else 0)
    # 段位名变了：满星清零=升段（胜），零星变多=掉段（负）
    if before["stars"] >= before["of"] - 1 and after["stars"] <= 1:
        return 1
    if before["stars"] <= 0 and after["stars"] >= max(0, after["of"] - 2):
        return -1
    return None


def result_between(before, after):
    """比较两次结算卡 -> 1 胜 / -1 负 / 0 没变 / None 判不出。"""
    if not before or not after:
        return None
    if _div_same(before.get("sig"), after.get("sig")):
        d = after["stars"] - before["stars"]
        return 1 if d > 0 else (-1 if d < 0 else 0)
    # 段位变了：满星后清零是升段（赢），零星后变多是掉段（输）
    if before["stars"] >= before["of"] - 1 and after["stars"] <= 1:
        return 1
    if before["stars"] <= 0 and after["stars"] >= max(0, after["of"] - 2):
        return -1
    # 判不出来就明说，不要猜
    return None


def result_from_delta(before, after):
    """+1 win, -1 loss, 0 unchanged, None unknown."""
    if not before or not after:
        return None
    if before["of"] != after["of"]:
        return None
    d = after["stars"] - before["stars"]
    if d > 0:
        return 1
    if d < 0:
        return -1
    return 0


def selftest():
    cases = [
        ("★0/3", (0, 3)),
        ("1/3", (1, 3)),
        ("★ 2 / 3", (2, 3)),
        ("中级决斗家 III", None),
        ("", None),
    ]
    bad = 0
    for text, want in cases:
        got = parse_stars(text)
        ok = got == want
        if not ok:
            bad += 1
        print("%-20r -> %-8s %s" % (text, got, "OK" if ok else "**FAIL**"))
    print()
    b, a = {"stars": 1, "of": 3}, {"stars": 2, "of": 3}
    print("1/3 -> 2/3 :", result_from_delta(b, a), "(want 1)")
    print("1/3 -> 0/3 :", result_from_delta(b, {"stars": 0, "of": 3}), "(want -1)")
    print("1/3 -> 1/3 :", result_from_delta(b, {"stars": 1, "of": 3}), "(want 0)")
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    r = read_badge()
    print(r if r else "badge unreadable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
