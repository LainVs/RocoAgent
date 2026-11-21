# -*- coding: utf-8 -*-
"""UI 探测：按钮 / VS 页 / 结算页 / 换人面板

从 robot.py 拆出（第 1071-1307 行），纯搬移，逻辑未改。
"""
from __future__ import annotations

import numpy as np
import cv2

from config import (  # noqa: F401
    LOBBY_BTN, RESULT_BTN, ACTION_BTN_BAND, CONFIRM_BAND,
    RESULT_BAND, RESULT_LIST_BAND, SKILL_BOX, log,
)
from winio import Screen
from perceive import _is_skillbar


def has_start_button(screen):
    """Gold '开始挑战' blob in the bottom-right banner."""
    x, y, w, h = LOBBY_BTN
    crop = screen.grab_s((x, y, w, h))
    if crop is None:
        return False
    r = crop[:, :, 0].astype(np.int16)
    g = crop[:, :, 1].astype(np.int16)
    b = crop[:, :, 2].astype(np.int16)
    gold = (r > 205) & (g > 150) & (g < 215) & (b < 120) & (r - b > 110)
    # 门槛改成相对 band 面积的比例（2026-11-21）。
    # 原来写死 8000 像素，那是 520x150 的 band 下量的；band 改成
    # 420x140 后总像素从 78000 降到 58800，8000 占比从 10% 变成 13.6%，
    # 在 4:3 下按钮更小就过不了。
    ch, cw = crop.shape[:2]
    return int(gold.sum()) > int(cw * ch * 0.08)


def has_exit_button(screen):
    """White '退出' blob on the result screen."""
    x, y, w, h = RESULT_BTN
    crop = screen.grab_s((x, y, w, h))
    if crop is None:
        return False
    mx = crop.max(axis=2).astype(np.int16)
    mn = crop.min(axis=2).astype(np.int16)
    white = ((mx - mn) < 20) & (mx > 215)
    return int(white.sum()) > 4000


