# -*- coding: utf-8 -*-
"""skill_names.py —— 从卡面读技能名，用来校验队伍配置。

为什么需要
----------
`perceive.attach_skill_names()` 现在的做法是：从 `my_team` 表按**槽位顺序**
猜技能名。也就是说，它完全信任队伍配置。

踩过的坑（docs/PLAN_SKILL_KIND.md）：配置过期是最隐蔽的 bug —— 配置里写
「借用」，实际卡面是「火云车」，于是 power=0 的判定作用在错误的槽位上，
表现为"策略选了低伤害技能"，很容易误判成策略 bug。

而卡面上**印着名字**（吞噬 / 火焰切割 / 蝙蝠 / 惊吓盒子），所以这件事
可以根治：读卡面，跟配置对账。

为什么不是"直接 OCR 出名字"
---------------------------
实测 easyocr 在这套小尺寸艺术字上**不稳定**：同一张图换缩放倍数，
有时读得出有时读不出（4 个槽位里稳定只有 1~2 个）。纯 OCR 不可靠。

但这里有个关键约束让问题变简单：**每个槽位的候选只有 4 个** ——
就是我方这只精灵配的 4 个技能。于是不需要"认出整串字"，只需要
「在这 4 个候选里挑最像的那个」。实测：

    OCR='惊吓盒'  -> 候选 [吞噬, 火焰切割, 蝙蝠, 惊吓盒子] -> 惊吓盒子  ✓
    OCR='借'      -> 候选 [借用, 火云车, 诋毁, 闪光冲击]   -> 借用      ✓

这是**受限词表纠错**，不是开放式 OCR。要求低得多，也稳得多。

用法
----
    python src/skill_names.py --check         # 读一次，跟配置对账
    python src/skill_names.py --calib 20      # 标定名字条位置
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, r"D:\Tools")

# 名字条窗口相对伤害胶囊的偏移（标定值，见 --calib）
#
# 实测 4 个槽位（logs/bar_partial_093057.png，2560x1600）：
#   胶囊 (x0,y0,x1,y1)      名字条实际 (x0,y0,x1,y1)
#   245,226,312,254         183,252,324,292
#   201,405,267,437         139,435,281,480
#   229,587,291,622         167,620,311,673
# 规律：左边 ≈ 胶囊左边 - 62，上边 ≈ 胶囊底边 + 8，宽 150，高 56。
# 卡面倾斜会让它上下浮动 ±12px，所以窗口给足。
PLATE_DX = -62
PLATE_DY = 8
PLATE_W = 150
PLATE_H = 56


def plate_window(box, img_w, img_h):
    """伤害胶囊 -> 名字条搜索窗口。"""
    x0, _y0, _x1, y1 = [int(v) for v in box]
    wx0 = max(0, x0 + PLATE_DX)
    wy0 = max(0, y1 + PLATE_DY)
    return (wx0, wy0,
            min(img_w, wx0 + PLATE_W),
            min(img_h, wy0 + PLATE_H))


def tight_text(sub):
    """在窗口里收紧到**名字条上的文字**。返回 (x,y,x1,y1) 或 None。

    踩过的坑（两版）
    ----------------
    v1：用"深色像素"当文字掩码。
        失败 —— 卡面背景本身就是暗的，整窗都是"深色"，收紧完全失效
        （实测 rowsum 最大值 = 窗口全宽，包围盒永远是整个窗口）。

    v2（当前）：先找**米白底**（名字条底板），再在底板里找深色字。
        底板是关键中间层：卡面图案再暗，也不是米白；而名字条底板
        不管倾斜多少度都是米白。
    """
    import cv2
    import numpy as np
    if sub is None or sub.size == 0:
        return None
    b = sub[:, :, 0].astype(np.int16)
    g = sub[:, :, 1].astype(np.int16)
    r = sub[:, :, 2].astype(np.int16)
    # 米白底：三通道都高、且彼此接近（排除彩色卡面）
    pale = ((r > 190) & (g > 180) & (b > 160)
            & (np.abs(r - g) < 55) & (np.abs(g - b) < 65)).astype(np.uint8)
    pale = cv2.morphologyEx(pale, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(pale, 8)
    if n <= 1:
        return None
    # 取最大的那块当底板
    j = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    px, py, pw, ph, parea = stats[j]
    if pw < 40 or ph < 12 or parea < 0.25 * pw * ph:
        return None
    plate = sub[py:py + ph, px:px + pw]
    # 底板里找深色字
    mx = plate.max(axis=2).astype(np.int16)
    mn = plate.min(axis=2).astype(np.int16)
    dark = ((mx < 160) & ((mx - mn) < 90)).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    ys, xs = np.where(dark > 0)
    if len(xs) < 40:
        return None
    # 按列投影切出**连续的文字带**，丢掉左侧的图标/圆点。
    #
    # 实测 slot3（蝙蝠）：底板里左边有粉色图标，直接取包围盒会把图标
    # 一起框进去，文字只占 40% 宽度，RapidOCR 直接放弃（返回空）。
    # 按列投影找"有墨迹的列"的最大连续段，就能把图标排除掉。
    colsum = dark.sum(axis=0)
    occupied = colsum >= 1
    # 允许 3 列以内的空隙（字与字之间的间距）
    gap = 0
    best_a = best_b = -1
    a = -1
    for x in range(len(occupied)):
        if occupied[x]:
            if a < 0:
                a = x
            gap = 0
            b = x
            if (b - a) > (best_b - best_a):
                best_a, best_b = a, b
        else:
            gap += 1
            if gap > 3 and a >= 0:
                a = -1
    if best_a < 0 or (best_b - best_a) < 10:
        # 投影失败就退回整体包围盒
        best_a, best_b = int(xs.min()), int(xs.max())
    sel = dark[:, best_a:best_b + 1]
    sy = np.where(sel.sum(axis=1) >= 1)[0]
    if len(sy) < 5:
        return None
    return (int(px + best_a), int(py + sy.min()),
            int(px + best_b) + 1, int(py + sy.max()) + 1)


def match_candidate(ocr_text, candidates):
    """在候选里挑最像的。返回 (名字, 分数) 或 (None, 0)。

    打分规则（从强到弱）：
        完全相等                    1.00
        候选以 OCR 开头（OCR 短了） 0.85   "惊吓盒" -> "惊吓盒子"
        OCR 以候选开头（OCR 长了）  0.85   "火焰切割啊" -> "火焰切割"
        公共前缀 >= 2 字            0.60
        公共前缀 1 字               0.30   只在候选里唯一时采用
    分数 < 0.55 视为没读出来 —— 宁可留空，也不要写错。

    踩过的坑：一开始写 `c.startswith(t) or t.startswith(c)` 不设下限，
    单字候选（技能库里有 1 个单字技能）会让 "火焰切割" 匹配到某个
    单字技能，并且返回那个单字。必须要求**最短长度 >= 2** 才走前缀分支。
    """
    t = (ocr_text or "").strip()
    if not t or not candidates:
        return None, 0.0
    best, best_s = None, 0.0
    for c in candidates:
        if not c:
            continue
        if t == c:
            s = 1.0
        elif len(t) >= 2 and len(c) >= 2 and (
                c.startswith(t) or t.startswith(c)):
            # 长的那边至少要多出 1 个字，否则就是完全相等（上面已处理）
            s = 0.85 if len(c) != len(t) else 1.0
        else:
            n = 0
            for a, b in zip(t, c):
                if a != b:
                    break
                n += 1
            s = 0.60 if n >= 2 else (0.30 if n == 1 else 0.0)
        if s > best_s:
            best, best_s = c, s
    # 首字相同但其余不同：候选里只有一个首字匹配时也可以采用
    if best_s < 0.55 and t:
        same_first = [c for c in candidates if c and c[0] == t[0]]
        if len(same_first) == 1:
            return same_first[0], 0.55
    return (best, best_s) if best_s >= 0.55 else (None, best_s)


def read_names(screen, slots, candidates_by_slot=None, scales=(1.0, 1.5, 2.0)):
    """读四个槽位的技能名。

    slots              : perceive._slots() 的结果（4 个胶囊或 None）
    candidates_by_slot : {slot_index: [候选名...]}，来自队伍配置。
                         给了就走受限词表匹配（推荐）。
    scales             : OCR 要试的放大倍数。

    ⚠ 现实预期（2026-11-21 实测，务必先看）
    ----------------------------------------
    RapidOCR 对这套小尺寸艺术字的**中文**识别能力有限：

        logs/bar_partial_093057.png（4 张卡，肉眼都很清楚）
          吞噬      ✓ 1.00
          火焰切割   ✓ 1.00
          蝙蝠      ✗ 空（任何缩放 / 旋转 / 二值化都读不出）
          惊吓盒(子) 0.85（少一个字，靠候选纠错补回来）

    对照组：用 cv2 字体渲染同样几个词（黑字白底、1.8 倍字号）也读成
    '????' 或空 —— 所以**不是裁剪或预处理的问题**，是识别模型本身对
    这些字的覆盖不足。

    所以这个函数的定位是**辅助校验**，不是权威来源：
      · 读到高分（>=0.85）时，可以用来发现"配置和卡面不一致"
      · 读不出时不报错、不阻断，返回 None
      · **绝不**用 OCR 结果去覆盖队伍配置

    单次 OCR 约 0.4s（0.4s × 最多 4 槽 × 最多 3 次），所以只适合
    放在后台线程 / 开局时跑一次，不能进主循环。
    """
    import cv2
    import ocr_cn

    ocr = ocr_cn.Ocr()
    try:
        full = screen.grab_full()
    except Exception:
        full = None
    if full is None:
        return [None] * len(slots)

    h, w = full.shape[:2]
    out = []
    for i, s in enumerate(slots):
        if not s:
            out.append(None)
            continue
        wx0, wy0, wx1, wy1 = plate_window(s["box"], w, h)
        win = full[wy0:wy1, wx0:wx1]
        t = tight_text(win)
        if not t:
            out.append({"raw": None, "name": None, "score": 0.0,
                        "why": "no text box"})
            continue
        tx0, ty0, tx1, ty1 = t
        pad = 14
        crop = full[max(0, wy0 + ty0 - pad):wy0 + ty1 + pad,
                    max(0, wx0 + tx0 - pad):wx0 + tx1 + pad]
        if crop.size == 0:
            out.append({"raw": None, "name": None, "score": 0.0,
                        "why": "empty crop"})
            continue

        cands = (candidates_by_slot or {}).get(i + 1)
        # 归一化：允许传单个字符串或列表。字符串可迭代，直接当列表用
        # 会把 "火云车" 拆成三个单字候选。
        if isinstance(cands, str):
            cands = [cands]

        best = {"raw": None, "name": None, "score": 0.0}
        tried = []
        for sc in scales:
            big = (crop if abs(sc - 1.0) < 1e-6 else
                   cv2.resize(crop, None, fx=sc, fy=sc,
                              interpolation=cv2.INTER_CUBIC))
            try:
                res = ocr.read_region(big[:, :, ::-1])
            except Exception:
                continue
            raw = " ".join(x for x, _c in res).replace(" ", "")
            tried.append(raw)
            if not raw:
                continue
            if cands:
                name, score = match_candidate(raw, cands)
            else:
                name, score = (raw or None), (1.0 if raw else 0.0)
            if score > best["score"]:
                best = {"raw": raw, "name": name, "score": round(score, 2)}
            if best["score"] >= 0.85:
                break                   # 够好了，不用再试
        if best["raw"] is None:
            best["why"] = "ocr empty (tried %d)" % len(tried)
        out.append(best)
    return out


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="读一次卡面，跟队伍配置对账")
    ap.add_argument("--frame", default="",
                    help="用一张存档的技能栏截图代替实时抓屏")
    args = ap.parse_args()

    import cv2
    import digitread as dr
    import perceive as P

    if args.frame:
        import imgio
        img = imgio.read(args.frame)
        if img is None:
            print("读不到截图：%s" % args.frame)
            return 1
        rgb = img[:, :, ::-1]
        h, w = rgb.shape[:2]
        slots = P._slots(rgb, (0, 0, w, h), dr.find_pills(rgb, (0, 0, w, h)))

        class _F:
            def grab_full(self):
                return rgb
        screen = _F()
    else:
        import winio
        screen = winio.Screen()
        st = P.Perc(screen).observe()
        slots = st["skills"]

    # 候选：默认取「恶魔狼」的配置（那是测试帧里出场的精灵）
    cands = {}
    try:
        import sqlite3
        import team_db
        from portrait_db import DB_PATH
        db = sqlite3.connect(DB_PATH)
        team = {t.get("name"): t for t in team_db.dump_team() if t.get("name")}
        for nm, t in team.items():
            names = []
            for sid in (t.get("moves") or [])[:4]:
                row = db.execute("SELECT name FROM skills WHERE id=?",
                                 (int(sid),)).fetchone()
                names.append(row[0] if row else None)
            if nm == "恶魔狼":
                cands = {i + 1: n for i, n in enumerate(names) if n}
        db.close()
    except Exception as e:
        print("读队伍配置失败：%s" % e)

    print("候选（恶魔狼）：%s" % cands)
    got = read_names(screen, slots, cands)
    for i, r in enumerate(got):
        if r is None:
            print("  slot %d  ——" % (i + 1))
            continue
        print("  slot %d  OCR=%-10r -> %-8s (%.2f) %s"
              % (i + 1, r.get("raw"), r.get("name"), r.get("score", 0),
                 r.get("why") or ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
