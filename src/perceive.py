# -*- coding: utf-8 -*-
"""词表 / 头像指纹 / 技能卡可放判定 / Perc 感知 / 技能栏解析

从 robot.py 拆出（第 518-1070 行），纯搬移，逻辑未改。
"""
from __future__ import annotations

import time
import numpy as np
import cv2

import digitread as dr

from config import (  # noqa: F401
    LOG_DIR, MY_BAR, FOE_BAR, SKILL_BOX,
    log,
)
from winio import Screen


def species_vocab():
    """精灵名词表（缓存），用于 HUD 名字的最近邻纠错。"""
    global _SPECIES_VOCAB
    if _SPECIES_VOCAB is None:
        try:
            import sqlite3
            from portrait_db import DB_PATH
            db = sqlite3.connect(DB_PATH)
            _SPECIES_VOCAB = [r[0] for r in db.execute(
                "SELECT DISTINCT name FROM species WHERE name IS NOT NULL")]
            db.close()
        except Exception:
            _SPECIES_VOCAB = []
    return _SPECIES_VOCAB


def _bust_sig(img):
    """头像的 12x12 灰度粗指纹（用于判断对面有没有换人）。"""
    if img is None or img.size == 0:
        return None
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    return cv2.resize(g, (12, 12), interpolation=cv2.INTER_AREA).astype(np.float32)


def _sig_dist(a, b):
    if a is None or b is None:
        return 1e9
    return float(np.abs(a - b).mean())


def _plate_p75(crop, pill):
    """Brightness of the name plate under a skill card, or None.

    The plate is the cream banner just below the damage capsule. It is the
    one part of the card whose colour does not depend on the skill art, so
    it is the only safe place to read "can this card be used".
    """
    h, w = crop.shape[:2]
    x0, y0, x1, y1 = [int(v) for v in pill["box"]]
    py0, py1 = y1 + 4, y1 + 32
    px0, px1 = x0 + 6, x1 - 6
    if py1 > h or px1 > w or py1 - py0 < 8 or px1 - px0 < 8:
        return None
    plate = crop[py0:py1, px0:px1]
    if plate.size == 0:
        return None
    return float(np.percentile(plate.max(axis=2).astype(np.float32), 75))


def _card_lit(crop, pill):
    """Is this skill card usable (enough energy) or greyed out?

    Greying is an overlay on the whole card: the cream name plate drops from
    ~245 to ~196 and its >230 pixel count goes to zero. 220 sits in the gap.

    The old test measured the art above the capsule, which misreads any skill
    whose icon is naturally dark (e.g. the purple 蝙蝠毒蛊 read 102 vs the
    105 threshold) and misses cards that are greyed but still bright.
    """
    p75 = _plate_p75(crop, pill)
    if p75 is None:
        return True
    return p75 > 220.0


def count_skill_badges(screen):
    """How many ★N cost badges are on screen (0..4, sometimes 5).

    The badge keeps its yellow star even when the card is greyed out, so it
    is the one marker that still says "the skill bar is up" on the dimmed
    frames where find_pills gives up on the capsule colour. The post-match
    summary has none - that is what makes it usable as a "not a result
    screen" test.
    """
    x0, y0, x1, y1 = SKILL_BOX
    crop = screen.grab_s((x0, y0, x1 - x0, y1 - y0))
    if crop is None or crop.size == 0:
        return 0
    d = np.abs(crop.astype(np.int16) - np.array(dr.BG, np.int16)).max(axis=2)
    m = (d <= 10).astype(np.uint8)
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    h, w = crop.shape[:2]
    cnt = 0
    for i in range(1, n):
        bx, by, bw, bh, _area = stats[i]
        if not (28 <= bw <= 110 and 14 <= bh <= 46):
            continue
        if not (1.1 <= bw / float(bh) <= 4.2):
            continue
        if bx + bw > w or by + bh > h:
            continue
        if int(dr.gate_yellow(crop[by:by + bh, bx:bx + bw]).sum()) >= 40:
            cnt += 1
    return cnt


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


