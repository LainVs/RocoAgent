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
import rocs  # noqa: E402  (asyncio BLE session)
import asyncio  # noqa: E402

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

# UI anchors (window-relative), measured pixel-exactly from live frames
LOBBY_BTN = (1980, 1480, 520, 150)     # 开始挑战  (gold blob centre ~2210,1567)
RESULT_BTN = (1150, 1470, 340, 130)    # 退出      (white blob centre ~1293,1532)
LOBBY_BTN_ABS = (2648, 1605)           # physical, verified clickable
RESULT_BTN_ABS = (1736, 1571)          # physical, verified clickable
ADVANCE_ABS = (1733, 1279)             # neutral spot that advances splash screens
ACTION_BTN_BAND = (1100, 1480, 400, 150)   # bottom-centre button slot
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


def focus(hwnd):
    if _UD.GetForegroundWindow() == hwnd:
        return True
    _UD.ShowWindow(hwnd, 9)          # SW_RESTORE
    ok = _UD.SetForegroundWindow(hwnd)
    time.sleep(0.05)
    return bool(ok) or _UD.GetForegroundWindow() == hwnd


# ------------------------------------------------------------------ capture
class Screen:
    """Region grabber. Full-window grabs cost ~100 ms; region grabs ~10 ms.

    We only ever need three small regions, so the poll loop stays at ~10 Hz.
    """

    def __init__(self):
        self.sct = mss.mss()
        self.rect = None
        self.hwnd = None
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
        self.last_refresh = time.time()

    def grab(self, box):
        """box = (x, y, w, h) window-relative -> RGB ndarray."""
        if self.rect is None:
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
            visible, npills = _is_skillbar(crop, reg)
            if visible:
                for i, pill in enumerate(_slots(crop, reg)):
                    r = dr.read_skill(crop, pill, self.tpl)
                    r["index"] = i + 1
                    r["lit"] = _card_lit(crop, pill) if pill else False
                    skills[i] = r

        cnt = False
        if battle:
            full = self.screen.grab_full()
            cnt = countdown_visible(full) if full is not None else False

        return {
            "t": time.time(),
            "battle": battle,
            "my_pct": my["pct"], "my_color": my["color"],
            "foe_pct": foe["pct"], "foe_color": foe["color"],
            "bar": visible, "npills": npills,
            "countdown": cnt,
            "myturn": bool(battle and (cnt or visible)),
            "skills": skills,
        }


def _read_bar(rgb, rect):
    """Same physics as hpwatch.read_bar, inlined so we don't import its CLI."""
    x, y, w, h = rect
    if x + w > rgb.shape[1] or y + h + 1 >= rgb.shape[0]:
        return {"pct": 0.0, "color": "none", "ui": False}
    sub = rgb[y:y + h, x:x + w]
    r = sub[:, :, 0].astype(np.int16)
    g = sub[:, :, 1].astype(np.int16)
    b = sub[:, :, 2].astype(np.int16)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    fm = ((mx - mn) > 60) & (mx > 145)
    cols = np.nonzero(fm.sum(axis=0) >= 2)[0]
    edge = np.concatenate([rgb[y - 2:y, x:x + w], rgb[y + h:y + h + 2, x:x + w]])
    er = edge[:, :, 0].astype(np.int16)
    eg = edge[:, :, 1].astype(np.int16)
    eb = edge[:, :, 2].astype(np.int16)
    emx = np.maximum(np.maximum(er, eg), eb)
    emn = np.minimum(np.minimum(er, eg), eb)
    ui = bool((((emx - emn) < 20) & (emx > 70) & (emx < 125)).mean() > 0.55)
    if not len(cols) or cols[0] > 6:
        return {"pct": 0.0, "color": "none", "ui": ui}
    pct = 100.0 * (cols[-1] + 1) / w
    px = sub[fm]
    if len(px) == 0:
        return {"pct": 0.0, "color": "none", "ui": ui}
    mr, mg = px[:, 0].mean(), px[:, 1].mean()
    color = "green" if mg >= mr else ("orange" if mg > 0.6 * mr else "red")
    return {"pct": round(min(pct, 100.0), 1), "color": color, "ui": ui}


