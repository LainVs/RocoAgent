"""energy.py - read the energy dots under the player's HP card.

The HUD does not show a number: it shows a row of small circular pips. A pip
with energy is a bright white disc, an empty pip is a dark red disc. The
"★10" to the right is the CAP, not the current value - reading it as the
current value is what the old implementation did, which is why it always
reported 10 (or None when the ROI missed).

Geometry (window-relative, measured 2026-11-18 on a 2586x1671 window):
    pip row   y 196..244, x 160..410
    6 pips, pitch ~52 px, radius ~21 px
    the cap badge starts around x=430

usage:
    python energy.py --now
    python energy.py --selftest
"""
from __future__ import annotations
import os
import paths

import sys

sys.path.insert(0, str(paths.ROOT / "src"))
sys.path.insert(0, str(paths.TOOLS))

PIP_BAND = (160, 196, 250, 48)      # x, y, w, h  (window-relative)
PIP_COUNT = 6


def _pips(band, offset=(0, 0)):
    """Locate the circular pips inside an already-cropped band.

    `band` must be the pip strip itself. The previous version cropped PIP_BAND
    out of whatever it was given, so passing an already-cropped strip produced
    an empty slice and every read returned None.
    """
    import cv2
    import numpy as np

    ox, oy = offset
    mx = band.max(axis=2).astype(np.int16)
    mn = band.min(axis=2).astype(np.int16)
    # The disc itself is near-white; the star inside is grey, the empty pip is
    # dark red. Detect the disc body (bright ring) rather than the star.
    disc = ((mn > 185) & ((mx - mn) < 45)).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(disc, 8)
    out = []
    for i in range(1, n):
        bx, by, bw, bh, area = stats[i]
        # A pip is a 25x25 disc (area ~325) at this resolution. Reject the thin
        # digits of the "★10" cap badge, which also pass the colour test.
        if not (280 <= area <= 420):
            continue
        if not (22 <= bw <= 30) or not (22 <= bh <= 30):
            continue
        out.append((bx + bw // 2 + ox, by + bh // 2 + oy, bw, bh, area))
    out.sort(key=lambda t: t[0])
    return out


def read_energy(rgb, band=None):
    """Number of lit pips (0..6), or None when the row cannot be read.

    Two pip appearances exist:
      * empty   - dark red disc, no white ring  -> the bright-disc detector
                  misses it entirely, so the pip count alone under-reports
      * lit     - near-white disc with a grey star

    So: count the pips that ARE bright, and treat the row as readable when at
    least 4 pips were located (the row always has 6, but a couple can be hidden
    behind the "★10" cap badge or clipped at the left edge).
    """
    import numpy as np

    pips = _pips(rgb)
    if len(pips) < 4:
        return None
    lit = 0
    for cx, cy, bw, bh, _area in pips[:PIP_COUNT]:
        px = rgb[cy - 3:cy + 3, cx - 3:cx + 3]
        if px.size == 0:
            continue
        mx = px.max(axis=2).astype(np.int16)
        if float(mx.mean()) > 150:
            lit += 1
    return lit

def read_energy_live(screen=None):
    import robot
    scr = screen or robot.Screen()
    if scr.rect is None:
        return None
    crop = scr.grab(PIP_BAND)
    if crop is None:
        return None
    return read_energy(crop)


def selftest():
    import cv2
    import numpy as np

    ok = True
    for path, want in (
        (str(paths.ROOT / "logs/estars.png"), 0),
    ):
        import os
        if not os.path.exists(path):
            print("skip", path)
            continue
        im = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        im = im[:, :, ::-1]
        got = read_energy(im, (0, 0, im.shape[1], im.shape[0]))
        print("%-24s -> %s (want %s) %s" % (
            os.path.basename(path), got, want,
            "OK" if got == want else "**FAIL**"))
        if got != want:
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print("energy =", read_energy_live())