# ---------------------------------------------------------------- 数字兜底
#
# 模板集是用 logs/digits/frames 那 110 张帧建的（build_templates.py）。
# 那批语料里「7」只出现过一次（"70"），而且是**粗竖直**的写法。
# 实盘还有另一种写法：**细斜杠**的 7（见 bar_partial_012307 的 427）。
# 它和所有 130 个模板的相关度最高只有 0.45，低于 TPL_MATCH=0.55，
# 于是被判成 '?'。
#
# 实测 173 张 _dump_bar 保存的失败帧、152 个被拒字形，按形状聚类后：
#   簇 0  n=115   全部是这种细斜杠 7   ← 唯一的真数字
#   簇 1~14        卡面图案噪点（云状/斜条/三角/白块），本来就不该当字
# 也就是说 76% 的失败是这一个数字造成的。
#
# 模板匹配对「同一个数字的两种写法」无能为力，但两者的**结构**一样。
# 实测三段墨迹的水平跨度（占字形宽度比例）：
#
#   数字   top(0~22%)  bottom(78~100%)  middle(40~60%)
#   7      0.71~1.00   0.36~0.43        0.29~0.43
#   2      0.86        0.86~0.93        0.43~0.50
#   4      0.64~0.71   0.29~0.71        0.79
#   5      0.86        0.93             0.86
#   1      0.57        1.00             0.29
#
# 只有 7 同时满足「顶部满宽 + 底部窄 + 中部窄」：
#   4 的中间是宽的，1/2/5 的底部是宽的，0/3/6/8/9 底部也宽。
# 所以这条判据只在模板已经拒识时才用，不会影响正常读数。

_SEVEN_TOP_MIN = 0.64
_SEVEN_BOT_MAX = 0.55
_SEVEN_MID_MAX = 0.55


def _band_span(a, f0, f1):
    """a 是二值字形（已裁掉空白行）。返回 [f0,f1) 这一段行里墨迹的水平跨度/宽度。"""
    h, w = a.shape[:2]
    y0 = int(f0 * h)
    y1 = max(y0 + 1, int(f1 * h))
    seg = a[y0:y1]
    if seg.size == 0 or w == 0:
        return 0.0
    cols = seg.any(axis=0)
    xs = np.where(cols)[0]
    if len(xs) == 0:
        return 0.0
    return (xs[-1] - xs[0] + 1) / float(w)


def _looks_like_seven(glyph):
    """结构判据：顶部满宽横杠 + 底部窄 + 中部窄 => 7。"""
    try:
        a = (np.clip(np.asarray(glyph, np.float32), 0, 1) > 0.45).astype(np.uint8)
        rows = np.where(a.any(axis=1))[0]
        if len(rows) < 6:
            return False
        a = a[rows[0]:rows[-1] + 1]
        top = _band_span(a, 0.00, 0.22)
        bot = _band_span(a, 0.78, 1.00)
        mid = _band_span(a, 0.40, 0.60)
        return (top >= _SEVEN_TOP_MIN and bot <= _SEVEN_BOT_MAX
                and mid <= _SEVEN_MID_MAX)
    except Exception:
        return False


_MAX_DMG_DIGITS = 3


def read_skill_with_fallback(crop, pill, tpl):
    """dr.read_skill + 两个拦河坝。

    1) 模板拒识时的 7 兜底
       只补 '?'，不覆盖已经认出来的数字 —— 兜底判据再准也不该推翻
       高置信度的模板结果。补上的槽位带 `recovered=True`。

    2) 位数合理性
       卡面伤害最多 3 位。4 位以上一定是**多切进了一个字形**。
       实测 turns.jsonl 里 25 条 4 位以上的读数全部 >999
       （"1062" / "1294" / "??????"），真实值都是 3 位数。
       不拦住的话，policy_v2 会把它当成"必定秒杀"（KO 权重 0.30），
       "??????" 更会被估成 10**5 = 100000。
       拦下后按"读不出"处理（text="?"，value=None）。
    """
    res = dr.read_skill(crop, pill, tpl)
    if not res or res.get("kind") != "num":
        return res
    txt = res.get("text") or ""
    out = res
    if "?" in txt:
        out = _recover_sevens(crop, pill, res)
        txt = out.get("text") or ""
    if len(txt) > _MAX_DMG_DIGITS:
        out = dict(out)
        out["text"] = "?"
        out["value"] = None
        out["rejected"] = len(txt)
    return out


def _recover_sevens(crop, pill, res):
    """把被模板拒识的细斜杠 7 补回去。返回新 dict（可能没有改动）。"""
    txt = res.get("text") or ""
    try:
        glyphs = dr.extract_glyphs(crop, pill)
    except Exception:
        return res
    if len(glyphs) != len(txt):
        return res
    chars = list(txt)
    changed = False
    for i, ch in enumerate(chars):
        if ch == "?" and _looks_like_seven(glyphs[i]["glyph"]):
            chars[i] = "7"
            changed = True
    if not changed:
        return res
    out = dict(res)
    out["text"] = "".join(chars)
    out["value"] = int(out["text"]) if out["text"].isdigit() else None
    out["recovered"] = True
    return out