def _is_skillbar(crop, region):
    """Is the skill card column actually drawn? Returns (visible, npills).

    Scenery contains plenty of dark blobs that survive the capsule colour
    filter, so require a card-like lattice: at least 3 slots filled and the
    fitted vertical pitch in the 150-215 px range.
    """
    try:
        pills = dr.find_pills(crop, region)
    except Exception:
        return False, 0
    slots = _slots(crop, region)
    filled = [s for s in slots if s]
    if len(filled) < 2:
        return False, len(pills)
    ys = sorted((s["box"][1] + s["box"][3]) / 2.0 for s in filled)
    gaps = [b - a for a, b in zip(ys, ys[1:])]
    # gaps are multiples of the card pitch (one card may be missed entirely)
    ok = all(120 <= g <= 420 for g in gaps)
    return bool(ok), len(pills)


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
    for i, s in enumerate(slots):
        if s is not None:
            continue
        # expected centre from the nearest filled neighbour
        ref = next(((j, ys[j]) for j in (i - 1, i + 1, i - 2, i + 2)
                    if 0 <= j < 4 and ys[j] is not None), None)
        if ref is None:
            continue
        j, yj = ref
        want = yj + (i - j) * pitch
        if not (0 <= want < h):
            continue
        y0 = max(0, int(want - 60))
        y1 = min(h, int(want + 60))
        band = crop[y0:y1, 140:min(w, 430)]
        if band.size == 0:
            continue
        # Slide a capsule-sized window and score how much of it is the capsule's
        # uniform dark grey. Connected components were useless here: the scene
        # behind the card (dark stair rails) is also dark, so blobs merged into
        # one huge region. A uniform-colour window match does not care.
        near = (np.abs(band.astype(np.int16) - 61).max(axis=2) <= 22).astype(np.float32)
        best = None
        for bw, bh in ((68, 32), (64, 30), (72, 36), (60, 28), (66, 34)):
            if band.shape[0] <= bh or band.shape[1] <= bw:
                continue
            for yy in range(0, band.shape[0] - bh + 1, 3):
                for xx in range(0, band.shape[1] - bw + 1, 3):
                    s = float(near[yy:yy + bh, xx:xx + bw].mean())
                    if best is None or s > best[0]:
                        best = (s, xx, yy, bw, bh)
        if best is None or best[0] < 0.42:
            continue
        _, xx, yy, bw, bh = best
        slots[i] = {"box": (xx + 140, yy + y0, xx + 140 + bw, yy + y0 + bh),
                    "kind": "num", "color": None}
    return slots


def _slots(crop, region):
    """Fit the four damage capsules onto a vertical lattice.

    Cards are ~180 px apart. For each card the damage capsule sits to the right
    of its cost badge, so within a slot we keep the pill with the largest x0.
    Searching the lattice beats classifying badges one by one: the badge star's
    colour changes (yellow -> red/green) when energy is low, which is exactly
    when naive badge detection breaks.
    """
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
    return _fill_missing(crop, best[1])


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


