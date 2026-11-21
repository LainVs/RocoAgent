# -*- coding: utf-8 -*-
"""历史战绩：游戏自己记的累计胜负，是唯一不依赖推断的胜负来源。

面板位置：大厅左侧那个带"L"角标的圆形图标（客户区 191,943），
点开是一张"历史战绩"卡片，中间一行：

    胜利 89  失败 76

只要在每场前后各读一次，比较这两个数字：

    胜利 +1 -> 这场赢了
    失败 +1 -> 这场输了
   都没变 -> 这场没打完 / 读错了

比"星数差""段位名"都可靠：星级会一次加 1 也会加 2、升段还会清零，
而这组计数是单调累加的。
"""
from __future__ import annotations

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "logs", "record_state.json")
# 逐场快照（append-only）就放在 STATE 旁边，文件名固定为
# record_snapshots.jsonl。不在这里定常量：测试会把 STATE 指到临时目录，
# 快照必须跟着走（见 save_state）。

OPEN_CLICK = (200, 1042)         # 客户区：历史战绩图标中心（实测 4:3 下）
CLOSE_CLICK = (2012, 422)        # 客户区：面板右上角 X
RECORD_BOX = (1040, 525, 460, 70)  # 客户区："胜利 N 失败 M" 那一行（实测）


def _panel_open(img):
    """面板打开时那一行是白底黑字；没打开就是场景画面。"""
    if img is None or img.size == 0:
        return False
    mn = img.min(axis=2)
    return float((mn > 200).mean()) > 0.35


def _parse_record_text(text, lines=None):
    """从 OCR 结果里解析 (wins, losses)。

    为什么要专门写一个（2026-11-21 实测）
    ----------------------------------
    OCR 返回的**顺序不固定**。实测同一帧两次读取：

        "胜利 140 失败 161"      <- 正常的从左到右
        "胜利 失败 140 161"      <- 文字先出，数字后出

    原来用 `re.search(r"(\\d+)\\D{0,6}?(\\d+)", text)` 取"前两个数字"。
    第二种顺序下会算错：`_num_after("失败")` 会先撞到 "140"（它排在
    "失败"后面），于是得到 (140, 140)。

    现在的做法：
      1. 找到"胜利"和"失败"两个关键词的**位置**
      2. 按位置先后配对：靠前的关键词配第一个数字，靠后的配第二个
      3. 关键词不全时退回"取前两个数字"
    """
    if not text:
        return None

    nums = re.findall(r"\d{1,4}", text)
    if not nums:
        return None

    pos_w = text.find("胜利")
    pos_l = text.find("失败")

    def _nearest_after(kw_pos):
        """关键词**之后最近**的数字（跳过"胜利 失败 140 161"这种排列）。"""
        m = re.search(r"(\d{1,4})", text[kw_pos:])
        return int(m.group(1)) if m else None

    def _nearest_before(kw_pos):
        m = None
        for m2 in re.finditer(r"(\d{1,4})", text[:kw_pos]):
            m = m2
        return int(m.group(1)) if m else None

    # ---- 按关键词就近取数（2026-11-22 实测修正）
    #
    # 旧逻辑："靠前的关键词配第一个数字、靠后的配第二个"。
    # 但 OCR 会多读出数字（段位、时间、分隔符里的数字），实测抓到：
    #
    #     before=143 after=168 / before_losses=168 after_losses=14
    #     before=14  after=144 / before_losses=169 after_losses=171
    #
    # 胜场和败场的数字明显串了 —— 战绩基线和归因全被污染。
    #
    # 现在改成：每个关键词取**它自己最近**的数字。这样多出来的数字
    # （在关键词之外的）不会被误当成绩。
    if pos_w >= 0 and pos_l >= 0:
        w = _nearest_after(pos_w)
        l_ = _nearest_after(pos_l)
        if w is not None and l_ is not None and w != l_:
            return (w, l_)
        # 两个关键词后面都取不到（或取到同一个数）：
        # 试"关键词前最近"，再退回旧的位置配对。
        w2 = _nearest_before(pos_w)
        l2 = _nearest_before(pos_l)
        if w2 is not None and l2 is not None and w2 != l2:
            return (w2, l2)
        if len(nums) >= 2:
            if pos_w < pos_l:
                return (int(nums[0]), int(nums[1]))
            return (int(nums[1]), int(nums[0]))
        return None

    if pos_w >= 0:
        # 只有"胜利"：它后面 8 字符内的数字算胜场
        m = re.search(r"胜利\D{0,8}(\d{1,4})", text)
        if m:
            w = int(m.group(1))
            rest = [int(x) for x in nums if int(x) != w]
            return (w, rest[0] if rest else None)
        return None

    if pos_l >= 0:
        m = re.search(r"失败\D{0,8}(\d{1,4})", text)
        if m:
            l_ = int(m.group(1))
            rest = [int(x) for x in nums if int(x) != l_]
            return (rest[0] if rest else None, l_)
        return None

    if len(nums) >= 2:
        return (int(nums[0]), int(nums[1]))
    return None


