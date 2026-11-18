"""energy.py - read the bottom-left energy counter 'N/10'.

The HUD shows a small white '\u2605N/10' on a dark rounded panel at window-rel
(110,1525)-(200,1557). White text on dark is the easiest OCR target in the whole
game, so we binarise and template-match with digitread's glyph engine.
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, r"D:\Tools")
import digitread as dr  # noqa: E402

ENERGY_ROI = (104, 1522, 108, 38)      # window-relative (x, y, w, h)

# Rebuilt from live frames: 0..9 from this HUD font.
_TPL = None
_TPL_PATH = r"D:\Projects\RocoAgent\logs\digits\energy_tpl.npz"


def _masks(reg):
    """White-text mask: bright and near-neutral (the panel is dark grey)."""
    r = reg[:, :, 0].astype(np.int16)
    g = reg[:, :, 1].astype(np.int16)
    b = reg[:, :, 2].astype(np.int16)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    white = ((mx > 175) & ((mx - mn) < 45)).astype(np.uint8)
    return white


def _glyphs(reg):
    """Segment the white digits of 'N/10'; keep only the part before '/'."""
    m = _masks(reg)
    # the '/' is a thin slanted stroke; digits are wider blobs. Cut at the first
    # column gap wider than 3px after the leading digits.
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    blobs = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if h < 10 or h > 26 or a < 25 or w > 22:
            continue
        blobs.append((x, y, w, h, a))
    blobs.sort(key=lambda t: t[0])
    return blobs, m


def read_energy(rgb, roi=ENERGY_ROI):
    """Energy 0..10.

    The readout is always 'N/10' or '10/10', so the blob count alone tells us
    which case we are in: 4 blobs = one energy digit + '/', '1', '0';
    5 blobs = '1','0','/', '1','0'.
    """
    x, y, w, h = roi
    if y + h > rgb.shape[0] or x + w > rgb.shape[1]:
        return None
    reg = rgb[y:y + h, x:x + w]
    blobs, m = _glyphs(reg)
    if len(blobs) == 5:
        return 10
    if len(blobs) != 4:
        return None
    bx, by, bw, bh, ba = blobs[0]
    g = m[by:by + bh, bx:bx + bw].astype(np.float32)
    lab, score = _classify(np.clip(g, 0, 1), bw / float(bh))
    if score < 0.55 or not isinstance(lab, int):
        return None
    return lab


_DIGITS = None


def _classify(glyph, aspect):
    """Delegate to digitread's template matcher (it resizes to the proto size)."""
    global _DIGITS
    if _DIGITS is None:
        _DIGITS = dr.load_templates()
    if _DIGITS is None:
        return "?", 0.0
    lab, score = dr.classify(glyph, aspect, _DIGITS)
    return lab, score


def _load_digits():
    if os.path.exists(_TPL_PATH):
        z = np.load(_TPL_PATH)
        return {k: z[k] for k in z.files}
    return _build_digits()


def _build_digits():
    """Harvest digit shapes from the two calibration frames shipped with the repo."""
    src = [
        (r"D:\Projects\RocoAgent\logs\frames2\024605_0416.png", 0),
        (r"D:\Projects\RocoAgent\logs\k1.png", 1),
    ]
    out = {}
    for path, _ in src:
        if not os.path.exists(path):
            continue
        im = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]
        x, y, w, h = ENERGY_ROI
        reg = im[y:y + h, x:x + w]
        blobs, m = _glyphs(reg)
        widths = [b[2] for b in blobs]
        si = int(np.argmin(widths)) if len(blobs) > 1 else -1
        digits = blobs[:si] if si > 0 else blobs
        for (bx, by, bw, bh, ba) in digits:
            g = cv2.resize(m[by:by + bh, bx:bx + bw].astype(np.float32),
                           (14, 18), interpolation=cv2.INTER_AREA)
            out["_%d_%d" % (bx, by)] = np.clip(g, 0, 1)
    return out


if __name__ == "__main__":
    # calibration: 024605 must read 0, k1 must read 10
    for p in [r"D:\Projects\RocoAgent\logs\frames2\024605_0416.png",
              r"D:\Projects\RocoAgent\logs\k1.png"]:
        if not os.path.exists(p):
            print("missing", p)
            continue
        im = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]
        x, y, w, h = ENERGY_ROI
        reg = im[y:y + h, x:x + w]
        blobs, m = _glyphs(reg)
        print(os.path.basename(p), "blobs", blobs, "->", read_energy(im))
    print(_build_digits().keys())