class Perc:
    """Reads state from three small screen regions. ~25 ms per observation."""

    def __init__(self, screen):
        self.screen = screen
        self.tpl = dr.load_templates()

    def _bar(self, box):
        """逐区域抓血条。只在 Screen 没有 grab_frame（测试替身）时用。"""
        x, y, w, h = box
        crop = self.screen.grab_s((x, y - 2, w, h + 4))
        if crop is None:
            # 抓不到画??0 血。以前这里返回0.0，窗口最小化/抓屏失败。?
            # 会被力竭分支当成"HP 归零"，立刻去点换人面板（而且点不准）。?
            return {"pct": None, "color": "none", "ui": False}
        return _read_bar(crop, (0, 2, w, h))

    def _bar_from(self, frame, scale, box):
        """同 _bar，但从已经抓好的一整帧里切（省一次 16ms 的抓屏）。"""
        x, y, w, h = box
        if frame is None:
            return self._bar(box)
        crop = self.screen.crop_frame(frame, (x, y - 2, w, h + 4), scale)
        if crop is None:
            return {"pct": None, "color": "none", "ui": False}
        return _read_bar(crop, (0, 2, w, h))

    def _grab(self, frame, scale, box):
        """同 screen.grab_s(box)，但走整帧切片。"""
        if frame is None:
            return self.screen.grab_s(box)
        return self.screen.crop_frame(frame, box, scale)

    def observe(self):
        # 一次抓屏，切出全部区域。实测把 5 次 16ms 的 mss 开销压成 1 次 51ms。
        #
        # 外部可能注入自己的 Screen 实现（providers.py 允许传 screen=...），
        # 没有 grab_frame 就退回逐区域抓，行为与重构前完全一致。
        if hasattr(self.screen, "grab_frame"):
            frame, scale = self.screen.grab_frame()
        else:
            frame, scale = None, (1.0, 1.0)
        my = self._bar_from(frame, scale, MY_BAR)
        foe = self._bar_from(frame, scale, FOE_BAR)
        # 两边血条都能读出数? 只说??HUD"。S5 赛季大厅左上那条金色横幅
        # 正好压在 MY_BAR/FOE_BAR ? 于是大厅被读?battle=1, 机器人对着
        # "开始挑战站一晚上也不点。真血条有灰色底板 (ui), 横幅没有;
        # 技能栏/倒计时在场时也足以证明在战斗里（缩放让底板判据失效时兜底）。?
        bars = my["pct"] is not None and foe["pct"] is not None
        battle = bool(bars and my["ui"] and foe["ui"])

        x0, y0, x1, y1 = SKILL_BOX
        cw, ch = x1 - x0, y1 - y0
        crop = self._grab(frame, scale, (x0, y0, cw, ch))
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
                    r = read_skill_with_fallback(crop, pill, self.tpl)
                    r["index"] = i + 1
                    p75 = _plate_p75(crop, pill) if pill else None
                    r["plate"] = None if p75 is None else int(round(p75))
                    # Same rule as _card_lit; computed here so the plate value
                    # can go into the turn log without a second pass. A missing
                    # capsule means "no card here", which is never pressable.
                    r["lit"] = bool(p75 is not None and p75 > 220.0)
                    skills[i] = r
                # 用"我方装备的技能表"纠正 num/dash 误判。
                # 实测：变化技能（力量增效）的卡面也画数字，digitread
                # 会读成伤害（i1 v=50 kind=num），导致 policy_v2 的
                # buff 分支永远走不到。这里用权威数据覆盖。
                _fix_status_kinds(skills, getattr(self, "my_name", None))
                # 补真实技能名 + 效果分类。
                # 实测（2026-11-21）：下游只有 kind="dash" 这个"无伤害"
                # 标记，无法区分「借用」（随机复制技能，打了纯亏）和
                # 「钧势」（物防+140%，值得优先叠）。实盘连续 7 局
                # 在 T1/T2 打 slot 4，而真实队伍所有 slot 4 都是借用。
                attach_skill_names(skills, getattr(self, "my_name", None))

        cnt = False
        if bars:
            # Grab the numeral's own ROI instead of the whole 2586x1671 window:
            # the full grab cost ~100 ms to read a 340x150 region.
            cnt_crop = self._grab(frame, scale, CNT_ROI)
            cnt = countdown_visible_crop(cnt_crop) if cnt_crop is not None else False
        # 底板判据失效但技能栏画着 -> 仍然是在战斗。
        #
        # 注意：**不能用倒计时兜底**。实测 2026-11-21 03:00，大厅的
        # "进入排位"界面被误判成 battle=True：
        #     my_ui=False foe_ui=False（血条底板没检测到，不在战斗）
        #     但 bars=True（读到大厅横幅上的假数字 20.5/29.8）
        #     cnt=True（大厅的"限时试用系别 5天0小时"被当成倒计时）
        # 于是机器人以为在战斗，连按 5 次 2 键（bar never drew, blind）。
        #
        # 现在要求：要么底板确认，要么技能栏真的画出来了（visible），
        # 单靠倒计时不再足以判定战斗。
        if bars and not battle and visible:
            battle = True

        # Energy: a row of pips under the HP card. Only meaningful in battle,
        # and only reported when the row was actually located.
        en = None
        if battle:
            try:
                import energy as _energy
                pip = self._grab(frame, scale, _energy.PIP_BAND)
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
    """Cost badge ('★N') vs damage capsule.

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


# ---------------------------------------------------------------- 技能表缓存
#
# `_fix_status_kinds` 和 `attach_skill_names` 每帧各开一次 sqlite 连接
# （sqlite3.connect + team_db.dump_team()），实测 27ms + 24ms = **51ms**，
# 比整个技能栏图像处理（32ms）还贵。但它们查的东西**只跟 my_name 有关**：
# 我方 4 个槽位装的是哪 4 个技能，一局里只有换精灵时才变。
#
# 所以把「查数据库」和「按帧判断」拆开：前者按 my_name 缓存，后者保持纯计算。
# 换队/改队伍配置最多 30s 内生效（_META_TTL），对实战足够。

_TEAM_TTL = 15.0
_team_cache = {"at": 0.0, "team": None}

_META_TTL = 30.0
_meta_cache = {}


def _team_snapshot():
    """team_db 的队伍配置，15s 内复用。"""
    now = time.time()
    c = _team_cache
    if c["team"] is not None and now - c["at"] < _TEAM_TTL:
        return c["team"]
    try:
        import team_db
        team = {t.get("name"): t for t in team_db.dump_team() if t.get("name")}
    except Exception:
        team = {}
    c["at"] = now
    c["team"] = team
    return team


def _skill_meta(my_name):
    """我方某只精灵的技能表：moves / 每个槽位的 power 与 name / 变化技能耗能集合。

    返回 None 表示查不到（调用方按"未知"处理）。结果按 my_name 缓存 30s。
    """
    if not my_name:
        return None
    now = time.time()
    hit = _meta_cache.get(my_name)
    if hit is not None and now - hit["at"] < _META_TTL:
        return hit
    meta = {"at": now, "moves": [], "powers": {}, "names": {},
            "dash_costs": None}
    team = _team_snapshot()
    meta["moves"] = list((team.get(my_name) or {}).get("moves") or [])
    try:
        import sqlite3
        from portrait_db import DB_PATH
        db = sqlite3.connect(DB_PATH)
        try:
            for i, sid in enumerate(meta["moves"][:4]):
                row = db.execute("SELECT power, name FROM skills WHERE id=?",
                                 (int(sid),)).fetchone()
                if row:
                    meta["powers"][i] = row[0] or 0
                    meta["names"][i] = row[1]
            if not meta["moves"]:
                # 队伍没配 -> 只知道这只精灵所有变化技能的耗能，用于路线 2
                row = db.execute(
                    "SELECT id FROM species WHERE name=? "
                    "ORDER BY implemented DESC, total DESC LIMIT 1",
                    (my_name,)).fetchone()
                if row:
                    costs = set()
                    for r in db.execute(
                            "SELECT DISTINCT s.cost FROM species_skills ss "
                            "JOIN skills s ON s.id=ss.skill_id "
                            "WHERE ss.species_id=? "
                            "AND (s.power IS NULL OR s.power<=0)", (row[0],)):
                        if r[0] is not None:
                            costs.add(int(r[0]))
                    meta["dash_costs"] = costs
        finally:
            db.close()
    except Exception:
        pass
    _meta_cache[my_name] = meta
    return meta


def _fix_status_kinds(slots, my_name):
    """用我方装备的技能表纠正 num/dash 误判（模块级辅助）。

    为什么需要
    ----------
    `_is_dash` 只认画面上是两个横杠的卡。但变化技能（力量增效/防御/
    剑舞…）在游戏里**卡面也画数字**，digitread 会把它读成伤害值 ——
    实测 2026-11-21 02:40 抓到 `i1 v=50 kind=num`，而那个槽位其实是
    「力量增效」（cost 1，无伤害）。

    数据来源的降级链
    ----------------
    1. team_db 里这只精灵的 moves（最准，但队伍配置常常不全）
    2. 如果 team_db 没有 -> 用**这只精灵所有可学技能**里 power<=0 的
       技能名集合，再跟卡面耗能对照（模糊但比不判好）

    实测教训：03:10 那局出战的是「音速犬」，但 team_db 只配了「恶魔狼」，
    所以第 1 条路查不到 -> kind 全是 None -> buff 分支仍然走不到。
    """
    if not slots or not my_name:
        return slots
    try:
        meta = _skill_meta(my_name)
        if meta is None:
            return slots
        moves = meta["moves"]
        if moves:
            # 路线 1：队伍配置了 moves，直接查 power（已缓存在 meta["powers"]）
            for slot in range(1, min(4, len(moves)) + 1):
                if (meta["powers"].get(slot - 1) or 0) <= 0:
                    s = slots[slot - 1]
                    if s is not None:
                        s["kind"] = "dash"
                        s["value"] = None
                        s["color"] = None
        else:
            # 路线 2：队伍没配 -> 用这只精灵的"变化技能耗能集合"推断
            dash_costs = meta.get("dash_costs")
            if not dash_costs:
                return slots
            # 卡面耗能如果唯一对应一个"变化技能耗能"，且该槽位读到的
            # 数值不合理（<=60，远低于同卡组其他伤害），标 dash
            for idx, s in enumerate(slots):
                if s is None or s.get("kind") != "num":
                    continue
                v = s.get("value")
                if v is not None and v <= 60:
                    # 小数值极可能是变化技能（伤害卡很少低于 60）
                    others = [x.get("value") for x in slots
                              if x is not None and x is not s
                              and x.get("value") is not None]
                    if others and max(others) >= 100:
                        s["kind"] = "dash"
                        s["value"] = None
                        s["color"] = None
    except Exception:
        pass
    return slots


def mark_status_slots(slots, costs, known_dash=None):
    """用"我方装备的技能表"纠正 num/dash 判定。

    为什么需要
    ----------
    `_is_dash` 只认画面上是两个横杠的卡。但变化技能（力量增效/防御/
    剑舞…）在游戏里**卡面也画数字**（显示的是耗能或层数），
    digitread 会把它读成伤害值 —— 实测 2026-11-21 02:40 抓到
    `i1 v=50 kind=num`，而那个槽位其实是「力量增效」（cost 1，无伤害）。

    这里用权威信息（team_db 里我方装备的技能 id -> 是否 power>0）
    直接覆盖 kind，不再依赖画面形状。

    costs     : {slot: cost}（my_card_costs 返回）
    known_dash: {slot: True} 明确是变化技能的槽位（可选）
    """
    if not slots:
        return slots
    if known_dash:
        for slot, is_dash in known_dash.items():
            idx = int(slot) - 1
            if 0 <= idx < len(slots) and slots[idx] and is_dash:
                slots[idx]["kind"] = "dash"
                slots[idx]["color"] = None
                slots[idx]["value"] = None
    return slots


def attach_skill_names(slots, my_name):
    """给每个槽位补上真实技能名与效果分类。

    为什么需要
    ----------
    机器人只读卡面伤害数字，不读技能名，所以下游拿到的是
    `kind="dash"` 这种"无伤害"标记，无法区分：
        借用（随机复制技能） vs 钧势（物防+140%）
    两者都是 power=0，但前者打出去纯亏一回合，后者值得优先叠。

    实测（2026-11-21）：实盘连续 7 局在 T1/T2 打 slot 4，
    而真实队伍所有 slot 4 都是「借用」/「取念」。

    这里从 team_db 的队伍配置（moves 的 id 顺序 = 卡片顺序）
    查真实技能名，再交给 skill_kind 分类，写入：
        s["skill_name"] = "借用"
        s["skill_kind"] = "copy"

    查不到就什么都不写（下游按 unknown 处理，不会误判成 buff）。
    """
    if not slots or not my_name:
        return slots
    try:
        import skill_kind as SK
        meta = _skill_meta(my_name)
        if meta is None:
            return slots
        moves = meta["moves"]
        if not moves:
            return slots
        for i, sid in enumerate(moves[:4]):
            if i >= len(slots) or slots[i] is None:
                continue
            name = meta["names"].get(i)
            if not name:
                continue
            slots[i]["skill_name"] = name
            slots[i]["skill_kind"] = SK.classify(name)
    except Exception:
        pass
    return slots


# ------------------------------------------------------------------ UI probes