def _grab_box_keep_ar(screen, box):
    """按当前分辨率抓一块，并**保持宽高比**缩回基准尺寸。

    为什么不能直接用 screen.grab_client（2026-11-21 实测）
    ----------------------------------------------------
    `grab_client` 内部是"按 sx/sy 分别缩放裁剪，再 `_fit` 缩回基准尺寸"，
    两步都是**非等比**的。当客户区宽高比和基准（2560x1600 = 1.6）不同时，
    文字会被横向压扁或拉长：

        基准  RECORD_BOX 宽高比 6.57
        2048x1536 (1.333) -> 5.49   横向压缩 16%
        1920x1080 (1.778) -> 7.34   横向拉伸 12%

    OCR 对文字变形很敏感（实测变形后"失败 161"被读成"失败 16"）。

    这里改成：先按 sx 等比缩放取框（只用 sx，不用 sy），
    再缩回基准尺寸 —— 这样文字比例始终不变。
    代价是纵向位置要用 sy 单独算（框的中心点位置仍按 sy 走）。
    """
    import cv2
    cw, ch = getattr(screen, "client_size", (0, 0))
    if not cw or not ch:
        return screen.grab_client(box)
    sx, sy = screen.scale()
    x, y, w, h = box
    # 位置按各自轴的缩放走（这是对的：HUD 是贴边锚定的）
    px = int(round(x * sx))
    py = int(round(y * sy))
    # 尺寸只按 sx 缩放，保持文字比例
    pw = max(1, int(round(w * sx)))
    ph = max(1, int(round(h * sx)))
    img = screen._grab_at_client((px, py, pw, ph))
    if img is None or img.size == 0:
        return None
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)


def read(screen, click, log=None):
    """点开面板读 (wins, losses)，读完关掉。失败返回 None。

    click 是 (x, y) -> bool 的硬件点击（必须走开发板，系统鼠标游戏不认）。
    不做"面板是否已经打开"的判断：那个探针在面板动画/半透明状态下会误判，
    实测导致直接返回 None。这里改成"点了就读，读不到再点一次"。
    """
    import cv2
    import ocr_cn

    got = None
    # 坐标是客户区基准分辨率，必须按当前缩放换算，否则换分辨率就点空
    px, py = screen.phys_client(*OPEN_CLICK)
    cpx, cpy = screen.phys_client(*CLOSE_CLICK)
    try:
        for attempt in range(3):
            click(px, py)
            # 面板有入场动画：实测 0.35s×4 常常还是空白（日志里大量
            # "record ocr[1]: " 空字符串）。这里改成"最多等 2.5 秒、
            # 一看到白底就多等 0.4 秒让数字画完再读"。
            img = None
            deadline = time.time() + 2.5
            while time.time() < deadline:
                time.sleep(0.25)
                img = _grab_box_keep_ar(screen, RECORD_BOX)
                if _panel_open(img):
                    # 白底出现了，但数字可能还没画完 —— 再给它一点时间
                    time.sleep(0.4)
                    img = _grab_box_keep_ar(screen, RECORD_BOX)
                    break
            if img is None or img.size == 0:
                continue
            big = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            lines = ocr_cn.Ocr().read_region(big)
            text = " ".join(t for t, _c in lines)
            if log:
                log("record ocr[%d]: %s" % (attempt + 1, text.strip()[:40]))
            got = _parse_record_text(text, lines)
            if got:
                break
            # 读不到就把画面存下来，第二天能看出是没打开还是读错区域
            try:
                cv2.imwrite(os.path.join(ROOT, "logs",
                                         "record_fail_%s.png"
                                         % time.strftime("%H%M%S")),
                            img[:, :, ::-1])
            except Exception:
                pass
    finally:
        click(cpx, cpy)
    return got


