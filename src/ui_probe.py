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
from perceive import _is_skillbar, countdown_visible_crop, CNT_ROI


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


# -------------------------------------------------------------- switch panel
# 强制换人面板（队伍列表）：5 张卡纵向斜列在屏幕左侧。
#
# ★ 判据演化（2026-11-22，逐帧标定后的最终方案）
# ------------------------------------------------------------
# 之前五代判据全部失败，每次失败都留了证据，按顺序是：
#   1. 「浅色矩形板」：卡片是「头像 + 名牌」的组合，没有一块大浅色矩形
#   2. 「高饱和头像」：背景的红色城堡 / 旗帜同样高饱和，误检横向分散点
#   3. 「编号数字 1~5」：白字太细，连通域只有 380px，不稳定
#   4. 「暗色名牌 + 血条」：背景本身很暗，连通域粘成 480x230 的大块
#   5. 「奶白编号药丸」：能稳定给出 5 个，但药丸和血条是**斜向错开**的，
#      由药丸反推血条位置误差 ±20px，会把活着的卡读成 0%（选错精灵的根源）
#
# 现在锚在名牌里的**金星（★N）**上。用两帧不同状态的真实截图标定
# （switch_now.png 与 battle_now.png，两帧的队伍列表整体上下相差 40px，
#  说明这个判据不依赖固定 y）：
#
#     金星 15x12、面积 ~110、颜色 R>200 G>148 B<115 且 R-B>105
#     同一张卡内金星与血条的相对位置固定：
#         血条 y 带 ≈ 金星 y - 16 .. y + 14
#         血条 x   ≈ 金星 x + 29 .. x + 119
#
# 所以行目标取 (金星x - 16, 金星y)：
#   · 这是名牌左边缘，点它就是点这张卡（实测高亮框覆盖整个「头像+名牌」）
#   · robot._living_rows 的读数带 (tx+6, ty-16, 175, 30) 正好落在血条上，
#     不需要改 robot 侧的任何一个偏移量
#
# 实测读数（switch_now.png，客户区坐标；血条带内「高对比亮像素」占比）：
#     card1 金星(384, 603) 空血  0.03
#     card2 金星(338, 729) 空血  0.03
#     card3 金星(343, 863) 有血  0.18
#     card4 金星(397, 990) 有血  0.14
#     card5 金星(496,1097) 有血  0.43
# 与截图上「前两张黑条、后三张绿条」完全一致；robot 侧的 0.06 阈值
# 正好落在 0.03 与 0.14 之间，不需要动。
#
# 为什么不用「药丸」当锚点：药丸在卡片的左外侧，选中卡（会放大右移）
# 时相对位移达 +30px；金星长在名牌上，跟着名牌走，没有这个问题。
PARTY_BOX = (250, 540, 660, 780)   # 窗口坐标（= 客户区 + BASE_OFF 13,58）

# 金星的形状约束（实测 14~16 x 12~13，面积 102~140；放宽一倍留余量，
# 但必须窄到能把「选中卡的橙色高光」（154x37、2000+ px）排除掉）
STAR_W = (12, 20)
STAR_H = (8, 18)
STAR_AREA = (70, 300)
STAR_FILL = 0.35
# 相邻两张卡的最小 y 间距（实测 111~150）。小于它的两个"星"视为
# 同一张卡的重复检出（描边、高光），只保留先出现的那个。
ROW_MIN_PITCH = 70


def _star_points(crop):
    """找名牌里的金星，返回 [(x_center, y_center), ...]（crop 局部，y 升序）。

    crop 是 grab_s 的返回值，**RGB** 通道序（GrabRect._fit 之后仍是 RGB）。
    """
    r = crop[:, :, 0].astype(np.int16)
    g = crop[:, :, 1].astype(np.int16)
    b = crop[:, :, 2].astype(np.int16)
    gold = ((r > 200) & (g > 148) & (b < 115) & (r - b > 105)).astype(np.uint8)
    gold = cv2.morphologyEx(gold, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(gold, 8)
    pts = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if not (STAR_W[0] <= w <= STAR_W[1]):
            continue
        if not (STAR_H[0] <= h <= STAR_H[1]):
            continue
        if not (STAR_AREA[0] <= area <= STAR_AREA[1]):
            continue
        if area / float(w * h) < STAR_FILL:
            continue
        pts.append((x + w // 2, y + h // 2))
    pts.sort(key=lambda p: p[1])
    return pts


def find_switch_rows(screen, bar_visible=None, countdown=None):
    """Detect the forced-switch party list (cards down the left edge).

    Returns a list of window-coordinate (x, y) click targets, top row first;
    each target is the row's name-plate left edge, centred on the HP bar, so
    `robot.Bot._living_rows` can read the bar straight out of it.

    `bar_visible` lets the caller pass in an already-computed skill-bar result;
    recomputing it costs ~50 ms and _battle_tick already knows the answer.

    `countdown` is the real discriminator, and it is **not** optional in
    practice — see the long note above PARTY_BOX for how it was found:

        None  = 自己检测（约 1 ms）。可见倒计时 -> 这不是强制换人面板。
        False = 调用方已知"没有倒计时"，跳过检测。
                （主动换人：按 E 打开列表时回合倒计时还在，不能因此否决）

    为什么必须区分：正常回合也会把队伍列表画出来（实测 live5.png：
    底部是「逃跑/背包/替换/技能」操作条，左边队伍列表带技能名和伤害数），
    如果只判"列表在"，就会把正常回合当成强制换人，反复点卡片白白丢回合。
    """
    # The skill cards' white name plates live in the same area. If the skill bar
    # is on screen this is a normal battle decision window, not the panel.
    if bar_visible is None:
        x0, y0, x1, y1 = SKILL_BOX
        bar = screen.grab_s((x0, y0, x1 - x0, y1 - y0))
        if bar is not None:
            bar_visible, _ = _is_skillbar(bar, (0, 0, x1 - x0, y1 - y0))
        else:
            bar_visible = False
    if bar_visible:
        return []
    # A visible turn countdown means the game is still taking our decision, so
    # the list below is the plain party list, not the forced-replacement panel.
    # Measured: forced panel -> no countdown (switch_now / battle_now),
    # normal turn with the same list on screen -> countdown (live5 / live7).
    if countdown is None:
        cnt = screen.grab_s(CNT_ROI)
        countdown = bool(cnt is not None and countdown_visible_crop(cnt))
    if countdown:
        return []
    crop = screen.grab_s(PARTY_BOX)
    if crop is None or crop.size == 0:
        return []
    pts = _star_points(crop)
    if len(pts) < 3:
        return []
    rows = []
    last_y = None
    for cx, cy in pts:
        if last_y is not None and cy - last_y < ROW_MIN_PITCH:
            continue
        rows.append((int(PARTY_BOX[0] + cx - 16), int(PARTY_BOX[1] + cy)))
        last_y = cy
    return rows[:6]

# ------------------------------------------------------------------ decision
HEAL_HINTS = ("休息回复", "回复", "治疗")

# We cannot OCR the Chinese skill names quickly, but each pet's non-damaging
# ("dash") slot pattern is distinctive. Team sheet:
#   奶龙     1 泥浆铠甲(-) 2 过充?num) 3 电磁偏转(-) 4 休息回复(-)  -> heal slot 4
# So a card set whose dash slots are (1,3,4) is 奶龙 and slot 4 is the heal.
HEAL_BY_DASH = {(1, 3, 4): 4}

