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
    # A "0" is a ring, so a plain connected-components pass reports the hole as
    # its own blob and the count comes out one too high. Keep only outer
    # contours (RETR_EXTERNAL) so each glyph is exactly one blob.
    cnts, _hier = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blobs = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        area = int(cv2.contourArea(c))
        if area < 60 or h < 18 or h > 48:
            continue
        blobs.append((x, y, w, h, area))
    blobs.sort(key=lambda t: t[0])
    if len(blobs) < 3:
        return None
    # Blobs run left to right: [count] [slash] [total]. The count is everything
    # before the slash, so its blob index is len-2 for "N/3" and len-3 for
    # "NN/3" - i.e. always the blob immediately left of the slash.
    left = blobs[:-2]
    if not left:
        return None
    n = _glyph_to_int(crop, left[-1])
    if n is None:
        return None
    return n, len(blobs) - 1


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