def load_state():
    """读上次的战绩基线，返回 (wins, losses) 或 None。

    踩过的坑（2026-11-21 09:30，用户报"对局记录没有输赢"）
    --------------------------------------------------
    原来这里是：

        return json.load(f)          # 返回 {"wins":113,"losses":106,"at":...}

    但 diff() 期望的是 tuple：

        if after[0] > before[0] ...  # <== KeyError: 0

    dict 没有整数键 0/1，所以每次调用 diff() 都抛 KeyError，
    被外层 except 吞掉，results.jsonl 永远写不进去（全是 flat）。

    日志里能看到 record_state.json 停在 2026-11-19（基线建立那天），
    之后再没更新过 —— 因为 save_state 只在 diff 成功后才调用。

    现在统一返回 tuple，与 save_state 的参数形式对齐。
    """
    import json
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        # 兼容两种格式：dict（现在）和 list（更早的版本）
        if isinstance(d, dict):
            w, l = d.get("wins"), d.get("losses")
        elif isinstance(d, (list, tuple)) and len(d) >= 2:
            w, l = d[0], d[1]
        else:
            return None
        if w is None or l is None:
            return None
        return (int(w), int(l))
    except Exception:
        return None


def save_state(wins, losses):
    """存基线 + 追加一行逐场快照。

    为什么两份（2026-11-21）：
    `record_state.json` 只有**当前值**，回答不了"今天打得怎么样"。
    快照是 append-only 的，每判完一场一行，UI 用它算今日/近 20 局增量。
    写失败不影响基线 —— 两件事互相独立。
    """
    import json
    prev = load_state()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump({"wins": wins, "losses": losses, "at": ts}, f)
    except OSError:
        pass
    try:
        dw = int(wins) - int(prev[0]) if prev else 0
        dl = int(losses) - int(prev[1]) if prev else 0
        # 快照跟着 STATE 走：测试会把 STATE 指到临时目录，
        # 如果这里写死仓库路径，跑一次测试就会往真实历史里塞假数据。
        snap = os.path.join(os.path.dirname(STATE), "record_snapshots.jsonl")
        with open(snap, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": ts, "wins": int(wins),
                                "losses": int(losses),
                                "dw": dw, "dl": dl},
                               ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError):
        pass


def diff(before, after):
    """(wins, losses) 两次读数 -> 'win' / 'loss' / None。

    严格条件：赢和输只有一项变化，另一项必须完全不变。

    为什么必须严格（2026-11-21）：一局只该改变一个数字。
    如果两个都变，说明跨了多局（机器人卡住没读）或者读数错乱，
    这种情况宁可不判（返回 None），也不能记错胜负 —— 错的战绩
    会污染胜率统计，比没有更糟。
    """
    if not before or not after:
        return None
    # 兼容 dict 传入（防止调用方直接传 load_state 的原始结果）
    if isinstance(before, dict):
        before = (before.get("wins"), before.get("losses"))
    if isinstance(after, dict):
        after = (after.get("wins"), after.get("losses"))
    if before[0] is None or before[1] is None:
        return None
    if after[0] is None or after[1] is None:
        return None
    if after[0] > before[0] and after[1] == before[1]:
        return "win"
    if after[1] > before[1] and after[0] == before[0]:
        return "loss"
    return None