def find_switch_rows(screen):
    """Detect the forced-switch party list (5 staggered cards down the left).

    Each row is a light rounded plate (~155x70) with the pet icon on top. The
    death animation has none of these, so this cleanly separates "pet just died"
    from "the game is actually waiting for me to pick a replacement".

    Returns a list of window-relative (x, y) click targets, top row first.
    """
    X0, Y0, W, H = 300, 400, 620, 950
    crop = screen.grab((X0, Y0, W, H))
    if crop is None:
        return []
    # The skill cards' white name plates live in the same area and match the
    # same "light rounded rect" test. If the skill bar is on screen this is a
    # normal battle, not the switch panel.
    x0, y0, x1, y1 = SKILL_BOX
    bar = screen.grab((x0, y0, x1 - x0, y1 - y0))
    if bar is not None:
        visible, _ = _is_skillbar(bar, (0, 0, x1 - x0, y1 - y0))
        if visible:
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
SWITCH_DMG = 40        # below this, a fresh pet is usually better


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

    damaged = [s for s in usable if s["kind"] == "num" and s["value"] is not None]
    if not damaged:
        if usable:
            # Cards are lit but no damage number could be read: that is a failed
            # read, not an empty hand. Tell the caller so it can reuse its last
            # working plan instead of burning the turn on 聚能.
            return None, "lit cards but unreadable"
        return ("energy",), "nothing affordable, gather energy"

    good = [s for s in damaged if s["color"] != "red"]
    pool = good if good else damaged
    pool.sort(key=lambda s: s["value"], reverse=True)
    best = pool[0]

    # Everything on hand is weak: hand the turn to a fresh pet instead of
    # chipping away. The caller supplies the slot of a living bench pet.
    slot = ctx.get("switch_slot")
    if slot and ctx.get("switch_streak", 99) >= 3 and best["value"] < SWITCH_DMG:
        return ("switch", slot), "all skills weak (best %s), switch to slot %d" % (
            best["value"], slot)

    return ("skill", best["index"]), "best affordable dmg %s (%s)" % (
        best["value"], best["color"])


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
        self.match_ended_at = 0.0
        self.suppress_until = 0.0
        self.faint_at = None
        self.switch_attempt = 0
        self.switch_gave_up = False
        self.switch_count = 0
        self.last_switch_turn = -99
        self.unreadable_since = None
        self.nodraw_since = None
        self.prev_my_pct = None
        self.heal_streak = 0
        self.last_progress_at = time.time()
        self.stuck_count = 0
        self.turn = 0
        self.match_started = None
        self.match_count = 0
        self.last_state = None
        self.focused_at = 0.0

    def connect(self):
        return self.loop.run_until_complete(self.ble.connect())

    def send_key(self, k):
        self.loop.run_until_complete(self.ble.key(str(k)))

    def ensure_focus(self):
        if time.time() - self.focused_at < 3.0:
            return True
        ok = focus(self.screen.hwnd) if self.screen.hwnd else False
        self.focused_at = time.time()
        return ok

    def run(self):
        if not self.connect():
            log("BLE FAILED - aborting")
            return 1
        self.ensure_focus()
        log("bot up; rect=%s" % (self.screen.rect,))
        last_log = 0.0
        self.match_started = None
        self.match_count = 0
        self.deadline = (time.time() + self.args.seconds) if self.args.seconds else None
        while True:
            if self.deadline and time.time() > self.deadline:
                log("deadline reached, exiting")
                return 0
            st = self.perc.observe()
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
                        log("=== MATCH %d START %s ===" % (
                            self.match_count, time.strftime("%H:%M:%S")))
                self._battle_tick(st, now)

            # ---- not in battle: lobby / vs screen / result
            else:
                if self.battle_false_since is None:
                    self.battle_false_since = now
                # a match only counts as over after a clear pause, so a single
                # flicker in the HP-bar border check cannot split one match in two
                if self.match_started is not None and now - self.battle_false_since > 6.0:
                    self._end_match()
                self._idle_tick(now)

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
                    self.click_abs(*ADVANCE_ABS)
                    self.click_abs(*RESULT_BTN_ABS)
                self.last_progress_at = now
                self.clicked_at = now
                self.advance_count = 0

            time.sleep(POLL_S)

    def _battle_tick(self, st, now):
        # fainted -> forced switch
        if st["my_pct"] is not None and st["my_pct"] <= 0.5:
            if self.faint_at is None:
                self.faint_at = now
            # The death animation shows 0 HP for a few seconds; _try_switch only
            # acts once the party panel is actually drawn.
            if now - self.faint_at > 1.0 and now - self.switched_at > 2.5:
                self._try_switch(now)
            return
        if self.faint_at is not None:
            self.faint_at = None
            self.switch_attempt = 0
            self.switch_gave_up = False

        # ---- is it my decision window?
        # Gate on the SKILL BAR, not on the countdown. The countdown appears a
        # moment before the cards are drawn; acting on the countdown alone made
        # the bot press blind on roughly half its turns.
        if not st["bar"]:
            if self.bar_gone_since is None:
                self.bar_gone_since = now
            # the bar must be *continuously* absent for 1.5 s to count as a new
            # turn (a detector flicker is far shorter)
            if now - self.bar_gone_since > 1.5:
                self.acted_at = 0.0
            self.prev_key = None
            if st["countdown"]:
                if self.nodraw_since is None:
                    self.nodraw_since = now
                if (now - self.nodraw_since > 8.0
                        and now - self.acted_at > 8.0):
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

        # ---- require a stable read (the bar fades in; half-drawn frames gave
        #      nonsense like 'dmg 5')
        key = (round(st["my_pct"] or 0, 1), round(st["foe_pct"] or 0, 1),
               tuple((s["text"] if s else "") for s in st["skills"]),
               tuple((bool(s and s.get("lit"))) for s in st["skills"]))
        if key != self.prev_key:
            self.prev_key = key
            self.stable_since = now
            if self.acted_at == 0.0:
                return
        if now - self.stable_since < 0.15 and self.acted_at == 0.0:
            return

        # ---- turn boundary
        self.turn_key = key

        # ---- patience: if no damage digit is readable, wait for a better frame
        # We have 15 s a turn and act in ~0.2 s, so waiting 2.5 s costs nothing
        # and avoids spending the turn on a blind guess.
        readable = any(s and s["kind"] == "num" and s["value"] is not None
                       for s in st["skills"])
        if not readable:
            if self.unreadable_since is None:
                self.unreadable_since = now
            if now - self.unreadable_since < 2.5:
                return
        else:
            self.unreadable_since = None

        # hard floor: never two presses inside 1.2 s, whatever the state says
        if self.acted_at and now - self.acted_at < 1.2:
            return
        # one press per turn, 8 s retry if the press was dropped
        if self.acted_at and now - self.acted_at < 8.0:
            return

        # context the heal gate needs: did we just take damage, and how many
        # turns in a row have we been healing?
        took_damage = (self.prev_my_pct is not None
                       and st["my_pct"] is not None
                       and st["my_pct"] < self.prev_my_pct - 0.5)

        action, why = decide(st, {
            "panic_guard": not self.args.no_guard,
            "took_damage": took_damage,
            "heal_streak": self.heal_streak,
            "switch_slot": self.next_switch_slot(),
            "switch_streak": self.turn - self.last_switch_turn,
        })
        if action is None:
            # digits unreadable this turn
            lit = [s for s in st["skills"] if s and s.get("lit")]
            if not lit:
                # nothing is affordable: gathering energy is the only move
                action, why = ("energy",), "unreadable + no energy"
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

        self.ensure_focus()
        if action[0] == "skill":
            self.send_key(action[1])
        elif action[0] == "energy":
            self.send_key("f")
        elif action[0] == "switch":
            self.send_key(action[1])
            time.sleep(1.0)
            self.send_key("space")
            self.switch_count += 1
            self.last_switch_turn = self.turn
        self.acted_at = now
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
            if self.advance_count >= 12:
                # give up for a while instead of wedging forever
                if now - self.advance_at < 60.0:
                    return
                self.advance_count = 0
            self.advance_count += 1
            if self.advance_count in (1, 5, 12):
                log("advance splash (click %d)" % self.advance_count)
            self.ensure_focus()
            self.click_abs(*ADVANCE_ABS)
            self.advance_at = now
            return
        self.advance_count = 0
        if self.idle_seen != want:
            self.idle_seen = want
            return                      # confirm on the next poll
        self.idle_seen = None
        log("lobby -> \u5f00\u59cb\u6311\u6218")
        self.ensure_focus()
        self.click_abs(*LOBBY_BTN_ABS)
        # VS splash + battle intro cinematic: 特性 banners, no HP bars yet.
        # Clicking through it just wastes inputs, so stay quiet.
        self.suppress_until = now + 14.0
        self.clicked_at = now

    def click_abs(self, x, y):
        self.loop.run_until_complete(self.ble.click_at(x, y))

    def _end_match(self):
        started = self.match_started
        self.match_started = None
        ended = time.time()
        self.match_ended_at = ended
        self.last_progress_at = ended
        self.advance_count = 0
        dur = ended - started
        log("=== MATCH %d END (%.0fs, %d actions) ===" % (
            self.match_count, dur, self.turn))
        try:
            with open(ROUND_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "match": self.match_count,
                    "start": time.strftime("%H:%M:%S", time.localtime(started)),
                    "end": time.strftime("%H:%M:%S", time.localtime(ended)),
                    "seconds": round(dur, 1),
                    "actions": self.turn,
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
        """Rotate through bench slots.

        Mid-battle the party list is not on screen, so we cannot tell which
        pets are alive; rotate and let the failed attempts move on.
        """
        slots = [2, 3, 4, 5, 6]
        return slots[self.switch_count % len(slots)]

    def _living_rows(self, rows):
        """Classify party cards as alive or greyed-out.

        Fainted pets stay in the list but are drawn desaturated, and cannot be
        picked. Sampling the card plate and measuring the mean channel spread
        separates them without OCR.
        """
        out = []
        for i, (tx, ty) in enumerate(rows):
            box = self.screen.grab((tx - 70, ty - 30, 150, 70))
            if box is None:
                continue
            spread = float((box.max(axis=2).astype(np.int16)
                            - box.min(axis=2).astype(np.int16)).mean())
            out.append((i + 1, (tx, ty), spread))
        return out

    def _try_switch(self, now):
        """Pick the next living pet: number key, wait 1 s, Space.

        Two things learned the hard way:
          * the number key needs about a second before Space, otherwise the
            confirmation is swallowed;
          * fainted pets remain in the list, greyed out, and cannot be chosen -
            picking row 1 blindly selects a corpse and the turn is lost.
        """
        rows = find_switch_rows(self.screen)
        if len(rows) < 3:
            return
        cand = self._living_rows(rows)
        if not cand:
            return
        alive = [c for c in cand if c[2] >= 25]
        pool = alive if alive else cand
        slot, _pos, spread = pool[self.switch_attempt % len(pool)]
        log("FAINT -> switch slot %d (spread %.0f, attempt %d)"
            % (slot, spread, self.switch_attempt))
        self.ensure_focus()
        self.send_key(slot)
        time.sleep(1.0)
        self.send_key("space")
        self.switch_attempt += 1
        self.switched_at = now


    def _dump(self, st, action, why):
        rec = {
            "ts": time.strftime("%H:%M:%S", time.localtime(st["t"])),
            "turn": self.turn,
            "my_pct": st["my_pct"], "foe_pct": st["foe_pct"],
            "action": list(action), "why": why,
            "skills": [{"i": s["index"], "v": s["value"], "c": s["color"],
                        "t": s["text"]} if s else None for s in st["skills"]],
        }
        try:
            with open(TURN_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass


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

