"""robot.py - RocoAgent PvP battle bot (single process: capture -> decide -> HID).

Why this exists
---------------
Every attempt last night failed for one reason: latency. Each frame grab or BLE
write cost 4-5 seconds because a fresh process had to be spawned. The game gives
only 15 s per turn, and an ignored press makes the turn time out into
`使出了★0聚能` (0 damage). So: one process, one BLE link, one mss handle,
poll at ~8 Hz, decide locally in <20 ms.

Safety
------
All in-game input goes through the ESP32 HID board. Nothing here calls
SendInput / pyautogui / windows-desktop clicks.

usage:
    python robot.py --log                    run the autopilot
    python robot.py --once                   print one observation and exit
    python robot.py --probe 20               print 20 observations, no input
    python robot.py --panic-guard            (default on) heal/switch when low
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"D:\Tools")

import cv2  # noqa: E402
import mss  # noqa: E402

import digitread as dr  # noqa: E402
import esp_shim as rocs  # noqa: E402  (BLE via the espfast daemon)
import asyncio  # noqa: E402
import portraits  # noqa: E402  (top-left HUD bust -> active pet)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

LOG_DIR = r"D:\Projects\RocoAgent\logs"
TURN_LOG = os.path.join(LOG_DIR, "turns.jsonl")
ROUND_LOG = os.path.join(LOG_DIR, "rounds.jsonl")

TITLE = "\u6d1b\u514b\u738b\u56fd"
MY_BAR = (166, 170, 244, 20)
FOE_BAR = (2251, 170, 245, 20)
# digitread's skill-bar search box, as (x0, y0, x1, y1)
SKILL_BOX = dr.BAR                # (250, 480, 780, 1520)

POLL_S = 0.12
ACT_COOLDOWN_S = 1.0
SWITCH_COOLDOWN_S = 2.5
SWITCH_RETRY_S = 2.0        # never re-enter _try_switch faster than this

# UI anchors (window-relative), measured pixel-exactly from live frames
LOBBY_BTN = (1980, 1480, 520, 150)     # 开始挑战  (gold blob centre ~2210,1567)
RESULT_BTN = (1150, 1470, 340, 130)    # 退出      (white blob centre ~1293,1532)
# UI anchors, WINDOW-RELATIVE. They used to be absolute physical pixels measured
# while the window happened to sit at (443,39); the moment the user dragged the
# window the clicks landed on whatever used to be at that spot on the desktop -
# "it is clicking a bit above the 开始挑战 button". Anything tied to the window
# must be stored relative to the window rect and offset at click time.
LOBBY_BTN_REL = (2210, 1567)             # 开始挑战 centre (window-relative)
RESULT_BTN_REL = (1293, 1530)            # 退出 centre
ADVANCE_REL = (1290, 1238)               # neutral spot that advances splash screens
ACTION_BTN_BAND = (1100, 1480, 400, 150)   # bottom-centre button slot
CONFIRM_BAND = (820, 1350, 1000, 220)      # the on-screen "Space" confirm plate
RESULT_BAND = (2050, 1250, 480, 260)       # "观战人数 / 奖励总数" block
RESULT_LIST_BAND = (120, 330, 420, 900)    # the six "我方战况" rows
STUCK_S = 60.0                         # no progress for this long => force an action
CLICK_COOLDOWN_S = 6.0
ADVANCE_COOLDOWN_S = 2.5

# Forced-switch panel: the five replacement rows down the left edge (physical
# pixels), measured off a live frame at 1:1.
SWITCH_ROW_ABS = [
    (783, 559), (783, 700), (783, 840), (783, 985), (783, 1120),
]

# Damage-capsule anchors for the forced-switch list, unused for now.


BOT_LOG = os.path.join(LOG_DIR, "bot_stdout.log")
LOCK_PATH = os.path.join(LOG_DIR, "robot.lock")
_LOCK_FH = None


def acquire_lock():
    """Single instance only: two bots would double every key press and fight
    over the BLE link.

    Uses an OS file lock (msvcrt) rather than a pid file: the lock is released
    automatically when the process dies, so a stale file can never wedge us.
    """
    global _LOCK_FH
    import atexit
    import msvcrt
    try:
        fh = open(LOCK_PATH, "a+")
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return False
    except Exception:
        return True                     # never block on an unexpected FS error
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    _LOCK_FH = fh
    atexit.register(lambda: fh.close())
    return True


def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(BOT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ------------------------------------------------------------------ window
_UD = ctypes.windll.user32


def find_game():
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, lp):
        n = ctypes.create_unicode_buffer(256)
        _UD.GetWindowTextW(hwnd, n, 256)
        if TITLE in n.value and _UD.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            _UD.GetClassNameW(hwnd, cls, 256)
            if "Unreal" in cls.value:
                r = wt.RECT()
                _UD.GetWindowRect(hwnd, ctypes.byref(r))
                found.append((hwnd, r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    _UD.EnumWindows(cb, 0)
    return found[0] if found else None


def _raise_topmost(hwnd):
    """Raise the game above whatever covers it, then drop the always-on-top flag.

    Returns the foreground state. Kept separate because both focus() and
    _click_to_focus() need exactly this step before anything else can work.
    """
    try:
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP = 0x0002 | 0x0001 | 0x0040      # NOMOVE | NOSIZE | SHOWWINDOW
        _UD.ShowWindow(hwnd, 9)             # SW_RESTORE
        _UD.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP)
        _UD.SetForegroundWindow(hwnd)
        _UD.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP)
    except Exception:
        pass
    return _UD.GetForegroundWindow() == hwnd


def focus(hwnd):
    """Bring the game to the foreground.

    Plain SetForegroundWindow is refused when the caller is not already the
    foreground process, which is how the bot ended up firing keys into the
    editor window instead of the game. Attaching to the foreground thread's
    input queue lifts that restriction.
    """
    if _UD.GetForegroundWindow() == hwnd:
        return True
    _UD.ShowWindow(hwnd, 9)          # SW_RESTORE
    fg = _UD.GetForegroundWindow()
    try:
        cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        fg_tid = _UD.GetWindowThreadProcessId(fg, None)
        tgt_tid = _UD.GetWindowThreadProcessId(hwnd, None)
        attached = []
        for tid in {fg_tid, tgt_tid}:
            if tid and tid != cur_tid:
                if _UD.AttachThreadInput(cur_tid, tid, True):
                    attached.append(tid)
        _UD.BringWindowToTop(hwnd)
        _UD.SetForegroundWindow(hwnd)
        _UD.SetFocus(hwnd)
        for tid in attached:
            _UD.AttachThreadInput(cur_tid, tid, False)
    except Exception:
        pass
    if _UD.GetForegroundWindow() != hwnd:
        # Last resort: raise the game above whatever window is covering it (the
        # editor was fully covering the game, so even hardware clicks landed on
        # the editor and focus never moved).
        _raise_topmost(hwnd)
    time.sleep(0.08)
    return _UD.GetForegroundWindow() == hwnd


# ------------------------------------------------------------------ capture
class Screen:
    """Region grabber. Full-window grabs cost ~100 ms; region grabs ~10 ms.

    We only ever need three small regions, so the poll loop stays at ~10 Hz.
    """

    def __init__(self):
        self.sct = mss.mss()
        self.rect = None
        self.hwnd = None
        self.last_refresh = 0.0
        self.refresh()

    def refresh(self):
        g = find_game()
        if not g:
            self.rect = None
            self.hwnd = None
            return
        self.hwnd, x, y, w, h = g
        # A minimised game reports a tiny placeholder rect, so every window-
        # relative coordinate silently becomes garbage (this caused a 10-minute
        # stuck loop where the bot clicked random spots on the desktop).
        if _UD.IsIconic(self.hwnd):
            _UD.ShowWindow(self.hwnd, 9)        # SW_RESTORE
            time.sleep(0.4)
            g = find_game()
            if g:
                self.hwnd, x, y, w, h = g
        self.rect = (x, y, w, h)
        # Client-area origin relative to the window rect. Screenshots (and the
        # HUD boxes measured on them) are client-relative, so portrait work
        # goes through grab_client(); everything else stays window-relative.
        r = wt.RECT()
        _UD.GetClientRect(self.hwnd, ctypes.byref(r))
        pt = wt.POINT(0, 0)
        _UD.ClientToScreen(self.hwnd, ctypes.byref(pt))
        self.client_off = (pt.x - x, pt.y - y)
        self.client_size = (r.right, r.bottom)
        self.last_refresh = time.time()
    def grab(self, box):
        """box = (x, y, w, h) window-relative -> RGB ndarray."""
        if self.rect is None or time.time() - self.last_refresh > 2.0:
            self.refresh()
            if self.rect is None:
                return None
        gx, gy = self.rect[0], self.rect[1]
        x, y, w, h = box
        shot = self.sct.grab({"left": gx + x, "top": gy + y, "width": w, "height": h})
        return cv2.cvtColor(np.asarray(shot), cv2.COLOR_BGRA2RGB)

    def grab_full(self):
        if self.rect is None:
            self.refresh()
            if self.rect is None:
                return None
        return self.grab((0, 0, self.rect[2], self.rect[3]))

    def grab_client(self, box):
        """box = (x, y, w, h) CLIENT-relative -> RGB ndarray.

        Used for the HUD portrait, whose coordinates were measured on a
        screenshot (screenshots are client-area only, no frame).
        """
        if self.rect is None or time.time() - self.last_refresh > 2.0:
            self.refresh()
            if self.rect is None:
                return None
        ox, oy = getattr(self, "client_off", (0, 0))
        x, y, w, h = box
        return self.grab((ox + x, oy + y, w, h))


# ------------------------------------------------------------------ perception
def _card_lit(crop, pill):
    """Is this skill card lit (affordable) or dimmed (not enough energy)?

    Measured on live frames: affordable cards have a max-channel mean of
    148-159, unaffordable ones 56-88. 105 sits in the gap.
    """
    x0, y0, x1, y1 = pill["box"]
    ax0, ax1 = max(0, x0 - 45), x1
    ay0, ay1 = max(0, y0 - 70), max(0, y0 - 10)
    art = crop[ay0:ay1, ax0:ax1]
    if art.size == 0:
        return True
    return float(art.max(axis=2).mean()) > 105.0


CNT_ROI = (1120, 55, 340, 150)     # the big white countdown numeral


def countdown_visible(rgb):
    """Is the 15 s countdown numeral on screen?

    Measured: inside a battle this is 0 while the turn is animating and
    900-4300 while it is my decision window. Independent of card art, unlike
    the capsule detector.
    """
    x, y, w, h = CNT_ROI
    if y + h > rgb.shape[0] or x + w > rgb.shape[1]:
        return False
    return int((rgb[y:y + h, x:x + w].min(axis=2) > 200).sum()) > 250


def countdown_visible_crop(crop):
    """Same test when the crop already IS the numeral ROI."""
    if crop is None or crop.size == 0:
        return False
    return int((crop.min(axis=2) > 200).sum()) > 250


class Perc:
    """Reads state from three small screen regions. ~25 ms per observation."""

    def __init__(self, screen):
        self.screen = screen
        self.tpl = dr.load_templates()

    def _bar(self, box):
        x, y, w, h = box
        crop = self.screen.grab((x, y - 2, w, h + 4))
        if crop is None:
            return {"pct": 0.0, "color": "none", "ui": False}
        return _read_bar(crop, (0, 2, w, h))

    def observe(self):
        my = self._bar(MY_BAR)
        foe = self._bar(FOE_BAR)
        battle = my["ui"] and foe["ui"]

        x0, y0, x1, y1 = SKILL_BOX
        cw, ch = x1 - x0, y1 - y0
        crop = self.screen.grab((x0, y0, cw, ch))
        skills = [None] * 4
        visible, npills = False, 0
        if crop is not None:
            reg = (0, 0, cw, ch)
            # find_pills is the expensive step (~107 ms). Run it once and share
            # the result: it used to run three times per observation because
            # _is_skillbar and _slots each called it.
            try:
                pills = dr.find_pills(crop, reg)
            except Exception:
                pills = []
            slots = _slots(crop, reg, pills)
            visible, npills = _is_skillbar(crop, reg, pills, slots)
            if visible:
                for i, pill in enumerate(slots):
                    r = dr.read_skill(crop, pill, self.tpl)
                    r["index"] = i + 1
                    r["lit"] = _card_lit(crop, pill) if pill else False
                    skills[i] = r

        cnt = False
        if battle:
            # Grab the numeral's own ROI instead of the whole 2586x1671 window:
            # the full grab cost ~100 ms to read a 340x150 region.
            cnt_crop = self.screen.grab(CNT_ROI)
            cnt = countdown_visible_crop(cnt_crop) if cnt_crop is not None else False

        # Energy: a row of pips under the HP card. Only meaningful in battle,
        # and only reported when the row was actually located.
        en = None
        if battle:
            try:
                import energy as _energy
                pip = self.screen.grab(_energy.PIP_BAND)
                if pip is not None:
                    en = _energy.read_energy(pip)
            except Exception:
                en = None

        return {
            "t": time.time(),
            "battle": battle,
            "my_ui": my["ui"], "foe_ui": foe["ui"],
            "my_pct": my["pct"], "my_color": my["color"],
            "foe_pct": foe["pct"], "foe_color": foe["color"],
            "bar": visible, "npills": npills,
            "countdown": cnt,
            "energy": en,
            "myturn": bool(battle and (cnt or visible)),
            "skills": skills,
        }


def _read_bar(rgb, rect):
    """Same physics as hpwatch.read_bar, inlined so we don't import its CLI.

    `pct` is None when the bar cannot be read, and only 0.0 when the bar is
    genuinely empty. Returning 0.0 for both made "I cannot see the HUD" and "my
    pet has fainted" indistinguishable: during the VS splash the HP bars are not
    drawn yet, so the bot read 0.0, decided it had fainted, and then sat in the
    switch branch for the whole match - 45 s and 0 actions on 13:11:18.
    """
    x, y, w, h = rect
    if x + w > rgb.shape[1] or y + h + 1 >= rgb.shape[0]:
        return {"pct": None, "color": "none", "ui": False}
    sub = rgb[y:y + h, x:x + w]
    r = sub[:, :, 0].astype(np.int16)
    g = sub[:, :, 1].astype(np.int16)
    b = sub[:, :, 2].astype(np.int16)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    # Fill test. Two failure modes were measured on live frames:
    #   * green/orange bars: fill max-channel ~170-200, empty slot ~110
    #   * red critical bars: fill max-channel ~155, empty slot ~142
    # The old gate (max > 145) rejected the red fill entirely, so a pet at 2%
    # read as "no fill" = fainted. A gate of 150 sits between the red fill and
    # the red empty slot; combined with the saturation test it separates both.
    fm = ((mx - mn) > 55) & (mx > 150)
    cols = np.nonzero(fm.sum(axis=0) >= 2)[0]
    edge = np.concatenate([rgb[y - 2:y, x:x + w], rgb[y + h:y + h + 2, x:x + w]])
    er = edge[:, :, 0].astype(np.int16)
    eg = edge[:, :, 1].astype(np.int16)
    eb = edge[:, :, 2].astype(np.int16)
    emx = np.maximum(np.maximum(er, eg), eb)
    emn = np.minimum(np.minimum(er, eg), eb)
    ui = bool((((emx - emn) < 20) & (emx > 70) & (emx < 125)).mean() > 0.55)
    if not len(cols) or cols[0] > 6:
        # No fill at all. Only call it an empty bar when the frame really is a
        # battle HUD; otherwise the reading is unknown.
        return {"pct": 0.0 if ui else None, "color": "none", "ui": ui}
    pct = 100.0 * (cols[-1] + 1) / w
    px = sub[fm]
    if len(px) == 0:
        return {"pct": 0.0 if ui else None, "color": "none", "ui": ui}
    mr, mg = px[:, 0].mean(), px[:, 1].mean()
    color = "green" if mg >= mr else ("orange" if mg > 0.6 * mr else "red")
    return {"pct": round(min(pct, 100.0), 1), "color": color, "ui": ui}


def _is_skillbar(crop, region, pills=None, slots=None):
    """Is the skill card column actually drawn? Returns (visible, npills).

    Scenery contains plenty of dark blobs that survive the capsule colour
    filter, so require a card-like lattice: at least 3 slots filled and the
    fitted vertical pitch in the 150-215 px range.

    `pills` and `slots` may be passed in when the caller already computed them:
    find_pills costs ~107 ms on a real frame, and it used to run three times per
    observation through this call chain.
    """
    if pills is None:
        try:
            pills = dr.find_pills(crop, region)
        except Exception:
            return False, 0
    if slots is None:
        slots = _slots(crop, region, pills)
    filled = [s for s in slots if s]
    if len(filled) < 2:
        return False, len(pills)
    # Capsules in one column share an x range; a slot that drifted sideways is a
    # bad _fill_missing guess (observed: slot 2 placed at x=148 while the real
    # column is x=228..321, which broke the pitch test and made the whole bar
    # read as "not drawn" for four minutes).
    xs = sorted(s["box"][0] for s in filled)
    if xs[-1] - xs[0] > 220:
        return False, len(pills)
    ys = sorted((s["box"][1] + s["box"][3]) / 2.0 for s in filled)
    gaps = [b - a for a, b in zip(ys, ys[1:])]
    # gaps are multiples of the card pitch (one card may be missed entirely).
    # A gap below 120 means two slots collapsed onto the same card.
    ok = all(120 <= g <= 420 for g in gaps)
    return bool(ok), len(pills)


def _is_dash(crop, pill):
    """Is this capsule showing "--" (no damage number) rather than digits?

    digitread classifies a capsule as "num" as soon as any glyph passes a colour
    gate, and the "--" dashes are white, so a status card comes back as a number
    slot. That mislabels the slot, shifts the whole lattice and made the skill
    bar read as "not drawn" for four minutes.

    Shape is decisive: "--" is two short horizontal bars, each much wider than
    it is tall, sitting on one row. Digits are roughly square and have more than
    two components.
    """
    x0, y0, x1, y1 = pill["box"]
    reg = crop[y0:y1, x0:x1]
    if reg.size == 0:
        return False
    mx = reg.max(axis=2).astype(np.int16)
    mn = reg.min(axis=2).astype(np.int16)
    white = (((mx - mn) < 45) & (mn > 150)).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(white, 8)
    bars = 0
    for i in range(1, n):
        bx, by, bw, bh, area = stats[i]
        if area < 8:
            continue
        # a dash: wide, flat, and only a few pixels tall
        if bw >= 5 and bh <= 6 and bw >= 2 * bh:
            bars += 1
    return bars >= 2


def _is_badge(crop, pill):
    """Cost badge ('\u2605N') vs damage capsule.

    Both are the same dark capsule. The badge additionally holds a star, which
    is a solid red or yellow blob; damage digits are thin strokes. The star turns
    RED when the skill is unaffordable, which is exactly when the stock
    find_pills leaks badges into the damage list.
    """
    x0, y0, x1, y1 = pill["box"]
    reg = crop[y0:y1, x0:x1]
    r, g, b = dr.channels(reg)
    bright = int(((r > 170) & (g > 160) & (b > 140)).sum())
    yel = int(dr.gate_yellow(reg).sum())
    grn = int(dr.gate_green(reg).sum())
    red = int(dr.gate_red(reg).sum())
    return (red + yel >= 150) and grn < 20 and bright < 90


def _damage_pills(crop, region):
    """Return the four damage capsules in slot order (1..4)."""
    pills = dr.find_pills(crop, region)
    pills.sort(key=lambda p: p["box"][1])
    if len(pills) == 4:
        return pills
    if len(pills) == 8:
        # badge, damage, badge, damage, ... -> take every second
        return pills[1::2]
    return [p for p in pills if not _is_badge(crop, p)]


def _fill_missing(crop, slots):
    """Second pass for card slots whose capsule the strict finder missed.

    Observed failure: the one card carrying a damage number ("546") had its
    capsule tinted by the card art, find_pills dropped it, we read three dashes
    and concluded 'unreadable' - every turn. Since the lattice tells us roughly
    where that capsule must be, search a narrow band around the expected row with
    relaxed darkness thresholds, and keep the rightmost capsule (the badge is
    always to its left).
    """
    ys = [((s["box"][1] + s["box"][3]) / 2.0) if s else None for s in slots]
    known = [y for y in ys if y is not None]
    if len(known) < 2:
        return slots
    gaps = sorted(b - a for a, b in zip(sorted(known), sorted(known)[1:]))
    pitch = gaps[0] if gaps else 180.0
    if not (120 <= pitch <= 240):
        pitch = 180.0
    h, w = crop.shape[:2]
    n_slots = len(slots)
    for i, s in enumerate(slots):
        if s is not None:
            continue
        # expected centre from the nearest filled neighbour.
        # Bound the index by len(ys), not a hard-coded 4: _fill_missing is called
        # with whatever slot list _slots built, and a shorter list made ys[j]
        # raise IndexError, which crashed the whole bot (rc=1, no traceback).
        ref = next(((j, ys[j]) for j in (i - 1, i + 1, i - 2, i + 2)
                    if 0 <= j < n_slots and 0 <= j < len(ys)
                    and ys[j] is not None), None)
        if ref is None:
            continue
        j, yj = ref
        want = yj + (i - j) * pitch
        if not (0 <= want < h):
            continue
        y0 = max(0, int(want - 60))
        y1 = min(h, int(want + 60))
        # Search band for the missing capsule.
        #
        # History: the original used a fixed 140..430 and sometimes latched onto
        # a neighbour card's digits. Commit 1d51d0b narrowed it to "the x range
        # of the capsules already found, -30/+110". That was wrong in the other
        # direction: when find_pills returns only one or two capsules the band
        # collapses around them and the missing slots can never be recovered,
        # which is exactly the "find_pills=2, slots all wrong" state seen in the
        # 17:21 match.
        #
        # Compromise: keep the full band, but require the candidate to sit within
        # a plausible distance of the column the known capsules form. The column
        # is derived from the median x, so one stray capsule cannot shrink it.
        xs_known = sorted(int(s["box"][0]) for s in slots if s)
        if len(xs_known) >= 2:
            col = xs_known[len(xs_known) // 2]
            # Wide enough to hold a card, narrow enough to exclude the next
            # column. Cards are ~180 px apart and the capsule sits near the
            # card's left edge, so -70/+120 around the median column covers the
            # digits without reaching the neighbour.
            bx0 = max(0, col - 70)
            bx1 = min(w, col + 120)
        else:
            bx0, bx1 = 140, min(w, 430)
        band = crop[y0:y1, bx0:bx1]
        if band.size == 0:
            continue
        # Score every capsule-sized window with a box filter instead of a Python
        # triple loop. The original slid a window in 3 px steps and took a numpy
        # mean each time: 5 sizes x ~40 x ~97 positions per missing slot, which
        # measured at ~500 ms per observation on a real frame. cv2.blur computes
        # the same means for all positions in one pass.
        # The capsule fill is NOT a reliable anchor: on some cards the art tints
        # it to (160,158,147) instead of the nominal (61,61,61), so a "uniform
        # dark grey" window match latches onto the shadow beside the card and
        # frames empty scenery (measured on v1.png slot 2, which then read "--"
        # while the card clearly showed 546).
        #
        # The digits themselves are the reliable feature: saturated green or red
        # strokes, 9-32 px tall. Find those and take their bounding box.
        r_, g_, b_ = dr.channels(band)
        ink = (dr.gate_green(band) | dr.gate_red(band)).astype(np.uint8)
        if ink.sum() >= 20:
            n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
            blobs = []
            for k in range(1, n):
                bx, by, bw2, bh2, area = stats[k]
                if area < 12 or not (8 <= bh2 <= 30) or not (2 <= bw2 <= 26):
                    continue
                blobs.append((bx, by, bw2, bh2))
            if blobs:
                # Keep the tightest cluster of digit-sized blobs: pick the row
                # with the most vertical overlap, so stray marks on neighbouring
                # cards do not widen the box (v1.png slot 2 read "????4?546??"
                # when they were included).
                blobs.sort(key=lambda t: t[0])
                best_row = None
                for bx, by, bw2, bh2 in blobs:
                    row = [t for t in blobs
                           if abs(t[1] - by) <= 10]
                    if best_row is None or len(row) > len(best_row):
                        best_row = row
                row = best_row or blobs
                xs = [t[0] for t in row]
                ys = [t[1] for t in row]
                x2s = [t[0] + t[2] for t in row]
                y2s = [t[1] + t[3] for t in row]
                pad = 3
                px0 = max(0, min(xs) - pad)
                px1 = min(band.shape[1], max(x2s) + pad)
                py0 = max(0, min(ys) - pad)
                py1 = min(band.shape[0], max(y2s) + pad)
                slots[i] = {"box": (px0 + bx0, py0 + y0, px1 + bx0, py1 + y0),
                            "kind": "num", "color": None}
                continue

        # Fallback: the original uniform-grey window search, kept for cards whose
        # digits are white (no saturated ink to find).
        near = (np.abs(band.astype(np.int16) - 61).max(axis=2) <= 22).astype(np.float32)
        # Restrict the search to the x-range where this pet's capsules actually
        # live, taken from the slots that were found properly. Hard-coding a band
        # (the first attempt used 80..200 of the band) pushed slot 2's window to
        # the right and it framed empty scenery instead of the "546" capsule:
        # cards are staggered, so capsule x differs by up to 100 px between rows.
        xs = [int(s["box"][0]) for s in slots if s]
        if xs:
            lo = max(0, min(xs) - 140 - 20)
            hi = max(xs) - 140 + 130
        else:
            lo, hi = 0, band.shape[1]
        lo = max(0, min(lo, band.shape[1] - 1))
        hi = max(lo + 1, min(hi, band.shape[1]))
        best = None
        for bw, bh in ((68, 32), (64, 30), (72, 36), (60, 28), (66, 34)):
            if band.shape[0] <= bh or band.shape[1] <= bw:
                continue
            m = cv2.blur(near, (bw, bh))
            m = m[:band.shape[0] - bh + 1, :band.shape[1] - bw + 1]
            if m.size == 0:
                continue
            sub = m[:, lo:min(hi, m.shape[1])]
            if sub.size == 0:
                continue
            yy, xx = np.unravel_index(int(np.argmax(sub)), sub.shape)
            s = float(sub[yy, xx])
            xx += lo
            if best is None or s > best[0]:
                best = (s, int(xx), int(yy), bw, bh)
        if best is None or best[0] < 0.42:
            continue
        _, xx, yy, bw, bh = best
        slots[i] = {"box": (xx + bx0, yy + y0, xx + bx0 + bw, yy + y0 + bh),
                    "kind": "num", "color": None}
    return slots


def _slots(crop, region, pills=None):
    """Fit the four damage capsules onto a vertical lattice.

    Cards are ~180 px apart. For each card the damage capsule sits to the right
    of its cost badge, so within a slot we keep the pill with the largest x0.
    Searching the lattice beats classifying badges one by one: the badge star's
    colour changes (yellow -> red/green) when energy is low, which is exactly
    when naive badge detection breaks.
    """
    if pills is None:
        try:
            pills = dr.find_pills(crop, region)
        except Exception:
            pills = []
    slots = [None, None, None, None]
    if not pills:
        return slots
    cy = {id(p): (p["box"][1] + p["box"][3]) / 2.0 for p in pills}
    best = None
    for anchor in pills:
        y0 = cy[id(anchor)]
        for pitch in range(150, 216, 5):
            cand = [None, None, None, None]
            for p in pills:
                k = int(round((cy[id(p)] - y0) / pitch))
                if not (0 <= k <= 3):
                    continue
                if cand[k] is None or p["box"][0] > cand[k]["box"][0]:
                    cand[k] = p
            filled = sum(1 for c in cand if c is not None)
            # prefer more filled slots, then a tight lattice
            score = (filled, -abs(pitch - 180))
            if best is None or score > best[0]:
                best = (score, cand)
    if best is None:
        return slots
    slots = _fill_missing(crop, best[1])
    # Relabel capsules that are actually showing "--": digitread calls anything
    # with a colour-gated glyph a number, and the white dashes qualify.
    for s in slots:
        if s and s.get("kind") == "num" and _is_dash(crop, s):
            s["kind"] = "dash"
            s["color"] = None
    return slots


# ------------------------------------------------------------------ UI probes
def has_start_button(screen):
    """Gold '开始挑战' blob in the bottom-right banner."""
    x, y, w, h = LOBBY_BTN
    crop = screen.grab((x, y, w, h))
    if crop is None:
        return False
    r = crop[:, :, 0].astype(np.int16)
    g = crop[:, :, 1].astype(np.int16)
    b = crop[:, :, 2].astype(np.int16)
    gold = (r > 205) & (g > 150) & (g < 215) & (b < 120) & (r - b > 110)
    return int(gold.sum()) > 8000


def has_exit_button(screen):
    """White '退出' blob on the result screen."""
    x, y, w, h = RESULT_BTN
    crop = screen.grab((x, y, w, h))
    if crop is None:
        return False
    mx = crop.max(axis=2).astype(np.int16)
    mn = crop.min(axis=2).astype(np.int16)
    white = ((mx - mn) < 20) & (mx > 215)
    return int(white.sum()) > 4000


def has_vs_screen(screen):
    """The pre-match VS splash: hard left teal / right orange split.

    Nothing else in the game is close to these two colours, so it is a safe
    'hands off' signal - we must never click while the match is loading.
    """
    left = screen.grab((150, 250, 300, 300))
    right = screen.grab((1900, 250, 300, 300))
    if left is None or right is None:
        return False
    ml = left.reshape(-1, 3).mean(axis=0)
    mr = right.reshape(-1, 3).mean(axis=0)
    return (abs(ml[0] - 35) < 45 and abs(ml[1] - 135) < 45 and abs(ml[2] - 171) < 45
            and abs(mr[0] - 196) < 45 and abs(mr[1] - 96) < 45 and abs(mr[2] - 22) < 45)


def find_action_button(screen):
    """The big bottom-centre button, whatever it currently says.

    This one slot hosts different buttons depending on the screen:
        确认首发  (pre-match, warm yellow  mean ~211,165,83)
        退出      (result,      near white   mean ~218,213,202)
    Treating them as one "action button" means we never again sit on a screen
    waiting for a button whose label we did not anticipate.

    Returns physical (x, y) or None.
    """
    x, y, w, h = ACTION_BTN_BAND
    crop = screen.grab((x, y, w, h))
    if crop is None:
        return None
    r = crop[:, :, 0].astype(np.int16)
    g = crop[:, :, 1].astype(np.int16)
    b = crop[:, :, 2].astype(np.int16)
    warm = ((r > 215) & (g > 170) & (b > 70) & (b < 190))
    pale = np.minimum(np.minimum(r, g), b) > 200
    m = (warm | pale).astype(np.uint8)
    if int(m.sum()) < 6000:
        return None
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    best = None
    for i in range(1, n):
        bx, by, bw, bh, area = stats[i]
        # A real button is ~285x80 and sits centred in this band. The loading
        # screen's pale grey floor is far wider - it used to win here and steal
        # the click from the lobby's 开始挑战 button.
        if not (170 <= bw <= 380) or not (45 <= bh <= 130) or area < 9000:
            continue
        cxr = bx + bw // 2
        if abs(cxr - 193) > 95:          # band is 400 wide, buttons sit ~193 in
            continue
        if best is None or area > best[0]:
            best = (area, bx, by, bw, bh)
    if best is None:
        return None
    _, bx, by, bw, bh = best
    cx = x + bx + bw // 2
    cy = y + by + bh // 2
    if screen.rect is None:
        return None
    return (screen.rect[0] + cx, screen.rect[1] + cy)


def find_confirm_button(screen):
    """Locate the on-screen "Space" confirm plate shown while the switch panel
    is open.

    Clicking it is more reliable than sending a Space key: the board maps
    "space" to Keyboard.press(' ') (esp_alt/src/main.cpp:248) and HANDOFF.md
    records that "number key + Space" never actually switched a pet. The plate
    is a light rounded rectangle on the saturated arena floor.

    Returns physical (x, y) or None.
    """
    x, y, w, h = CONFIRM_BAND
    crop = screen.grab((x, y, w, h))
    if crop is None or crop.size == 0:
        return None
    mx = crop.max(axis=2).astype(np.int16)
    mn = crop.min(axis=2).astype(np.int16)
    light = ((mn > 190) & ((mx - mn) < 35)).astype(np.uint8)
    if int(light.sum()) < 800:
        return None
    n, lab, stats, _ = cv2.connectedComponentsWithStats(light, 8)
    best = None
    for i in range(1, n):
        bx, by, bw, bh, area = stats[i]
        if area < 800 or not (50 <= bw <= 300) or not (24 <= bh <= 120):
            continue
        if best is None or area > best[0]:
            best = (area, bx, by, bw, bh)
    if best is None:
        return None
    _, bx, by, bw, bh = best
    cx, cy = x + bx + bw // 2, y + by + bh // 2
    if screen.rect is None:
        return None
    return (screen.rect[0] + cx, screen.rect[1] + cy)


def is_result_screen(screen):
    """Is the post-match summary on screen?

    After a match ends the game shows "我方战况" on the left with six rows of
    pet names and damage numbers, plus a huge ATTACK banner. The HP bars are
    still drawn, so battle detection stays true and the bot keeps polling a
    screen where no skill bar will ever appear - that is the "215 s, 3 actions"
    match.

    Marker: the result panel stacks SIX evenly spaced text rows down the left
    side. A normal battle has at most four skill cards in that area, and they
    are wider plates, not thin text rows. Counting six evenly spaced bright
    rows is specific enough that scenery cannot produce it (an earlier version
    tested only "dark with some bright pixels" and ended healthy matches after
    2 seconds).
    """
    x, y, w, h = RESULT_LIST_BAND
    crop = screen.grab((x, y, w, h))
    if crop is None or crop.size == 0:
        return False
    mx = crop.max(axis=2).astype(np.int16)
    mn = crop.min(axis=2).astype(np.int16)
    # bright text on a dark panel
    bright = ((mn > 175) & ((mx - mn) < 70))
    rows = bright.sum(axis=1)
    if rows.max() < 6:
        return False
    band = rows > 2
    runs, run = [], 0
    for v in band:
        if v:
            run += 1
        elif run:
            runs.append(run)
            run = 0
    if run:
        runs.append(run)
    text_rows = [r for r in runs if 5 <= r <= 45]
    if len(text_rows) < 5:                  # the panel stacks six rows
        return False
    # and they must be evenly spaced down the band
    return float((mx < 100).mean()) > 0.35


def find_switch_rows(screen, bar_visible=None):
    """Detect the forced-switch party list (5 staggered cards down the left).

    Each row is a light rounded plate (~155x70) with the pet icon on top. The
    death animation has none of these, so this cleanly separates "pet just died"
    from "the game is actually waiting for me to pick a replacement".

    `bar_visible` lets the caller pass in an already-computed skill-bar result;
    recomputing it costs ~50 ms and _battle_tick already knows the answer.

    Returns a list of window-relative (x, y) click targets, top row first.
    """
    X0, Y0, W, H = 300, 400, 620, 950
    crop = screen.grab((X0, Y0, W, H))
    if crop is None:
        return []
    # The skill cards' white name plates live in the same area and match the
    # same "light rounded rect" test. If the skill bar is on screen this is a
    # normal battle, not the switch panel.
    if bar_visible is None:
        x0, y0, x1, y1 = SKILL_BOX
        bar = screen.grab((x0, y0, x1 - x0, y1 - y0))
        if bar is not None:
            bar_visible, _ = _is_skillbar(bar, (0, 0, x1 - x0, y1 - y0))
        else:
            bar_visible = False
    if bar_visible:
        return []
    mx = crop.max(axis=2).astype(np.int16)
    mn = crop.min(axis=2).astype(np.int16)
    light = (((mx - mn) < 30) & (mx > 200)).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(light, 8)
    plates = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 2200 or not (130 <= w <= 200) or not (40 <= h <= 110):
            continue
        plates.append((y + h // 2, x + w // 2))
    if len(plates) < 3:
        return []
    plates.sort()
    gaps = sorted(b - a for (a, _), (b, _) in zip(plates, plates[1:]) if b - a > 80)
    if not gaps:
        return []
    pitch = gaps[len(gaps) // 2]
    if not (110 <= pitch <= 200):
        return []
    # keep 5 rows; the top card is the one usually clipped, so extend upward
    rows = list(plates)
    while len(rows) < 5:
        y0, x0 = rows[0]
        ny, nx = y0 - pitch, max(60, x0 - 45)
        if Y0 + ny < 450:
            break
        rows.insert(0, (ny, nx))
    rows = rows[:5]
    return [(int(X0 + x0), int(Y0 + y0)) for y0, x0 in rows]


# ------------------------------------------------------------------ decision
HEAL_HINTS = ("休息回复", "回复", "治疗")

# We cannot OCR the Chinese skill names quickly, but each pet's non-damaging
# ("dash") slot pattern is distinctive. Team sheet:
#   奶龙     1 泥浆铠甲(-) 2 过充电(num) 3 电磁偏转(-) 4 休息回复(-)  -> heal slot 4
# So a card set whose dash slots are (1,3,4) is 奶龙 and slot 4 is the heal.
HEAL_BY_DASH = {(1, 3, 4): 4}


def _heal_slot(st):
    dash = tuple((i + 1) for i, s in enumerate(st["skills"])
                 if s and s["kind"] == "dash")
    return HEAL_BY_DASH.get(dash)


HEAL_HP = 40          # only heal below this
HEAL_MAX_STREAK = 3   # never heal more than this many turns in a row
RETREAT_HP = 25       # below this, and behind on HP, consider switching out
SWITCH_DMG = 40        # below this, a fresh pet is usually better


def _est_value(s):
    """Best guess at the number on a card, even when one glyph failed to match.

    digitread reports "1?5" for a three-digit number whose middle glyph did not
    clear its correlation threshold. `value` is None in that case and the old
    code dropped the skill entirely, so a real "1?5" lost to a neighbouring
    "55" and the bot spent three turns on the weaker skill while the opponent
    walked through it.

    The digit count is still reliable, so use it as a floor: a three-glyph read
    is at least 100, a two-glyph read at least 10.
    """
    if s.get("value") is not None:
        return float(s["value"])
    if s.get("kind") == "num" and int(s.get("glyphs") or 0) >= 2:
        return float(10 ** (int(s["glyphs"]) - 1))
    return None


def _charge_or_switch(ctx):
    """Out of energy: gather it with F - unless that keeps getting interrupted.

    聚能 is not a free action. The opponent can spend their turn interrupting it,
    and then we take damage and gain nothing; the live log shows seven F presses
    in a row while the foe sat on 67% HP. A pet that is switched in enters with a
    full energy bar, so after two fruitless charges rotating is strictly better
    than pressing F a third time.
    """
    if (ctx.get("charge_streak", 0) >= 2
            and ctx.get("switch_slot")
            and ctx.get("switch_streak", 99) >= 3):
        return ("switch", ctx["switch_slot"]), \
            "no energy, charges keep getting interrupted -> rotate"
    return ("energy",), "nothing affordable, gather energy"


def decide(st, ctx):
    """Pick an action. Returns ((action, why) or (None, reason)).

    action: ("skill", n) | ("energy",)

    Order:
      1. Heal - but only on a real emergency (see below).
      2. Biggest *affordable* (lit) damaging skill that is not resisted.
      3. Nothing affordable -> gather energy with F.
      4. Nothing readable at all -> (None, ...) so the caller can reuse its
         last working plan instead of wasting the turn.

    The heal gate is deliberately strict. Earlier it was "hp <= 35%", but the
    actual damage loop came from elsewhere: a heal got cached as the repeatable
    plan and then replayed every unreadable turn, producing eight consecutive
    heals at 54-99% HP with the foe on 100% - two minutes of no output.
    So: heal only below 40%, only if we were just damaged, and never more than
    HEAL_MAX_STREAK turns running.
    """
    skills = [s for s in st["skills"] if s]
    if not skills:
        return None, "no skill read"
    usable = [s for s in skills if s.get("lit", True)]

    slot = _heal_slot(st)
    my = st["my_pct"]
    can_heal = (
        ctx.get("panic_guard", True)
        and slot
        and 1 <= slot <= len(st["skills"])
        and st["skills"][slot - 1]
        and st["skills"][slot - 1].get("lit", True)
        and my is not None
        and my < HEAL_HP
        and ctx.get("took_damage", True)
        and ctx.get("heal_streak", 0) < HEAL_MAX_STREAK
    )
    if can_heal:
        return ("skill", slot), "heal at %.0f%% (hurt last turn, streak %d)" % (
            my, ctx.get("heal_streak", 0))

    # About to lose this pet for nothing: hand the turn to a fresh one rather
    # than feeding the current one to the opponent. This is separate from the
    # "all my numbers are weak" rule below - that one looks at offence, this one
    # looks at the scoreboard.
    bench = ctx.get("switch_slot")
    foe_pct = st["foe_pct"]
    if (bench and ctx.get("switch_streak", 99) >= 3
            and my is not None and foe_pct is not None
            and my < RETREAT_HP and foe_pct > my + 25):
        return ("switch", bench), "retreat at %.0f%% vs foe %.0f%%" % (my, foe_pct)

    damaged = []
    for s in usable:
        if s["kind"] != "num":
            continue
        v = _est_value(s)
        if v is not None:
            damaged.append((s, v))
    if not damaged:
        if usable:
            # Cards are lit but no damage number could be read: that is a failed
            # read, not an empty hand. Tell the caller so it can reuse its last
            # working plan instead of burning the turn on 聚能.
            return None, "lit cards but unreadable"
        return _charge_or_switch(ctx)

    # The number on the card is already the damage *after* type effectiveness
    # (the game prints 140 -> 70 against a resisting target), so the biggest
    # number is the right pick and colour only breaks ties.
    #
    # Preferring "not red" first was a real bug: with slot 2 dimmed by energy
    # the hand was 120(red) + a stray "1" misread off slot 4, the filter threw
    # away the 120, kept the 1, and concluded "everything is weak" - so the bot
    # switched pets for nothing.
    pool = sorted(damaged,
                  key=lambda p: (p[1], p[0]["color"] != "red"), reverse=True)
    best, best_v = pool[0]

    # Everything on hand is weak: hand the turn to a fresh pet instead of
    # chipping away. The caller supplies the slot of a living bench pet.
    # Require at least two readable numbers - a single tiny value is usually a
    # misread, not a weak hand.
    slot = ctx.get("switch_slot")
    if (slot and ctx.get("switch_streak", 99) >= 3
            and len(damaged) >= 2 and best_v < SWITCH_DMG):
        return ("switch", slot), "all skills weak (best %s), switch to slot %d" % (
            best["text"], slot)

    return ("skill", best["index"]), "best affordable dmg %s (%s)" % (
        best["text"], best["color"])


def _match_name(st, cands):
    """Best-effort: first dash skill whose index maps to a heal name."""
    names = st.get("names") or []
    for s in cands:
        if s["index"] - 1 < len(names) and any(h in names[s["index"] - 1] for h in HEAL_HINTS):
            return s["index"]
    return None


# ------------------------------------------------------------------ main loop
class Bot:
    def __init__(self, args):
        self.screen = Screen()
        self.perc = Perc(self.screen)
        self.args = args
        self.loop = asyncio.new_event_loop()
        self.ble = rocs.Session(verbose=False)
        self.acted_at = 0.0
        self.switched_at = 0.0
        self.clicked_at = 0.0
        self.bar_gone_since = None
        self.battle_false_since = None
        self.turn_key = None
        self.prev_key = None
        self.stable_since = 0.0
        self.last_plan = None
        self.miss_streak = 0
        self.last_battle_at = 0.0
        self.idle_seen = None
        self.advance_at = 0.0
        self.advance_count = 0
        self.advance_gave_up = False
        self.result_seen_at = None
        self.gate_logged_at = 0.0
        self.turn_gate_since = None
        self.match_ended_at = 0.0
        self.suppress_until = 0.0
        self.faint_at = None
        self.switch_attempt = 0
        self.switch_gave_up = False
        self.switch_tried_at = 0.0
        self.switch_pending = None
        # Rows that were pressed but did not take (fainted pets, greyed out).
        # Cleared whenever a switch actually succeeds or the panel disappears.
        self.switch_dead = set()
        # slot -> pet name, in party order. Without OCR we cannot know this yet,
        # so it stays empty and _pick_switch_slot degrades to "first living".
        self.bench_names = {}
        # Active pet, read from the HUD bust (see portraits.py). This is the
        # only trustworthy species signal: the name text is OCR-unreliable and
        # the party panel does not include the pet that is on the field.
        self.active_pet = None
        self.pet_at = 0.0
        self._pet_poll_at = 0.0
        self._pet_unknown_at = 0.0
        self.foe_name = None
        self.my_name = None
        self.active_slot = None
        self.energy = None
        # Leader-form tracking (恶魔狼 首领血脉).
        # dead_count = own pets that have fainted this match; the "悲悯" trait
        # grants +30% dual attack per fainted ally, which is why the wolf is
        # saved for the late game.
        # Party order from myteam_wolf.md; slot 4 is the wolf.
        self.party_order = [1, 2, 3, 4, 5, 6]
        self.leader_slot = 4          # 恶魔狼
        self.leader_done = False      # 首领化 is once per match
        self.leader_enabled = False   # off until the panel mapping is verified
        self.dead_count = 0
        self.switch_count = 0
        self.last_switch_turn = -99
        self.unreadable_since = None
        self.nodraw_since = None
        self.prev_my_pct = None
        self.heal_streak = 0
        self.charge_streak = 0
        self.last_progress_at = time.time()
        self.stuck_count = 0
        self.turn = 0
        self.match_started = None
        self.match_count = 0
        self.last_state = None
        self.focused_at = 0.0
        self._titlebar_at = 0.0

    def connect(self):
        return self.loop.run_until_complete(self.ble.connect())

    def _gate(self, now, reason):
        """Explain, at most once a second, why the decision gate stayed shut.

        The 15:45 match produced 0 actions and the log contained only heartbeat
        lines, so there was no way to tell which condition blocked it. This makes
        the gate self-explaining without flooding the log.
        """
        if now - self.gate_logged_at < 1.0:
            return
        self.gate_logged_at = now
        log("GATE %s" % reason)

    def _game_focused(self):
        return bool(self.screen.hwnd) and _UD.GetForegroundWindow() == self.screen.hwnd

    def _ble(self, coro_factory, what="cmd"):
        """Run a BLE coroutine, surviving a dropped link.

        rocs.Session.send() raises RuntimeError("board offline") when the board
        is gone, and nothing used to catch it: the exception escaped run(), the
        process exited rc=1 without a traceback in the log, and the supervisor
        restarted it four seconds later mid-match. Reconnecting here keeps the
        bot alive across board resets and radio hiccups.
        """
        for attempt in (1, 2):
            try:
                return self.loop.run_until_complete(coro_factory())
            except Exception as e:
                log("BLE %s failed (%s), attempt %d" % (what, e, attempt))
                try:
                    self.loop.run_until_complete(self.ble.close())
                except Exception:
                    pass
                ok = False
                try:
                    ok = self.loop.run_until_complete(self.ble.connect())
                except Exception:
                    ok = False
                if not ok:
                    time.sleep(0.5)
                    continue
        return None

    def _click_to_focus(self):
        """Get the game into the foreground using a real hardware click.

        SetForegroundWindow is refused for a background process, and this bot
        runs under whatever launched it. A hardware click on a harmless part of
        the window is user input, which Windows does honour, and the board can
        produce it. Point: the middle of the arena, away from every button.

        The window must be raised FIRST. A click is delivered to whatever window
        is topmost at that pixel, so with the editor covering the game the click
        used to focus the *editor* - which is why every FOCUS LOST line was
        followed by another one.
        """
        r = self.screen.rect
        if not r:
            return False
        try:
            if self.screen.hwnd:
                _raise_topmost(self.screen.hwnd)
            self.loop.run_until_complete(
                self.ble.click_at(r[0] + 700, r[1] + 300))
            time.sleep(0.35)
        except Exception:
            return False
        return self._game_focused()

    def send_key(self, k, allow_focus_click=True):
        """Hardware key press, but only while the game owns the foreground.

        Keys land in whatever window has focus. Without this gate a lost focus
        turns every decision into typing into whatever happens to be on top -
        that is exactly how the bot spent ten minutes at 09:38 clicking through
        a browser dashboard instead of the game.

        Getting focus back is done with a hardware click, not just
        SetForegroundWindow: the latter is refused whenever another process owns
        the foreground, which is the normal case here because the bot runs under
        an editor.

        `allow_focus_click=False` is required for any key sent while a modal
        panel is open: the focus click lands in the middle of the screen, which
        for the party panel means "click empty space" and closes it again. That
        is why "E then number" produced no selection - E opened the panel, then
        the click before the number key dismissed it.
        """
        if not self._game_focused() and not self.ensure_focus():
            if not allow_focus_click or not self._click_to_focus():
                log("FOCUS LOST - key %s dropped (fg=%s game=%s)"
                    % (k, _UD.GetForegroundWindow(), self.screen.hwnd))
                return False
        r = self._ble(lambda: self.ble.key(str(k)), "key %s" % k)
        return r is not None

    def ensure_focus(self):
        """Return True only if the game really owns the foreground.

        Windows refuses SetForegroundWindow from a background process, and the
        cache shortcut used to report success without checking anything. Now the
        fast path requires the focus to actually be ours, and a refused switch
        falls back to a hardware click on the title bar (real user input is
        allowed to move focus) plus one retry.
        """
        if self._game_focused():
            self.focused_at = time.time()
            return True
        hwnd = self.screen.hwnd
        if not hwnd:
            return False
        if focus(hwnd):
            self.focused_at = time.time()
            return True
        if time.time() - self._titlebar_at > 2.0:
            self._titlebar_at = time.time()
            try:
                r = self.screen.rect
                if r:
                    self._ble(lambda: self.ble.click_at(r[0] + 700, r[1] + 18),
                              "titlebar")
                    time.sleep(0.25)
            except Exception:
                pass
        ok = focus(hwnd)
        self.focused_at = time.time()
        return ok

    def run(self):
        if not self.connect():
            log("BLE FAILED - aborting")
            return 1
        self.ensure_focus()
        log("bot up; rect=%s" % (self.screen.rect,))
        # PID 文件：UI 用它判断"机器人是否在跑"，避免每次轮询都 spawn 一个
        # PowerShell 查进程（实测一次 2.2 秒，UI 会被拖住）。
        try:
            with open(os.path.join(LOG_DIR, "bot.pid"), "w") as f:
                f.write(str(os.getpid()))
            import atexit

            def _drop_pid():
                try:
                    os.remove(os.path.join(LOG_DIR, "bot.pid"))
                except OSError:
                    pass
            atexit.register(_drop_pid)
        except Exception:
            pass
        last_log = 0.0
        self.match_started = None
        self.match_count = 0
        self.deadline = (time.time() + self.args.seconds) if self.args.seconds else None
        while True:
            if self.deadline and time.time() > self.deadline:
                log("deadline reached, exiting")
                return 0
            # A perception exception used to kill the process (rc=1, no traceback
            # in the log, supervisor restart mid-match). One bad frame must not
            # end the run: log it, back off, keep polling.
            try:
                st = self.perc.observe()
            except Exception as e:
                self.perc_errors = getattr(self, "perc_errors", 0) + 1
                log("PERCEPTION ERROR #%d: %s: %s" % (
                    self.perc_errors, type(e).__name__, e))
                if self.perc_errors in (1, 10, 100):
                    import traceback
                    log(traceback.format_exc().replace("\n", " | ")[:400])
                time.sleep(0.5)
                continue
            self.last_state = st
            now = time.time()

            # ---- battle
            # A "match" only begins once we actually see a decision window (cards
            # or countdown). Menus and cutscenes that happen to have HP-bar-like
            # borders were being counted as matches, producing 8-second 'games'
            # with one action.
            live = bool(st["battle"] and (st["bar"] or st["countdown"]))
            if st["battle"]:
                self.last_battle_at = now
                self.battle_false_since = None
                if self.match_started is None:
                    if live:
                        self.match_started = now
                        self.match_count += 1
                        self.turn = 0
                        self.turn_key = None
                        self.prev_key = None
                        self.miss_streak = 0
                        self.acted_at = 0.0
                        self.match_ended_at = 0.0
                        self.prev_my_pct = None
                        self.heal_streak = 0
                        self.charge_streak = 0
                        self.advance_count = 0
                        self.advance_gave_up = False
                        self.active_pet = None
                        self.leader_done = False
                        self.pet_at = 0.0
                        self._pet_poll_at = 0.0
                        # Per-match tally: a lifetime counter made the panel
                        # log read "dead=9" for a six-pet team.
                        self.dead_count = 0
                        log("=== MATCH %d START %s ===" % (
                            self.match_count, time.strftime("%H:%M:%S")))
                self._battle_tick(st, now)

                # Active-pet identity comes from the HUD bust, not from the
                # party panel, and it drives the 首领化 (leader form) trigger:
                # the transformation costs no turn, so doing it the moment the
                # wolf lands is strictly better than waiting for more 悲悯
                # stacks - the trait scales with fainted allies by itself.
                self._check_active_pet(st, now)
                # 首领化确认：变成狼的"银白发光"头像说明已经首领化过，
                # 这样 leader_done 不再依赖"按键发出去了"这个假设。
                if self.active_pet == "wolf_leader" and not self.leader_done:
                    self.leader_done = True
                    log("LEADER -> confirmed (bust switched to leader form)")
                leader_ready = (
                    (now - self.pet_at > 2.0 and st.get("bar"))
                    or now - self.pet_at > 4.0)
                if (self.active_pet == "wolf" and not self.leader_done
                        and self.faint_at is None and leader_ready):
                    self._leader_transform()

            # ---- not in battle: lobby / vs screen / result
            else:
                if self.battle_false_since is None:
                    self.battle_false_since = now
                # a match only counts as over after a clear pause, so a single
                # flicker in the HP-bar border check cannot split one match in two
                if self.match_started is not None and now - self.battle_false_since > 6.0:
                    self._end_match()
                self._idle_tick(now)

            # ---- the result screen keeps the HP bars drawn, so the branch above
            # never runs. Without this check the bot sits on the summary screen
            # until the watchdog fires, which is exactly the "215 s, 3 actions"
            # match at 17:22.
            #
            # Guard: the party panel (forced switch) also stacks five or six
            # rows of text down the left side, and while it is open this check
            # fired every poll - ending the match every 2 s, resetting
            # acted_at, and letting the bot fire the same skill ten times in
            # one turn. A result can only be real after the match has actually
            # been running for a while, so require a minimum age.
            if (self.match_started is not None
                    and now - self.match_started > 45.0
                    and not st["bar"]
                    and is_result_screen(self.screen)):
                if self.result_seen_at is None:
                    self.result_seen_at = now
                    # Keep the frame that triggered the detector: the guard
                    # below is a heuristic and has ended live matches before,
                    # so every hit must leave evidence behind.
                    self._dump_result_frame()
                elif now - self.result_seen_at > 2.5:
                    log("result screen detected -> ending match")
                    self._end_match()
                    self.result_seen_at = None
            else:
                self.result_seen_at = None

            if now - last_log > 15.0:
                last_log = now
                log("state battle=%s bar=%s me=%s foe=%s" % (
                    st["battle"], st["bar"], st["my_pct"], st["foe_pct"]))

            # ---- stuck watchdog
            # If nothing has happened for a minute we are on a screen we do not
            # recognise (that is exactly how the bot sat on 确认首发 for five
            # minutes). Force one action so the flow cannot deadlock.
            if st["battle"]:
                self.last_progress_at = now
            if now - self.last_progress_at > STUCK_S:
                self.stuck_count += 1
                log("STUCK %.0fs -> forcing action #%d" % (
                    now - self.last_progress_at, self.stuck_count))
                self.ensure_focus()
                btn = find_action_button(self.screen)
                if btn is not None:
                    self.click_abs(*btn)
                else:
                    # ESC, not a click. A blind click on the map moves the
                    # character: that is how the bot walked itself out of the PvP
                    # arena into the open world and never got back. ESC opens or
                    # closes a menu and is harmless on every screen we have seen.
                    self.send_key("esc")
                # Do NOT reset advance_count here: that reopened the blind-click
                # budget and let the loop keep firing every minute.
                self.last_progress_at = now
                self.clicked_at = now

            time.sleep(POLL_S)

    def _decide_v2(self, st, ctx):
        """Run policy_v2 on the current frame.

        Returns (action, why), or (None, reason) when the state is too thin for
        the model - the caller then falls back to the legacy decide().
        """
        import policy_v2
        import pvp_engine as E

        my_name = self.my_name
        foe_name = self.foe_name
        if not my_name or not foe_name:
            return None, "policy_v2: pet identity unknown"

        my_max = E.max_hp(my_name)
        foe_max = E.max_hp(foe_name)
        if not my_max or not foe_max:
            return None, "policy_v2: %s not in dataset" % (
                my_name if not my_max else foe_name)

        my_hp = int(my_max * (st["my_pct"] or 0) / 100.0)
        foe_hp = int(foe_max * (st["foe_pct"] or 0) / 100.0)
        if my_hp <= 0:
            return None, "policy_v2: my hp unreadable"

        skills = []
        for s in st["skills"]:
            if not s:
                continue
            # The number on the card is already post-effectiveness damage, so it
            # is exactly the figure policy_v2 wants.
            dmg = s.get("value")
            if dmg is None:
                dmg = _est_value(s)
            cost = 0
            try:
                meta = E.D.skill(s.get("name") or "")
                if meta:
                    cost = int(meta.get("energy_consumption") or 0)
            except Exception:
                pass
            skills.append({
                "i": s["index"], "dmg": dmg, "cost": cost,
                "kind": s.get("kind"), "lit": bool(s.get("lit", True)),
            })

        state = {
            "me": {"name": my_name, "hp": my_hp, "hp_max": my_max,
                   "energy": st.get("energy") if st.get("energy") is not None else 10,
                   "skills": skills},
            "foe": {"name": foe_name, "hp": foe_hp, "hp_max": foe_max},
            "bench": self.bench_state(),
            "charge_streak": self.charge_streak,
        }
        action, why, detail = policy_v2.decide(state)
        if detail:
            why = "%s [v2 %s]" % (why, detail.get("score"))
        return action, why

    def bench_state(self):
        """Bench pets for policy_v2, as far as we know them.

        Without OCR we only have names for the pets we have already seen on
        screen; unknown slots are omitted rather than guessed.
        """
        out = []
        for slot in range(1, 7):
            name = self.bench_names.get(slot)
            if not name:
                continue
            if slot == getattr(self, "active_slot", None):
                continue
            import pvp_engine as E
            mx = E.max_hp(name) or 1
            out.append({"name": name, "hp": mx, "hp_max": mx, "energy": 10})
        return out

    # ---------------------------------------------------------------- pets
    def _check_active_pet(self, st, now):
        """Identify the pet on the field from the top-left HUD bust.

        Cheap (~1 ms grab + a handful of 64x64 correlations), and it is the
        only reliable species signal available: the name is OCR-hostile and
        the party panel never lists the pet that is already out.
        """
        if not st.get("battle"):
            return
        if now - self._pet_poll_at < 0.5:
            return
        self._pet_poll_at = now
        try:
            m = portraits.SEARCH_MARGIN
            box = portraits.box_for(*self.screen.client_size, margin=m)
            img = self.screen.grab_client(box)
        except Exception as e:
            log("PET -> grab failed: %s: %s" % (type(e).__name__, e))
            return
        if img is None or img.size == 0:
            return
        name, sc = portraits.identify(img, target=(box[2] - 2 * m, box[3] - 2 * m))
        if name is not None and sc >= 0.62:
            if name != self.active_pet:
                log("PET -> %s (%.2f)" % (name, sc))
                self.active_pet = name
                self.pet_at = now
            return
        # Unknown bust: keep one sample every 10 s so the library can be
        # extended by hand without replaying a match. Store the centre crop so
        # every template in the library has the same geometry.
        if now - self._pet_unknown_at > 10.0:
            self._pet_unknown_at = now
            ts = time.strftime("%H%M%S")
            path = os.path.join(portraits.LIB_DIR, "_unknown_%s.png" % ts)
            try:
                bh, bw = img.shape[:2]
                portraits.save_template(img[m:bh - m, m:bw - m], path)
                # Also keep the name strip: the bust alone cannot be labelled
                # offline, and reading it here is what turns these samples into
                # a portrait library for the whole team.
                name_img = self.screen.grab_client((0, 0, 460, 170))
                if name_img is not None:
                    cv2.imwrite(os.path.join(
                        portraits.LIB_DIR, "_unknown_%s_name.png" % ts),
                        cv2.cvtColor(name_img, cv2.COLOR_RGB2BGR))
                log("PET -> unknown bust (%.2f) saved %s" % (sc, os.path.basename(path)))
            except Exception:
                pass

    def _dump_unreadable(self, tag):
        """技能栏读不出来时存一张全屏，供离线分析（每 15 秒最多一张）。"""
        if time.time() - getattr(self, "_unread_at", 0) < 15.0:
            return
        self._unread_at = time.time()
        try:
            full = self.screen.grab_full()
            if full is None:
                return
            ts = time.strftime("%H%M%S")
            path = os.path.join(LOG_DIR, "unread_%s_%s.png" % (tag, ts))
            if cv2.imwrite(path, cv2.cvtColor(full, cv2.COLOR_RGB2BGR)):
                log("BAR unreadable -> frame saved %s" % os.path.basename(path))
        except Exception:
            pass

    def _dump_result_frame(self):
        """Save the frame behind a result-screen detection (diagnostics)."""
        if time.time() - getattr(self, "_result_dump_at", 0) < 10.0:
            return
        self._result_dump_at = time.time()
        ts = time.strftime("%H%M%S")
        try:
            full = self.screen.grab_full()
            if full is not None:
                cv2.imwrite(os.path.join(LOG_DIR, "result_hit_%s.png" % ts),
                            cv2.cvtColor(full, cv2.COLOR_RGB2BGR))
            x, y, w, h = RESULT_LIST_BAND
            band = self.screen.grab((x, y, w, h))
            if band is not None:
                cv2.imwrite(os.path.join(LOG_DIR, "result_band_%s.png" % ts),
                            cv2.cvtColor(band, cv2.COLOR_RGB2BGR))
        except Exception:
            pass

    def _leader_transform(self):
        """首领化: W -> 1 -> Space. Free action, once per match."""
        log("LEADER -> 恶魔狼 on field, transforming (W/1/space)")
        if not self.send_key("w"):
            log("LEADER -> w dropped, retry next tick")
            return False
        time.sleep(0.9)
        self.send_key(1)
        time.sleep(0.9)
        for _ in range(3):
            self.send_key("space")
            time.sleep(0.5)
        self.leader_done = True
        log("LEADER -> sequence sent")
        # Keep a snapshot of the bust right after the sequence. If the leader
        # form changes the portrait (it should), this frame is the template we
        # need to verify the next transformation automatically instead of
        # trusting that the key sequence landed.
        time.sleep(1.2)
        try:
            m = portraits.SEARCH_MARGIN
            img = self.screen.grab_client(
                portraits.box_for(*self.screen.client_size, margin=m))
            if img is not None and img.size:
                ts = time.strftime("%H%M%S")
                path = os.path.join(LOG_DIR, "leader_after_%s.png" % ts)
                ok = cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
                log("LEADER -> bust %s %s" % (
                    "saved" if ok else "WRITE FAILED", path))
        except Exception:
            pass
        return True

    def _battle_tick(self, st, now):
        # fainted -> forced switch.
        # Sticky once entered: while the switch panel is open the HP bar is often
        # NOT drawn, so a per-poll `my_ui` test dropped out of this branch every
        # other frame, ran the reset below, and wiped switch_attempt /
        # switch_dead. That is exactly why the log showed "switch slot 3 ...
        # attempt 0" twenty times and never converged.
        # Forced switch: the party panel can appear for two very different
        # reasons, and the bot must react to both.
        #   1. our pet fainted (my_pct <= 0.5)
        #   2. an opponent skill forced it off the field while it was still
        #      healthy - measured at 446/497 HP in the 18:15 match
        # The old test only covered case 1, so case 2 fell through to the skill
        # bar branch, found no bar (the panel covers it), and sat there doing
        # nothing for the rest of the turn.
        #
        # The panel itself is the reliable signal: find_switch_rows returns >= 3
        # rows only when the party list is drawn, and requires the skill bar to
        # be absent, so it cannot fire during a normal decision window.
        fainted = (st["my_pct"] is not None and st["my_pct"] <= 0.5
                   and (st.get("my_ui") or self.faint_at is not None))
        forced_panel = False
        if not fainted and not st["bar"]:
            # The skill bar is already known to be absent, so pass that in and
            # skip a second ~50 ms detection pass.
            forced_panel = len(find_switch_rows(self.screen, bar_visible=False)) >= 3
        if fainted or forced_panel:
            if self.faint_at is None:
                self.faint_at = now
                # Remember the HP at panel time: a real faint enters with the
                # bar at/near 0, a forced switch enters with it high. Only the
                # former counts toward dead_count (悲悯 counts fainted pets).
                self._faint_hp = st["my_pct"] or 0
                if forced_panel:
                    log("FORCED SWITCH -> panel is up at hp %.0f%%"
                        % (st["my_pct"] or 0))
            # The death animation shows 0 HP for a few seconds; _try_switch only
            # acts once the party panel is actually drawn.
            if now - self.faint_at > 1.0 and now - self.switched_at > 2.5:
                self._try_switch(now, st)
            return

        # Only a genuinely healthy pet means the switch succeeded. Resetting on
        # "my_pct > 0.5" was too eager: a flicker of the panel counted as
        # recovery and restarted the whole blacklist.
        if self.faint_at is not None and st["my_pct"] is not None and st["my_pct"] > 5.0:
            log("FAINT -> recovered (hp %.0f%%) after %d attempts"
                % (st["my_pct"], self.switch_attempt))
            # Only a GENUINE faint counts for dead_count: the party panel can
            # also appear while the pet is still healthy (an opponent skill
            # forced it off the field), and the 悲悯 trait counts fainted pets
            # only. The pre-switch HP separates the two cases.
            if getattr(self, "_faint_hp", None) is not None \
                    and self._faint_hp <= 0.5:
                self.dead_count += 1
                log("FAINT -> dead_count=%d" % self.dead_count)
            self._faint_hp = None
            self.faint_at = None
            self.switch_attempt = 0
            self.switch_gave_up = False
            self.switch_tried_at = 0.0
            self.switch_pending = None
            self.switch_dead.clear()

        # ---- is it my decision window?
        # Gate on the SKILL BAR, not on the countdown. The countdown appears a
        # moment before the cards are drawn; acting on the countdown alone made
        # the bot press blind on roughly half its turns.
        if not st["bar"]:
            if self.bar_gone_since is None:
                self.bar_gone_since = now
            # The bar being away for a while used to reset acted_at, which let
            # the bot press again mid-turn whenever the detector flickered
            # during an attack animation. The 8 s pacing gate below is the only
            # retry mechanism now; this reset would defeat it.
            self.prev_key = None
            if st["countdown"]:
                if self.nodraw_since is None:
                    self.nodraw_since = now
                if (now - self.nodraw_since > 8.0
                        and now - self.acted_at > 8.0):
                    # 决策窗口开着但技能栏始终没画出来：先把画面留证，
                    # 下次才能离线看清到底是"动画期间"还是"检测器漏认"。
                    self._dump_unreadable("nodraw")
                    self.ensure_focus()
                    self.send_key(2)
                    self.acted_at = now
                    self.nodraw_since = None
                    self.turn += 1
                    log("T%-3d SKILL 2 (bar never drew, blind)" % self.turn)
            else:
                self.nodraw_since = None
            return
        self.bar_gone_since = None
        self.nodraw_since = None

        # ---- stability gate (with a timeout escape)
        # The read flickers: with 5-6 pills detected the slot->text mapping can
        # change on every single poll, so "same key twice in a row" never happens
        # and this used to return forever (0 actions for a whole match).
        key = (round(st["my_pct"] or 0, 1), round(st["foe_pct"] or 0, 1),
               tuple((s["text"] if s else "") for s in st["skills"]),
               tuple((bool(s and s.get("lit"))) for s in st["skills"]))
        if key != self.prev_key:
            self.prev_key = key
            self.stable_since = now
        stable = (now - self.stable_since) >= 0.15

        # A genuinely new turn: the numbers moved and some time has passed. This
        # is what re-arms the per-turn floors; without it the 8 s retry below
        # blocks the *next* turn too.
        if key != self.turn_key and now - self.acted_at > 2.5:
            self.turn_key = key
            # acted_at stays: a state change during an attack animation is not
            # a new turn. The 8 s gate below owns retry pacing.

        # ---- turn pacing -----------------------------------------------
        # acted_at is deliberately NOT reset by bar flicker or key changes any
        # more; those resets used to sit above this block and let the bot fire
        # five presses in four seconds whenever the bar detector wobbled during
        # an attack animation. The two gates below are now the ONLY thing that
        # can delay a second press, and neither can be bypassed by state churn:
        #   1.2 s hard floor between any two presses
        #   8 s wait for the turn to actually resolve before retrying
        if self.acted_at and now - self.acted_at < 1.2:
            return
        if self.acted_at and now - self.acted_at < 8.0:
            self._gate(now, "press sent, waiting for the turn to resolve")
            return

        if not stable:
            if self.turn_gate_since is None:
                self.turn_gate_since = now
            if now - self.turn_gate_since < 3.0:
                self._gate(now, "read not stable")
                return
            self._gate(now, "stability bypassed after 3s")
        self.turn_gate_since = None

        # context the heal gate needs: did we just take damage, and how many
        # turns in a row have we been healing?
        # ---- patience: if no damage digit is readable, wait briefly for a
        # better frame. 15 s a turn and we act in ~0.2 s, so 2.5 s costs nothing.
        readable = any(s and s["kind"] == "num" and s["value"] is not None
                       for s in st["skills"])
        if not readable:
            if self.unreadable_since is None:
                self.unreadable_since = now
            if now - self.unreadable_since < 2.5:
                self._gate(now, "no readable damage digit")
                return
            self._gate(now, "unreadable, proceeding after 2.5s")
        else:
            self.unreadable_since = None

        took_damage = (self.prev_my_pct is not None
                       and st["my_pct"] is not None
                       and st["my_pct"] < self.prev_my_pct - 0.5)

        ctx = {
            "panic_guard": not self.args.no_guard,
            "took_damage": took_damage,
            "heal_streak": self.heal_streak,
            "switch_slot": self.next_switch_slot(),
            "switch_streak": self.turn - self.last_switch_turn,
            "charge_streak": self.charge_streak,
        }
        action, why = None, ""
        # Try the expected-value policy first. It is the only decision path that
        # looks at turns-to-kill, the incoming hit, energy cost and switch value;
        # the legacy decide() below only compares the four numbers printed on the
        # cards. Falls through silently when the state is too incomplete for the
        # model (unknown pets, no readable cards).
        if not self.args.no_policy_v2:
            try:
                action, why = self._decide_v2(st, ctx)
            except Exception as e:
                log("policy_v2 failed: %s: %s" % (type(e).__name__, e))
                action = None
        if action is None:
            action, why = decide(st, ctx)
        if action is None:
            # digits unreadable this turn
            lit = [s for s in st["skills"] if s and s.get("lit")]
            if not lit:
                # nothing is affordable: gather energy, or rotate if charging
                # keeps being interrupted
                action, why = _charge_or_switch(ctx)
            elif len(lit) == 1:
                # Barely any energy left. Pressing the single affordable card
                # blind usually burns a turn on a buff/heal, so refill instead.
                action, why = ("energy",), "unreadable + only 1 card lit"
            else:
                cached = self.last_plan if (self.last_plan
                                            and self.last_plan[0] == "skill") else None
                # never press a key the game has greyed out: it does nothing,
                # which is how the bot once fired the same skill 17 times in a row
                if cached and not (st["skills"][cached[1] - 1]
                                   and st["skills"][cached[1] - 1].get("lit")):
                    cached = None
                if cached:
                    action, why = cached, "cached plan"
                else:
                    # Cards are lit but we cannot tell which one hurts. Slot 2 is
                    # the main attack on every pet in this team, so default to it;
                    # only fall back to another lit slot if 2 is known to be dark.
                    s2 = st["skills"][1] if len(st["skills"]) > 1 else None
                    slot = 2
                    if s2 is not None and not s2.get("lit"):
                        other = [x for x in lit if x["index"] != 1]
                        slot = other[0]["index"] if other else 1
                    action, why = ("skill", slot), "blind fallback: slot %d" % slot
                    self.miss_streak = 0
        else:
            heal_slot = _heal_slot(st)
            is_heal = action[0] == "skill" and heal_slot and action[1] == heal_slot
            # Cache only real attacks. Caching a heal caused eight straight heals
            # at 54-99% HP; caching 聚能 would replay it forever the same way.
            if action[0] == "skill" and not is_heal:
                self.last_plan = action
            self.heal_streak = (self.heal_streak + 1) if is_heal else 0
            self.miss_streak = 0
        self.prev_my_pct = st["my_pct"]

        ok = False
        if action[0] == "skill":
            ok = self.send_key(action[1])
        elif action[0] == "energy":
            ok = self.send_key("f")
        elif action[0] == "switch":
            # Mid-battle switching is E -> slot -> Space. Verified end to end on
            # 2026-11-18 14:05-14:15: E opened the party list, the number key
            # selected the row, and Space confirmed the swap (音速犬 -> 冥古龙).
            # The forced-switch panel is different: it is already open, so
            # _try_switch sends the slot directly.
            # Sending the slot without E just presses a skill key - that is why
            # the 10:32:07 "retreat" never swapped the pet and it died on the
            # spot.
            # E opens the party list. Take the foreground FIRST, before E, while
            # a click still does no harm: once the panel is open a click in the
            # middle of the screen dismisses it.
            self._click_to_focus()
            ok = self.send_key("e", allow_focus_click=False)
            if ok:
                time.sleep(0.9)
                # Pick the row by clicking its card. Keys are unreliable here
                # because the foreground is stolen between commands and
                # send_key refuses to fire without it; a hardware click always
                # gets the foreground and also confirms the choice.
                rows = find_switch_rows(self.screen, bar_visible=False)
                idx = action[1] - 1
                r = self.screen.rect
                if rows and r is not None and 0 <= idx < len(rows):
                    tx, ty = rows[idx]
                    px, py = r[0] + tx, r[1] + ty
                    log("SWITCH -> click card at %d,%d" % (px, py))
                    # precheck=False: a focus click would dismiss the panel.
                    ok = self.click_abs(px, py, precheck=False)
                    if ok:
                        time.sleep(0.6)
                        # Space deploys the pet (user-verified). Send it up to
                        # three times; a single press can be swallowed.
                        for i in range(3):
                            if not self.send_key("space",
                                                 allow_focus_click=False):
                                log("SWITCH -> space %d dropped" % (i + 1))
                            time.sleep(0.5)
                else:
                    ok = self.send_key(action[1], allow_focus_click=False)
                    if ok:
                        time.sleep(0.9)
                        for i in range(3):
                            if not self.send_key("space",
                                                 allow_focus_click=False):
                                log("SWITCH -> space %d dropped (fallback)"
                                    % (i + 1))
                            time.sleep(0.5)
            if ok:
                self.switch_count += 1
                self.last_switch_turn = self.turn
        if not ok:
            # The key never left the board because the game had lost the
            # foreground. Do not count it as a spent turn: retry on the next
            # poll instead of burning the whole decision window.
            return
        self.acted_at = now
        if action[0] == "energy":
            self.charge_streak += 1
        else:
            self.charge_streak = 0
        self.last_progress_at = now
        self.turn += 1
        lit = "".join("1" if (s and s.get("lit")) else "0" for s in st["skills"])
        log("T%-3d %s (%s) me=%s%% foe=%s%% lit=%s cnt=%d" % (
            self.turn,
            ("SKILL %d" % action[1]) if action[0] == "skill"
            else ("SWITCH %d" % action[1]) if action[0] == "switch" else "ENERGY F",
            why, st["my_pct"], st["foe_pct"], lit, int(st["countdown"])))
        self._dump(st, action, why)

    def _idle_tick(self, now):
        """Lobby / result / pre-match handling.

        Guarded hard: a flicker in battle detection must never make us click
        mid-match, so we only act once we have been out of battle for a while
        AND the same UI has been seen on two consecutive polls.
        """
        if now - self.last_battle_at < 3.0:
            return
        if now < self.suppress_until:
            return

        # The bottom-centre action button (确认首发 / 退出) is the single most
        # common reason to be stuck, so check it BEFORE the VS guard: the
        # pre-match lineup shares the teal/orange palette with the VS splash and
        # the guard would otherwise block the very click that starts the match.
        btn = find_action_button(self.screen)
        if btn is not None and now - self.clicked_at >= CLICK_COOLDOWN_S:
            log("action button -> click %d,%d" % btn)
            self.ensure_focus()
            self.click_abs(*btn)
            self.clicked_at = now
            self.last_progress_at = now
            self.suppress_until = now + 5.0
            self.advance_count = 0
            return

        if has_vs_screen(self.screen):
            # match is loading - hands off
            self.last_battle_at = now
            return
        if now - self.clicked_at < CLICK_COOLDOWN_S:
            return

        # (the bottom-centre action button was already handled above)
        want = "start" if has_start_button(self.screen) else None
        if want is None:
            # Splash screens (ALL CLEAR / rank / reward) only follow a finished
            # match. Advancing during a live match risks forfeiting it, so the
            # advance click is locked out until a match has actually ended.
            may_advance = (self.match_count == 0) or (
                self.match_ended_at and now - self.match_ended_at < 120.0)
            if not may_advance:
                return
            if now - self.advance_at < ADVANCE_COOLDOWN_S:
                return
            if self.advance_count >= 8:
                # Hard cap, never time-reset. The old "reset after 60 s" made the
                # bot emit another 12 blind clicks every minute; at 15:46 that
                # walked the character out of the PvP arena into the open world,
                # where none of our detectors match and it never came back.
                if not self.advance_gave_up:
                    self.advance_gave_up = True
                    log("advance cap reached on an unrecognised screen - "
                        "stopping blind clicks")
                return
            self.advance_count += 1
            if self.advance_count in (1, 5, 8):
                log("advance splash (click %d)" % self.advance_count)
            self.ensure_focus()
            self.click_rel(*ADVANCE_REL)
            self.advance_at = now
            return
        self.advance_count = 0
        if self.idle_seen != want:
            self.idle_seen = want
            return                      # confirm on the next poll
        self.idle_seen = None
        # Snapshot the ladder badge right before queueing: winning adds a star,
        # losing removes one, so comparing this with the value read on the next
        # lobby visit gives the match result without parsing any result screen.
        try:
            import rank as _rank
            badge = _rank.read_badge(self.screen)
            if badge:
                prev = getattr(self, "rank_before", None)
                if prev and prev.get("of") == badge.get("of"):
                    res = _rank.result_from_delta(prev, badge)
                    if res is not None:
                        self._log_result(prev, badge, res)
                self.rank_before = badge
                log("ladder badge %d/%d" % (badge["stars"], badge["of"]))
        except Exception as e:
            log("badge read failed: %s" % e)
        log("lobby -> \u5f00\u59cb\u6311\u6218")
        self.ensure_focus()
        self.click_rel(*LOBBY_BTN_REL)
        # VS splash + battle intro cinematic: 特性 banners, no HP bars yet.
        # Clicking through it just wastes inputs, so stay quiet.
        self.suppress_until = now + 14.0
        self.clicked_at = now

    def click_rel(self, x, y):
        """Click a window-relative point.

        All UI anchors are stored relative to the window so that dragging the
        window does not invalidate them. Using absolute pixels is what made the
        bot click "a bit above 开始挑战" after the user moved the window.
        """
        r = self.screen.rect
        if r is None:
            return False
        return self.click_abs(r[0] + int(x), r[1] + int(y))

    def click_abs(self, x, y, precheck=True):
        """Hardware click, gated on the game actually owning the foreground.

        The cursor is moved by the board, so a click while another window is on
        top would land on that window's UI (it did, repeatedly). A hardware
        click is also the only reliable way to take the foreground back.

        `precheck=False` skips the focus dance entirely and just clicks. Use it
        when a modal panel is open: _click_to_focus() clicks the middle of the
        screen to steal focus, and in the party panel that click DISMISSES the
        panel. That is why "select a pet" produced a selection and then no
        confirm - the focus click closed the panel first.
        """
        if precheck and not self._game_focused() and not self.ensure_focus():
            if not self._click_to_focus():
                log("FOCUS LOST - click %d,%d dropped (fg=%s game=%s)"
                    % (x, y, _UD.GetForegroundWindow(), self.screen.hwnd))
                return False
        r = self._ble(lambda: self.ble.click_at(x, y), "click %d,%d" % (x, y))
        return r is not None

    def _log_result(self, before, after, res):
        """Record win/loss from the ladder badge delta.

        +1 win, -1 loss, 0 unchanged (which in this ladder means the star count
        was capped or the match ended in a draw). Written to results.jsonl so the
        win rate is a query, not a guess.
        """
        path = os.path.join(LOG_DIR, "results.jsonl")
        rec = {
            "ts": time.strftime("%H:%M:%S"),
            "before": before["stars"],
            "after": after["stars"],
            "of": after["of"],
            "delta": res,
            "result": "win" if res > 0 else ("loss" if res < 0 else "flat"),
        }
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
        log("RESULT %s (%d/%d -> %d/%d)" % (
            rec["result"], before["stars"], before["of"],
            after["stars"], after["of"]))

    def _end_match(self):
        started = self.match_started
        self.match_started = None
        ended = time.time()
        self.match_ended_at = ended
        self.last_progress_at = ended
        self.advance_count = 0
        dur = ended - started
        # Win/loss: the ladder badge is the only reliable result signal. It is
        # read in the lobby, so at this point we only know the value captured
        # when the match began; the delta is resolved on the next lobby visit by
        # rank_after(). Record what we have so nothing is lost.
        badge_before = getattr(self, "rank_before", None)
        log("=== MATCH %d END (%.0fs, %d actions) rank_before=%s ===" % (
            self.match_count, dur, self.turn,
            badge_before.get("stars") if badge_before else "?"))
        try:
            with open(ROUND_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "match": self.match_count,
                    "start": time.strftime("%H:%M:%S", time.localtime(started)),
                    "end": time.strftime("%H:%M:%S", time.localtime(ended)),
                    "seconds": round(dur, 1),
                    "actions": self.turn,
                    "rank_before": badge_before.get("stars") if badge_before else None,
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass
        self._spawn_review(started, ended)

    def _spawn_review(self, started, ended):
        """Kick off reviews for this match (fire and forget).

        Two reports:
          * review_turns.py - decision log, the useful one
          * review.py       - pixel timeline, coarse because frames are sparse
        """
        import subprocess
        try:
            t0 = time.strftime("%H%M%S", time.localtime(started - 2))
            t1 = time.strftime("%H%M%S", time.localtime(ended + 2))
            stem = "review_%s_%s" % (t0, t1)
            exe = sys.executable
            subprocess.Popen(
                [exe, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "review_turns.py"),
                 "--from", t0, "--to", t1,
                 "--out", os.path.join(LOG_DIR, stem + ".md")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=0x00000008)
            log("review queued -> %s.md" % stem)
        except Exception as e:
            log("review spawn failed: %s" % e)

    def next_switch_slot(self):
        """Rotate through the party for a mid-battle switch (E -> slot -> Space).

        This is the six-slot party list, which is not the same list as the
        forced-switch panel: that one only ever shows the five pets that are not
        currently out (verified at 1:1: five rows numbered 1-5, no sixth row).
        Mid-battle the party HUD shows all six, numbered 1-6, so 6 is a valid
        key here - and only here.
        """
        slots = [2, 3, 4, 5, 6, 1]
        return slots[self.switch_count % len(slots)]

    def _living_rows(self, rows):
        """Classify party cards as selectable or greyed-out.

        Fainted pets stay in the list, but with an empty HP bar and a grey
        plate, and cannot be picked.

        The previous test (mean channel spread over the whole card) was really
        measuring how colourful the card art was, not whether the pet is alive:
        on the first frame of a real panel it scored 28.7 on the fainted row,
        above its 25 threshold, so the bot would have sent out a corpse.

        The HP bar is the direct signal. Measured on a real panel: the fainted
        row fills 0.03 of this band (that is just the yellow cost star), living
        rows 0.10-0.54. 0.06 sits in the gap.
        """
        out = []
        for i, (tx, ty) in enumerate(rows):
            # The row target is the card's left edge and the HP bar starts a few
            # pixels right of it, so this band lands on the bar for every row
            # including the ones whose position was extrapolated.
            box = self.screen.grab((tx + 6, ty - 16, 175, 30))
            if box is None or box.size == 0:
                continue
            mx = box.max(axis=2).astype(np.int16)
            mn = box.min(axis=2).astype(np.int16)
            filled = ((mx - mn) > 55) & (mx > 145)
            out.append((i + 1, (tx, ty), float(filled.mean())))
        return out

    def _try_switch(self, now, st=None):
        """Pick the next living pet: number key, wait 1 s, Space.

        Two things learned the hard way:
          * the number key needs about a second before Space, otherwise the
            confirmation is swallowed;
          * fainted pets remain in the list, greyed out, and cannot be chosen -
            picking row 1 blindly selects a corpse and the turn is lost.

        Retries are rate-limited by RETRY_S. Without that, a failed send (focus
        lost, board offline) left both switch_attempt and switched_at untouched,
        so the poll loop re-entered this function every 0.12 s and logged
        "FAINT -> switch slot 1 (attempt 0)" hundreds of times while the pet sat
        dead - observed live at 12:59:27-12:59:38.
        """
        if now - getattr(self, "switch_tried_at", 0.0) < SWITCH_RETRY_S:
            return
        # The panel is up, so the skill bar is not; skip re-detecting it.
        rows = find_switch_rows(self.screen, bar_visible=False)
        if len(rows) < 3:
            return
        # Give up after a while instead of hammering a panel that is not
        # listening: the game picks for us after 15-45 s anyway.
        if self.switch_attempt >= 8:
            if not self.switch_gave_up:
                self.switch_gave_up = True
                log("FAINT -> 8 attempts failed, waiting for auto-pick")
            return
        cand = self._living_rows(rows)
        if not cand:
            return
        alive = [c for c in cand if c[2] >= 0.06]
        pool = alive if alive else cand

        # If the previous attempt already sent the number key but Space was
        # dropped (focus loss), the panel is still open with that row selected.
        # Resend Space instead of starting over with another number - restarting
        # is why the log shows "attempt 0" forever.
        if getattr(self, "switch_pending", None) is not None:
            slot = self.switch_pending
            # The panel is still up, which means the previous attempt did not
            # complete: either the row was a fainted pet (greyed out) or Space
            # never reached the game. Either way this row is not working right
            # now, so blacklist it and move on. Retrying the same row is what
            # produced "switch slot 3 ... attempt 0" twenty times in a row.
            self.switch_dead.add(slot)
            log("FAINT -> row %d did not take, blacklisting it (%d dead)"
                % (slot, len(self.switch_dead)))
            self.switch_pending = None
            self.switch_attempt += 1
            self.switch_tried_at = 0.0
            return

        # Choose the best replacement instead of rotating.
        # Rotating (switch_attempt % len(pool)) only skipped corpses and ignored
        # everything that decides a matchup, which is why the log shows
        # slot 3 -> slot 1 -> slot 2 within 18 seconds.
        pool = [c for c in pool if c[0] not in self.switch_dead]
        if not pool:
            if not self.switch_gave_up:
                self.switch_gave_up = True
                log("FAINT -> every row blacklisted, waiting for auto-pick")
            return
        slot, why = self._pick_switch_slot(pool, st)
        log("FAINT -> switch slot %d (%s, attempt %d)"
            % (slot, why, self.switch_attempt))
        self.switch_tried_at = now
        self.switch_pending = slot

        # Select AND confirm with one hardware click on the row's card.
        #
        # Why not keys: the bot runs under an editor, and the foreground is
        # stolen between every two commands. send_key refuses to fire unless the
        # game owns the foreground (correct - otherwise keys land in the editor),
        # so "E, then number" reliably lost the number and the log filled with
        # "FOCUS LOST - key N dropped (fg=1705966)".
        #
        # A hardware click is real user input: Windows always gives it the
        # foreground, and the user confirmed that a left click on a row both
        # selects and deploys the pet. So one click does the whole job and the
        # focus problem disappears.
        target = None
        for row_slot, pos, _fill in pool:
            if row_slot == slot:
                target = pos
                break
        r = self.screen.rect
        if target is not None and r is not None:
            px, py = r[0] + target[0], r[1] + target[1]
            log("FAINT -> click card at %d,%d" % (px, py))
            # precheck=False: a focus click in the middle of the screen would
            # dismiss the panel before the card is hit.
            if not self.click_abs(px, py, precheck=False):
                log("FAINT -> card click FAILED (board/focus), retry next tick")
                return                  # pending stays set, blacklisted next tick
            time.sleep(0.6)
            # Confirm with SPACE, several times. The user measured this in
            # game: after a row is clicked, Space deploys it - the centre click
            # alone does not. Space can be swallowed by focus churn, so send it
            # up to three times.
            for i in range(3):
                if not self.send_key("space", allow_focus_click=False):
                    log("FAINT -> space %d dropped" % (i + 1))
                time.sleep(0.5)
            # 首领化 is NOT triggered here any more. The panel-row -> party-slot
            # mapping was disproved (the panel excludes the pet on the field, so
            # "slot == leader_slot" never matched) and a death-count guess is
            # only a proxy. The HUD bust in the top-left corner names the pet
            # that actually landed, so _check_active_pet()/_leader_transform()
            # fire the W/1/Space sequence from the run loop instead.
        else:
            # No row geometry available: fall back to the key sequence.
            if not self.send_key(slot, allow_focus_click=False):
                return
            time.sleep(0.9)
            for i in range(3):
                if not self.send_key("space", allow_focus_click=False):
                    log("FAINT -> space %d dropped (fallback)" % (i + 1))
                time.sleep(0.5)
        # Do NOT assume success here. Whether the row was selectable is only
        # known on the next poll: if the panel is still up, the pending slot is
        # blacklisted at the top of this function. Clearing the blacklist here
        # would erase exactly that information.
        self.switch_attempt += 1
        self.switched_at = now

    def _pick_switch_slot(self, pool, st):
        """Score each living replacement and take the best.

        Value of a switch = how hard it hits the current foe, minus how hard the
        foe hits it, plus a type-matchup bonus. Uses the same damage model as the
        decision layer so the two never disagree.

        Panel-row mapping (to be verified against a real screenshot): the panel
        lists the LIVING pets that are not on the field, in party order. With
        party [1..6], one active and dead_count fainted, the row order is
        deduced rather than known - each pick logs the deduction so the user can
        correct it from the game.
        """
        # Derive which party slots the panel rows represent.
        active = getattr(self, "active_slot", None)
        alive = [s for s in self.party_order if s != active]
        # Fainted pets stay in the list but greyed out; the bot cannot tell
        # which rows are dead, so no subtraction here - _living_rows handles
        # that separately. Log the mapping for the user to verify.
        mapping = {i + 1: slot for i, slot in enumerate(alive)}
        log("PANEL MAP: rows=%s dead=%d -> %s"
            % (len(pool), self.dead_count, mapping))
        foe = (st.get("foe_name") if st else None) or self.foe_name
        if not foe:
            # Without the opponent's identity we cannot score matchups; fall back
            # to the first living row so we still act instead of standing still.
            slot = pool[0][0]
            return slot, "no foe identity, first living (map %s)" % mapping

        best = None
        for slot, _pos, _fill in pool:
            name = self.bench_names.get(slot)
            if not name:
                continue
            try:
                import pvp_engine as E
                dmg = self._best_damage(name, foe)
                inc = E.worst_incoming(foe, name, top=1)
                taken = inc[0]["dmg"] if inc else 0
                hp = E.max_hp(name) or 1
                score = dmg / 200.0 - taken / float(hp)
            except Exception:
                continue
            if best is None or score > best[0]:
                best = (score, slot, name, dmg, taken)
        if best is None:
            slot = pool[0][0]
            return slot, "no model data, first living"
        _s, slot, name, dmg, taken = best
        return slot, "%s dmg %d taken %d" % (name, dmg, taken)

    def _best_damage(self, name, foe):
        import pvp_engine as E
        return E.best_damage_from(name, foe)


    def _dump(self, st, action, why):
        rec = {
            "ts": time.strftime("%H:%M:%S", time.localtime(st["t"])),
            "turn": self.turn,
            "my_pct": st["my_pct"], "foe_pct": st["foe_pct"],
            "action": list(action), "why": why,
            # 身份字段：只有头像库认得出来时才有值（目前只有恶魔狼）
            "our_pet": getattr(self, "active_pet", None),
            "foe_pet": getattr(self, "foe_name", None),
            "skills": [{"i": s["index"], "v": s["value"], "c": s["color"],
                        "t": s["text"]} if s else None for s in st["skills"]],
        }
        try:
            with open(TURN_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
        # 对位库：写失败绝不影响对局
        try:
            import matchup_db
            if getattr(self, "_db", None) is None:
                self._db = matchup_db.connect()
            started = (time.strftime("%H:%M:%S", time.localtime(self.match_started))
                       if self.match_started else None)
            matchup_db.record_decision(self._db, {
                "match_started": started, "turn": self.turn, "ts": rec["ts"],
                "our_pet": rec["our_pet"], "foe_pet": rec["foe_pet"],
                "my_pct": rec["my_pct"], "foe_pct": rec["foe_pct"],
                "action": list(action), "why": why, "skills": [],
            })
        except Exception:
            self._db = None


def probe(n):
    scr = Screen()
    perc = Perc(scr)
    for i in range(n):
        t0 = time.time()
        st = perc.observe()
        dt = time.time() - t0
        sk = " ".join("%d:%s/%s" % (s["index"], s["text"], s["color"]) for s in st["skills"] if s)
        print("%2d %s %5.0fms battle=%d bar=%d(%d) me=%s foe=%s | %s" % (
            i, time.strftime("%H:%M:%S"), dt * 1000, st["battle"], st["bar"], st["npills"],
            st["my_pct"], st["foe_pct"], sk), flush=True)
        time.sleep(POLL_S)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--probe", type=int, default=0)
    ap.add_argument("--no-guard", action="store_true")
    ap.add_argument("--no-policy-v2", action="store_true",
                    help="disable the expected-value policy, use the legacy picker")
    ap.add_argument("--switch-slot", type=int, default=1)
    ap.add_argument("--seconds", type=int, default=0,
                    help="stop after N seconds (0 = run forever)")
    args = ap.parse_args()
    if args.probe:
        probe(args.probe)
        return 0
    if args.once:
        probe(1)
        return 0
    if not acquire_lock():
        print("another robot.py is already running; exiting")
        return 3
    try:
        return Bot(args).run()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    os.makedirs(LOG_DIR, exist_ok=True)
    sys.exit(main())