def has_vs_screen(screen):
    """VS/确认首发页：左青右橙的整屏分色。

    **这是唯一能阻止机器人在这个页面乱点的判据。**
    实测它一旦失效（颜色阈值卡太死），_idle_tick 会掉进
    advance splash 分支，隔次点 RESULT_SKIP_REL=(2303,238) ——
    那个位置正好是右上角的 X 关闭按钮，一点就把匹配取消了，
    然后退回大世界，再也回不来（用户 11-20 反馈的"点左上角/退出"）。
    """
    left = screen.grab_s((150, 250, 300, 300))
    right = screen.grab_s((1900, 250, 300, 300))
    if left is None or right is None:
        return False
    ml = left.reshape(-1, 3).mean(axis=0)
    mr = right.reshape(-1, 3).mean(axis=0)
    # 左：?蓝绿（g、b 明显高于 r。?
    teal = (ml[1] > ml[0] + 12) and (ml[2] > ml[0] + 18)
    # 右：橙（r 明显高于 b。?
    orange = (mr[0] > mr[2] + 35) and (mr[0] > 90)
    return bool(teal and orange)


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
    crop = screen.grab_s((x, y, w, h))
    if crop is None:
        return None
    r = crop[:, :, 0].astype(np.int16)
    g = crop[:, :, 1].astype(np.int16)
    b = crop[:, :, 2].astype(np.int16)
    warm = ((r > 215) & (g > 170) & (b > 70) & (b < 190))
    pale = np.minimum(np.minimum(r, g), b) > 200
    m = (warm | pale).astype(np.uint8)

    # 判据全部改成**相对 band 的比例**，不再用绝对像素（2026-11-21）
    # --------------------------------------------------------------
    # 原来写死 `170 <= bw <= 380` / `45 <= bh <= 130` / `abs(cxr-193) > 95`，
    # 这些是**基准 2560x1600 下**的量出来的数字。
    #
    # 换到 2048x1536（4:3）后按钮整体缩小，而且 band 位置也变了 ——
    # 实测 `mask 像素 = 1919`，远低于门槛 6000，`find_action_button`
    # 直接返回 None。调用方于是退回固定坐标 `LOBBY_BTN_REL`，
    # 而那个坐标在 4:3 下是错的（用户看到"点对战记录那里一直点上面"）。
    #
    # 改成比例判据后，任何分辨率下都按同一个相对尺度找按钮。
    ch, cw = crop.shape[:2]
    min_area = int(cw * ch * 0.05)          # 按钮占 band 5% 以上
    if int(m.sum()) < min_area:
        return None
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    best = None
    for i in range(1, n):
        bx, by, bw, bh, area = stats[i]
        if not (0.35 * cw <= bw <= 0.95 * cw):
            continue
        if not (0.22 * ch <= bh <= 0.95 * ch):
            continue
        if area < min_area:
            continue
        if best is None or area > best[0]:
            best = (area, bx, by, bw, bh)
    if best is None:
        return None
    _, bx, by, bw, bh = best
    return screen.abs_point((x, y, w, h), bx + bw // 2, by + bh // 2)


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
    crop = screen.grab_s((x, y, w, h))
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
    return screen.abs_point((x, y, w, h), bx + bw // 2, by + bh // 2)


def find_result_button(screen):
    """结算页的「退出」按钮 -> 物理坐标，找不到返回 None。

    为什么要搜索而不是用固定坐标（2026-11-21 用户反馈）
    --------------------------------------------------
    用户换到 2048x1536（4:3）后，"点对战记录那里一直点上面，点错了"。
    原来用 `RESULT_BTN_REL=(1293,1530)` 固定坐标，按 0.96 高度缩放后
    落在按钮上方的"对战记录"标签上。

    判据：白色/近白的大圆角按钮，尺寸占 band 的 30%~85% 宽、20%~90% 高。
    和 `find_action_button` 用同一套相对比例，任何分辨率都适用。
    """
    x, y, w, h = RESULT_BTN
    crop = screen.grab_s((x, y, w, h))
    if crop is None or crop.size == 0:
        return None
    mx = crop.max(axis=2).astype(np.int16)
    mn = crop.min(axis=2).astype(np.int16)
    white = ((mx - mn) < 25) & (mx > 205)
    m = white.astype(np.uint8)
    ch, cw = crop.shape[:2]
    min_area = int(cw * ch * 0.04)
    if int(m.sum()) < min_area:
        return None
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    best = None
    for i in range(1, n):
        bx, by, bw, bh, area = stats[i]
        if not (0.30 * cw <= bw <= 0.95 * cw):
            continue
        if not (0.20 * ch <= bh <= 0.95 * ch):
            continue
        if area < min_area:
            continue
        if best is None or area > best[0]:
            best = (area, bx, by, bw, bh)
    if best is None:
        return None
    _, bx, by, bw, bh = best
    return screen.abs_point((x, y, w, h), bx + bw // 2, by + bh // 2)


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
    crop = screen.grab_s((x, y, w, h))
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
    crop = screen.grab_s((X0, Y0, W, H))
    if crop is None:
        return []
    # The skill cards' white name plates live in the same area and match the
    # same "light rounded rect" test. If the skill bar is on screen this is a
    # normal battle, not the switch panel.
    if bar_visible is None:
        x0, y0, x1, y1 = SKILL_BOX
        bar = screen.grab_s((x0, y0, x1 - x0, y1 - y0))
        if bar is not None:
            bar_visible, _ = _is_skillbar(bar, (0, 0, x1 - x0, y1 - y0))
        else:
            bar_visible = False
    if bar_visible:
        return []
    mx = crop.max(axis=2).astype(np.int16)
    mn = crop.min(axis=2).astype(np.int16)
    # 实测换人面板的卡片是"半透明深底 + 白字 + 白色描边"，
    # 不是纯白块。原来的 (mx-mn)<30 & mx>200 会把整张卡判掉，
    # 结果面板明明开着 find_switch_rows 却返回 0（机器人卡在面板上）。
    # 改成"有足够多偏亮像素"就算一张卡。
    light = ((mn > 150) & ((mx - mn) < 90)).astype(np.uint8)
    if int(light.sum()) < 800:
        # 再退一步：只要这块区域里有明显的白色文字就认
        light = (mx > 205).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(light, 8)
    plates = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        # 宽度上限从 200 放宽到 340（实测卡片更宽），高度也放宽
        if area < 1500 or not (110 <= w <= 340) or not (30 <= h <= 140):
            continue
        plates.append((y + h // 2, x + w // 2))
    if len(plates) < 3:
        return []
    plates.sort()
    gaps = sorted(b - a for (a, _), (b, _) in zip(plates, plates[1:]) if b - a > 60)
    if not gaps:
        return []
    pitch = gaps[len(gaps) // 2]
    if not (80 <= pitch <= 240):
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
#   奶龙     1 泥浆铠甲(-) 2 过充?num) 3 电磁偏转(-) 4 休息回复(-)  -> heal slot 4
# So a card set whose dash slots are (1,3,4) is 奶龙 and slot 4 is the heal.
HEAL_BY_DASH = {(1, 3, 4): 4}

