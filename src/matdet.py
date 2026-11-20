# -*- coding: utf-8 -*-
"""matdet.py —— 材质检测器（先认材质，再让元素自报位置）

为什么不是"按坐标切矩形去读"
----------------------------
旧方案（robot.py 的 MY_BAR / FOE_BAR / SKILL_BOX）把"UI 在哪"写成了绝对像素。
一旦窗口大小、分辨率、游戏版本变化，坐标全错，而且**错得沉默**：
读到墙、读到背景，也会返回一个数字。
`logs/probe/regions.png` 里 energy_pip / btn_row 两个框已经画在空地上，就是这个病。

本模块的出发点是对画面的观察：无论血条、卡面、按钮还是段位牌，
视觉上只有四种材质，它们的签名**与分辨率、位置、卡面美术无关**：

  ① 描边贴片 plate    半透明深色圆角块 + 1px 亮边 + 内部高饱和填充
  ② 白字黑边 label    白色笔画 + 深色描边（描边宽 ∝ 字高）
  ③ 白色贴纸 sticker  近白圆角长条 + 纯黑字，整块无渐变
  ④ 彩色圆徽 badge    高饱和小色块，拓扑 = 环 / 实心 / 带斜杠

因此检测器的原子是这四个函数，业务元素由它们组合推导：

    find_plates()  -> 血条底板、能量槽、伤害胶囊
    find_stickers()-> 技能名牌、换人面板行、段位名牌、"开始挑战"
    find_labels()  -> 名字、等级、412/412、倒计时、按钮文字
    find_badges()  -> 属性徽章、编号 1-4、禁用符号、心

分辨率无关的三条实现约定
------------------------
1. 判据全部用「本区域的分位数」，不用绝对阈值（旧代码用 S<40 & V>185 找白贴纸，
   在真实帧上一张都找不到，因为贴纸是暖白、且被黑字切成碎块）。
2. 尺寸约束全部写成「band 尺寸的比例」或「宽高比」，不写像素数。
3. 形态学核的大小由「本区域的特征尺度」推出（贴纸高、字高），不写死。

坐标约定
--------
img 是 BGR（cv2.imread 的输出）。返回的 box 都是 img 像素的 (x, y, w, h)。
"""
from __future__ import annotations

import numpy as np
import cv2

# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------


def band_box(img, band):
    """比例带 (x0, y0, x1, y1) ∈ [0,1] -> img 像素矩形 (x, y, w, h)。"""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = band
    bx = max(0, min(w - 1, int(round(x0 * w))))
    by = max(0, min(h - 1, int(round(y0 * h))))
    bw = max(1, min(w - bx, int(round((x1 - x0) * w))))
    bh = max(1, min(h - by, int(round((y1 - y0) * h))))
    return bx, by, bw, bh


def crop(img, box):
    x, y, w, h = box
    return img[y:y + h, x:x + w]


def _odd(v, lo=3):
    v = int(max(lo, v))
    return v if v % 2 else v + 1


def _runs(row, max_gap=3, min_len=8):
    """一维 0/1 数组 -> [(x0, x1_inclusive, length)]，允许小空隙。"""
    idx = np.flatnonzero(row)
    if idx.size == 0:
        return []
    out = []
    start = prev = int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i - prev <= max_gap:
            prev = i
            continue
        if prev - start + 1 >= min_len:
            out.append((start, prev, prev - start + 1))
        start = prev = i
    if prev - start + 1 >= min_len:
        out.append((start, prev, prev - start + 1))
    return out


def _components(mask, min_area=1):
    n, lab, st, cen = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = (int(st[i, 0]), int(st[i, 1]), int(st[i, 2]),
                            int(st[i, 3]), int(st[i, 4]))
        if area < min_area:
            continue
        out.append({"x": x, "y": y, "w": w, "h": h, "area": area})
    return out


# --------------------------------------------------------------------------
# ① 描边贴片 plate
# --------------------------------------------------------------------------

def _bar_masks(roi):
    """血条类贴片的两种像素：填充（高饱和亮色）+ 槽底（深灰低饱和）。

    两种都收，是因为满血条几乎全是填充、空血条几乎全是槽底；
    只收一种，另一种情况就会漏检（旧代码 0 血时读不到就是这个原因）。
    """
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1].astype(np.int16)
    v = hsv[:, :, 2].astype(np.int16)
    # 分位数自适应：这块区域里"最不饱和 / 最亮"的那部分就是槽底与填充
    s_lo = np.percentile(s, 35)
    fill = (s > max(60, s_lo + 25)) & (v > 95)
    slot = (v > 40) & (v < 150) & (s < max(55, s_lo))
    return fill, slot


def find_slabs(roi, h_ref, h_frac=(0.006, 0.034), min_w_frac=0.10,
               max_w_frac=0.80, min_len_frac=0.10, max_edges=160,
               max_covers=4):
    """用「上下边缘配对 + 极性相反」找 UI 板（血条底板 / 能量槽 / 横向贴片）。

    为什么不用颜色、也不只用边缘强度
    --------------------------------
    · "深色低饱和"会把建筑阴影一起吃进来（敌方那侧实测如此）；
    · 只用 |Sobel| 也不行：明亮天空、石阶纹理同样有强水平边；
    · 但**板的上边缘和下边缘梯度符号一定相反**（一块亮的板压在背景上，
      进板时亮度上升、出板时下降）。这条约束是材质级的不变量，
      和"板里有没有血"、"板后面是天空还是墙"都无关。

    所以判据是：
        1. 两条长直水平边，y 间距 ∈ [h_frac]·H
        2. x 交集 ≥ min_w_frac·band_w，宽高比 5..45
        3. 两条边的梯度符号相反
        4. 板内部比边上平（内部 |Sobel| 均值显著低于边缘）
    """
    if roi is None or roi.size == 0:
        return []
    h, w = roi.shape[:2]
    if h < 12 or w < 40:
        return []
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.abs(gy)
    thr = max(9.0, float(np.percentile(mag, 85)))

    min_len = int(w * min_len_frac)
    gap = max(4, int(w * 0.02))
    edges = []                     # (y, x0, x1, sign)
    for y in range(1, h - 1):
        row = mag[y] > thr
        for x0, x1, ln in _runs(row, max_gap=gap, min_len=min_len):
            seg = gy[y, x0:x1 + 1]
            sign = 1 if float(seg.mean()) >= 0 else -1
            edges.append((y, x0, x1, sign))
    if not edges:
        return []
    if len(edges) > max_edges:
        edges.sort(key=lambda e: -(e[2] - e[1]))
        edges = sorted(edges[:max_edges], key=lambda e: e[0])

    # ---- 合并同向、**同一行附近**的边（只是把 1~2px 厚的描边并成一条）
    # 收紧到 |Δy|<=1 且 x 必须有重叠：放宽会把"血条下沿"和"能量 pip 行上沿"
    # 粘成一条，产出 h=19 的假板（实测 144028 那帧就是这样读错的）。
    merged = []
    for (y, x0, x1, sign) in edges:
        for m in merged:
            ov = min(m[2], x1) - max(m[1], x0)
            if (abs(m[0] - y) <= 1 and m[3] == sign
                    and ov >= 0.70 * min(m[2] - m[1], x1 - x0)):
                m[1] = min(m[1], x0)
                m[2] = max(m[2], x1)
                m[0] = min(m[0], y)
                break
        else:
            merged.append([y, x0, x1, sign])
    edges = [tuple(m) for m in merged]
    edges.sort(key=lambda e: e[0])

    h_lo = max(3, int(round(h_frac[0] * h_ref)))
    h_hi = max(h_lo + 1, int(round(h_frac[1] * h_ref)))

    # 血条里那行字（数字）—— 底板**一定完整包住它**，且字高约占板高的 50~70%。
    # 内填充边（被文字切开的上下沿）比字矮、包不住；能量 pip 行在字的下方、
    # 也包不住；等级文字在板上方、不在板内。这是把"嵌套候选"区分开的决定性判据。

    _tm_cache = {"m": None}

    def _text_inside(ox0, ya, ow, dh):
        """候选框里是否"完整地"压着一行字（上下都有留白）。

        两个坑都踩过：
          · 用整带的**行投影**（不限制 x）→ 名字/天空的白像素让每一行都"有字"，
            留白判据永远失败（实测 36/36 真血条都判 False）；
          · 用 `V>190 & S<110` 当白字 → 亮天空/白云也算白。
        所以：用 `text_pixels`（带描边的字），并且**只在候选自己的矩形里**判。
        text_pixels 每个带只算一次（缓存），候选只做切片。
        """
        if _tm_cache["m"] is None:
            _tm_cache["m"] = text_pixels(roi)
        tm = _tm_cache["m"]
        core = tm[ya:ya + dh, ox0:ox0 + ow]
        if core.size == 0 or int(core.sum()) < 6:
            return False
        prof = core.sum(axis=1)
        rows = np.flatnonzero(prof > max(1, int(ow * 0.02)))
        if rows.size < 3:
            return False
        r0, r1 = int(rows[0]), int(rows[-1])
        if r0 <= 0 or r1 >= dh - 1:
            return False
        frac = (r1 - r0 + 1) / float(dh)
        return 0.30 <= frac <= 0.95

    out = []
    for i, (ya, xa0, xa1, sa) in enumerate(edges):
        for (yb, xb0, xb1, sb) in edges[i + 1:]:
            dh = yb - ya
            if dh < h_lo:
                continue
            if dh > h_hi:
                break
            if sa == sb:                    # 同向 -> 不是一块板的上下边
                continue
            ox0 = max(xa0, xb0)
            ox1 = min(xa1, xb1)
            ow = ox1 - ox0
            if ow < w * min_w_frac or ow > w * max_w_frac:
                continue
            # ---- 用"上下边同时存在"精修左右边界
            # 边缘合并会把相邻的东西（头像、编号圆）粘进来，配对得到的是
            # 两个 x 范围的交集，可能偏左或偏右。真正的板边界应当满足：
            # 这一列在上沿和下沿**都有**强梯度。
            band_top = mag[max(0, ya - 1):ya + 2, ox0:ox1].max(axis=0)
            band_bot = mag[max(0, yb - 1):yb + 2, ox0:ox1].max(axis=0)
            col_ok = (band_top > thr * 0.7) & (band_bot > thr * 0.7)
            if col_ok.sum() >= 10:
                runs = _runs(col_ok.astype(np.uint8),
                             max_gap=max(6, int(w * 0.04)),
                             min_len=max(20, int(ow * 0.55)))
                if runs:
                    rx0, rx1, _rl = max(runs, key=lambda r: r[2])
                    ox0, ox1 = ox0 + rx0, ox0 + rx1
                    ow = ox1 - ox0
                    if ow < w * min_w_frac:
                        continue
            ar = ow / float(dh)
            if not (5.0 <= ar <= 45.0):
                continue
            inside = gray[ya + 1:yb, ox0:ox1]
            if inside.size < 10:
                continue
            # 板内应当"平"：竖向梯度均值远小于边缘强度
            inner_gy = float(mag[ya + 1:yb, ox0:ox1].mean())
            e_top = float(mag[ya, ox0:ox1].mean())
            e_bot = float(mag[yb, ox0:ox1].mean())
            e_edge = 0.5 * (e_top + e_bot)
            if e_edge < 12.0:
                continue
            flatness = 1.0 / (1.0 + inner_gy / max(1.0, e_edge * 0.35))
            hsv_i = cv2.cvtColor(roi[ya + 1:yb, ox0:ox1], cv2.COLOR_BGR2HSV)
            sat_cov = float((hsv_i[:, :, 1] > 110).mean())
            # 血条里压着一行白字，且大致居中；能量 pip 行虽然也白，
            # 但它的白是"一排圆点"，水平重心会偏到 ★N 那边。
            white = ((hsv_i[:, :, 2] > 190) & (hsv_i[:, :, 1] < 105))
            w_cov = float(white.mean())
            text_like = 0.01 <= w_cov <= 0.30
            if text_like:
                cols = np.flatnonzero(white.any(axis=0))
                if cols.size:
                    cx = (cols[0] + cols[-1]) / 2.0 / float(white.shape[1])
                    text_like = abs(cx - 0.5) < 0.22
            conf = (flatness * 0.40
                    + min(1.0, e_edge / 90.0) * 0.25
                    + min(1.0, ar / 15.0) * 0.10
                    + min(1.0, sat_cov / 0.35) * 0.10
                    + (0.15 if text_like else 0.0))
            out.append({"x": int(ox0), "y": int(ya), "w": int(ow),
                        "h": int(dh), "conf": round(conf, 3),
                        "evidence": {"ar": round(ar, 1),
                                     "flat": round(flatness, 2),
                                     "edge": round(e_edge, 1),
                                     "inner": round(inner_gy, 1),
                                     "sat_cov": round(sat_cov, 3),
                                     "text": bool(text_like),
                                     "covers_text": None,
                                     "w_cov": round(w_cov, 3)}})
    # ---- covers_text 对每个候选都要判，但 text_pixels 整带只算一次（上面已缓存）
    out.sort(key=lambda d: -d["conf"])
    for d in out:
        covers = _text_inside(d["x"], d["y"], d["w"], d["h"])
        d["evidence"]["covers_text"] = bool(covers)
        d["conf"] = round(d["conf"] + (0.25 if covers else -0.30), 3)
    out.sort(key=lambda d: -d["conf"])
    dedup = []
    for d in out:
        dup = False
        for k in dedup:
            if (abs(d["y"] - k["y"]) <= 2 and abs(d["x"] - k["x"]) <= 5
                    and abs(d["w"] - k["w"]) <= max(6, d["w"] * 0.12)):
                dup = True
                break
        if not dup:
            dedup.append(d)
    return dedup[:6]


def find_bars(roi, min_w_frac=0.10, max_w_frac=0.62,
              h_frac=(0.008, 0.026), max_gap=4, h_ref=None, work_w=420,
              min_sat_cov=0.10):
    """在 roi 里找"横向长条"（血条 / 能量槽 / 伤害胶囊）。

    返回 [dict(x, y, w, h, conf, evidence)]，按 conf 降序，坐标是 roi 像素。
    做法：逐行找长游程 -> 垂直聚成带 -> 形状+边界对比打分。

    为了跨分辨率一致 + 快，内部先把 roi 缩到固定宽度 work_w 再扫；
    条高约束按 h_ref（调用方传整图高）的比例算，所以 800 和 1600 的高
    都落在同一档里。
    """
    if roi is None or roi.size == 0:
        return []
    rh, rw = roi.shape[:2]
    if rh < 12 or rw < 40:
        return []
    if h_ref is None:
        h_ref = rh

    # ---- 统一工作尺度
    scale = 1.0
    if work_w and rw > work_w * 1.5:
        scale = work_w / float(rw)
        roi_w = cv2.resize(roi, (work_w, max(8, int(round(rh * scale)))),
                           interpolation=cv2.INTER_AREA)
    else:
        roi_w = roi
    h, w = roi_w.shape[:2]

    fill, slot = _bar_masks(roi_w)
    m = (fill | slot).astype(np.uint8)
    # 水平闭运算：把血条内部的小缺口（文字、描边）连起来
    kx = _odd(int(w * 0.02), 3)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (kx, 1)))

    min_len = int(w * min_w_frac)
    per_row = []
    for y in range(h):
        rs = _runs(m[y], max_gap=max_gap, min_len=min_len)
        if rs:
            per_row.append((y, rs))

    # 垂直聚：相邻行、x 范围接近的游程归成同一块
    groups = []
    for y, rs in per_row:
        for (x0, x1, ln) in rs:
            hit = None
            for g in groups:
                if (y - g["y1"] <= 2
                        and abs(x0 - g["x0"]) <= max(3, int(w * 0.02))
                        and abs(x1 - g["x1"]) <= max(3, int(w * 0.02))):
                    hit = g
                    break
            if hit is None:
                groups.append({"y0": y, "y1": y, "x0": x0, "x1": x1})
            else:
                hit["y1"] = y
                hit["x0"] = min(hit["x0"], x0)
                hit["x1"] = max(hit["x1"], x1)

    V = cv2.cvtColor(roi_w, cv2.COLOR_BGR2HSV)[:, :, 2].astype(np.int16)
    S = cv2.cvtColor(roi_w, cv2.COLOR_BGR2HSV)[:, :, 1].astype(np.int16)
    out = []
    for g in groups:
        bw = g["x1"] - g["x0"] + 1
        bh = g["y1"] - g["y0"] + 1
        if bw <= 0 or bh <= 0:
            continue
        if bw < w * min_w_frac or bw > w * max_w_frac:
            continue
        ar = bw / float(bh)
        if not (5.0 <= ar <= 40.0):
            continue
        bh_orig = bh / scale
        if not (h_frac[0] * h_ref <= bh_orig <= h_frac[1] * h_ref):
            continue
        inner = V[g["y0"]:g["y1"] + 1, g["x0"]:g["x1"] + 1]
        if inner.size == 0:
            continue
        sin = S[g["y0"]:g["y1"] + 1, g["x0"]:g["x1"] + 1]
        # 饱和度覆盖：HP 条内部一定有"高饱和填充"（绿/黄/红/蓝）。
        # 能量 pip 行是近白低饱和，会被这一条筛掉 —— 这是两者最难区分的地方。
        sat_cov = float((sin > 110).mean())
        if sat_cov < min_sat_cov:
            continue
        # 边界对比：上下各外扩 2 行，均值差应当明显
        ty = max(0, g["y0"] - 2)
        by = min(h - 1, g["y1"] + 2)
        outside = float(np.mean([V[ty, g["x0"]:g["x1"] + 1].mean(),
                                 V[by, g["x0"]:g["x1"] + 1].mean()]))
        inside = float(inner.mean())
        contrast = abs(outside - inside)
        rowmean = inner.mean(axis=1)
        flat = 1.0 - min(1.0, float(rowmean.std()) / 40.0)
        conf = (min(1.0, contrast / 40.0) * 0.35
                + flat * 0.20
                + min(1.0, ar / 12.0) * 0.15
                + min(1.0, sat_cov / 0.45) * 0.30)
        inv = 1.0 / scale if scale else 1.0
        out.append({"x": int(round(g["x0"] * inv)),
                    "y": int(round(g["y0"] * inv)),
                    "w": int(round(bw * inv)),
                    "h": int(round(bh * inv)),
                    "conf": round(conf, 3),
                    "evidence": {"ar": round(ar, 1),
                                 "contrast": round(contrast, 1),
                                 "flat": round(flat, 2),
                                 "sat_cov": round(sat_cov, 3)}})
    out.sort(key=lambda d: -d["conf"])
    return out


def pick_pair(bars_l, bars_r, min_conf=0.45, max_dy_frac=0.008,
              img_h=1600, max_dw_frac=0.25, require_text=False):
    """左右各取一条血条底板，并用"镜像 UI"的几何关系交叉校验。

    HUD 是左右镜像的，所以两条底板几乎必然满足：
        同高（|dh| 小）、同宽（|dw| 小）、同一条水平线（|dy| 小）
    这三条一起用，比"谁的 conf 高"可靠得多——因为板的**边缘是成对出现的**
    （外框上下沿 + 填充上下沿），单看 conf 会挑到内填充而不是外框。

    require_text=True 时，只接受"完整包住那行数字"的候选。
    实测这是唯一能挡住"能量 pip 行 / 大厅横幅"这类误检的判据
    （负样本 23 条，不加这条时像素通道 23/23 都报出了值）。
    """
    def ok(c):
        if c is None:
            return False
        if c["conf"] < min_conf:
            return False
        if require_text and not c.get("evidence", {}).get("covers_text"):
            return False
        return True

    L = [b for b in bars_l[:5] if ok(b)]
    R = [b for b in bars_r[:5] if ok(b)]
    best = None
    for a in L:
        for b in R:
            dh = abs(a["h"] - b["h"])
            if dh > 0.30 * max(a["h"], b["h"]):
                continue
            dw = abs(a["w"] - b["w"])
            if dw > max_dw_frac * max(a["w"], b["w"]):
                continue
            dy = abs((a["y"] + a["h"] / 2.0) - (b["y"] + b["h"] / 2.0))
            if dy > max_dy_frac * img_h:
                continue
            sat = (a["evidence"].get("sat_cov", 0)
                   + b["evidence"].get("sat_cov", 0))
            score = a["conf"] + b["conf"] + 0.35 * sat - 2.0 * dy / max(1.0, img_h)
            if best is None or score > best[0]:
                best = (score, a, b, dy)
    if best is not None:
        _s, a, b, dy = best
        return a, b, dy

    a = L[0] if L and L[0]["conf"] >= min_conf else None
    b = R[0] if R and R[0]["conf"] >= min_conf else None
    if require_text:
        # 严格模式：**必须成对**。真实战斗里两侧一定同时有血条；
        # 大厅横幅、结算页这类"单侧的大板子"过不了这一关。
        return None, None, None
    return a, b, None


# --------------------------------------------------------------------------
# ② 白色贴纸 sticker
# --------------------------------------------------------------------------

def find_stickers(roi, min_aspect=2.8, max_aspect=12.0,
                  min_w_frac=0.18, min_fill=0.42):
    """在 roi 里找"近白圆角长条"（技能名牌 / 换人面板行 / 段位名牌）。

    旧代码用 S<40 & V>185 的绝对阈值，一张都找不到：
    贴纸是暖白（V 高但 S 有 20~45），而且上面的黑字把它切成几条细缝。
    这里改成「本区域分位数 + 较大的闭运算」，先把字孔补上再算形状。
    """
    if roi is None or roi.size == 0:
        return []
    h, w = roi.shape[:2]
    if h < 8 or w < 20:
        return []
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]

    # 「近白」是材质属性（反射率高），所以 V 的下限可以是绝对值；
    # 但上限不能写死，取本区域高分位，避免整块区域偏暗/偏亮时失效。
    thr_v = max(float(np.percentile(v, 90)), 185.0)
    thr_s = min(max(float(np.percentile(s, 35)), 40.0), 70.0)
    white = ((v >= thr_v) & (s <= thr_s)).astype(np.uint8)

    # 核高度按"候选贴纸高度"估：贴纸一般 0.02~0.05 倍区域高
    k = _odd(int(h * 0.02), 5)
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))

    out = []
    for c in _components(white, min_area=max(40, int(h * w * 0.0008))):
        bw, bh = c["w"], c["h"]
        if bw <= 0 or bh <= 0:
            continue
        if bw < w * min_w_frac:
            continue
        ar = bw / float(bh)
        if not (min_aspect <= ar <= max_aspect):
            continue
        fill = c["area"] / float(bw * bh)
        if fill < min_fill:
            continue
        out.append({"x": c["x"], "y": c["y"], "w": bw, "h": bh,
                    "conf": round(min(1.0, fill) * 0.6 + min(1.0, ar / 8.0) * 0.4, 3),
                    "evidence": {"ar": round(ar, 1), "fill": round(fill, 2)}})
    out.sort(key=lambda d: (d["y"], -d["conf"]))
    return out


# --------------------------------------------------------------------------
# ③ 白字黑边 label
# --------------------------------------------------------------------------

def label_mask(roi):
    """白字黑边 -> 0/1。笔画尺度由区域高度推出，不写像素值。"""
    if roi is None or roi.size == 0:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    h, w = v.shape[:2]
    stroke = _odd(max(2, int(h * 0.10)), 3)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (stroke, stroke))
    top = cv2.morphologyEx(v, cv2.MORPH_TOPHAT, ker)     # 亮细笔画
    bot = cv2.morphologyEx(v, cv2.MORPH_BLACKHAT, ker)   # 暗描边
    t_thr = max(18.0, float(np.percentile(top, 92)))
    b_thr = max(12.0, float(np.percentile(bot, 88)))
    m = ((top >= t_thr) & (bot >= b_thr)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    return m


def find_labels(roi, min_h_frac=0.30, max_h_frac=0.98):
    """找"白字黑边"的文字块（数字/汉字），返回每一行的合并框。"""
    m = label_mask(roi)
    if m is None:
        return []
    h, w = m.shape[:2]
    comps = [c for c in _components(m, min_area=max(12, int(h * w * 0.0002)))
             if c["h"] >= h * min_h_frac * 0.25 and c["h"] <= h * max_h_frac]
    if not comps:
        return []
    comps.sort(key=lambda c: c["x"])
    # 同一行的字块合并（字距 < 1.2 倍字高）
    lines = []
    for c in comps:
        placed = False
        for ln in lines:
            cy = ln["y"] + ln["h"] / 2.0
            if abs((c["y"] + c["h"] / 2.0) - cy) <= 0.6 * max(ln["h"], c["h"]):
                x1 = max(ln["x"] + ln["w"], c["x"] + c["w"])
                y0 = min(ln["y"], c["y"])
                y1 = max(ln["y"] + ln["h"], c["y"] + c["h"])
                ln.update({"x": min(ln["x"], c["x"]), "y": y0,
                           "w": x1 - min(ln["x"], c["x"]), "h": y1 - y0,
                           "n": ln.get("n", 1) + 1})
                placed = True
                break
        if not placed:
            lines.append({"x": c["x"], "y": c["y"], "w": c["w"], "h": c["h"], "n": 1})
    for ln in lines:
        ln["conf"] = round(min(1.0, ln["h"] / (h * 0.8)), 3)
    lines.sort(key=lambda d: d["x"])
    return lines


# --------------------------------------------------------------------------
# ④ 彩色圆徽 badge
# --------------------------------------------------------------------------

def find_badges(roi, size_frac=(0.10, 0.75), hole=False):
    """高饱和小色块。size_frac 相对 roi 高度。

    hole=True 时只返回"有洞"的（环），用于卡面禁用符号：
    红斜杠是一个环形连通域，比模板匹配稳得多，也不怕缩放。
    """
    if roi is None or roi.size == 0:
        return []
    h, w = roi.shape[:2]
    if h < 10 or w < 10:
        return []
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]
    m = ((s > max(90, float(np.percentile(s, 75)))) & (v > 80)).astype(np.uint8)
    if m.sum() < 20:
        return []
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    cnts, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    hier = hier[0] if hier is not None else []
    out = []
    for i, c in enumerate(cnts):
        x, y, bw, bh = cv2.boundingRect(c)
        if bh <= 0:
            continue
        if not (size_frac[0] * h <= bh <= size_frac[1] * h):
            continue
        ar = bw / float(bh)
        if not (0.55 <= ar <= 1.8):
            continue
        area = cv2.contourArea(c)
        if area / float(bw * bh) < 0.35:
            continue
        is_ring = bool(hier[i][3] != -1)          # 有父轮廓 = 洞里还有一个轮廓
        if hole and not is_ring:
            continue
        out.append({"x": x, "y": y, "w": bw, "h": bh, "ring": is_ring,
                    "conf": round(min(1.0, area / float(bw * bh)), 3)})
    out.sort(key=lambda d: -d["conf"])
    return out


# --------------------------------------------------------------------------
# 测量：填充率（亚像素）
# --------------------------------------------------------------------------

def refine_bar_by_text(img, slab, pad_y=0.35):
    """用"血条里那行白字"把板框精修一遍。

    为什么需要这一步
    ----------------
    边缘配对会同时找到"外框上下沿"和"填充上下沿"两对边，交集算出来的
    宽度可能偏左/偏右十几像素（实测 120411 那帧偏左 16px，比例从 0.95
    读成 0.09）。而血条里的白字**永远在板的正中间**，是最稳的种子：
    以字的垂直中心为板中心，向左右扫到"条色"结束。

    "条色" = 高饱和填充 ∪ 深色低饱和槽底；扫到场景（两者都不满足）就停。
    """
    x, y, w, h = slab["x"], slab["y"], slab["w"], slab["h"]
    py = int(round(h * pad_y))
    y0, y1 = max(0, y - py), min(img.shape[0], y + h + py)
    strip = img[y0:y1, max(0, x):min(img.shape[1], x + w)]
    if strip.size == 0:
        return slab
    hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]
    text = (v > 190) & (s < 110)
    if text.sum() < 20:
        return slab
    cols = np.flatnonzero(text.sum(axis=0) > max(1, text.shape[0] * 0.10))
    rows = np.flatnonzero(text.sum(axis=1) > max(1, text.shape[1] * 0.02))
    if cols.size < 4 or rows.size < 3:
        return slab
    tx0, tx1 = int(cols[0]), int(cols[-1])
    ty0, ty1 = int(rows[0]), int(rows[-1])
    if tx1 - tx0 < w * 0.10:
        return slab

    bar_h = int(round((ty1 - ty0 + 1) / 0.55))
    bar_h = int(np.clip(bar_h, max(6, int(h * 0.55)), int(h * 1.35)))
    cy = y0 + (ty0 + ty1) / 2.0
    my0 = int(round(cy - bar_h / 2.0))
    my1 = my0 + bar_h
    m = max(1, int(round(bar_h * 0.18)))
    ay0 = max(0, my0 - y0 + m)
    ay1 = min(strip.shape[0], my1 - y0 - m + 1)
    if ay1 - ay0 < 2:
        return slab
    band = hsv[ay0:ay1]
    bs = band[:, :, 1].astype(np.int16)
    bv = band[:, :, 2].astype(np.int16)
    bar_like = ((bs > 60) | ((bv > 25) & (bv < 165) & (bs < 75)))
    colscore = bar_like.mean(axis=0) > 0.45

    lo = tx0
    miss = 0
    while lo > 0:
        if colscore[lo - 1]:
            lo -= 1
            miss = 0
        else:
            miss += 1
            if miss > 2:
                break
            lo -= 1
    hi = tx1
    miss = 0
    while hi < colscore.size - 1:
        if colscore[hi + 1]:
            hi += 1
            miss = 0
        else:
            miss += 1
            if miss > 2:
                break
            hi += 1
    new_w = hi - lo + 1
    if new_w < w * 0.4 or new_w > w * 1.7:
        return slab
    out = dict(slab)
    out["x"] = max(0, x + lo)
    out["y"] = max(0, my0)
    out["w"] = int(new_w)
    out["h"] = int(bar_h)
    out["refined"] = True
    out["refine"] = {"text_w": tx1 - tx0 + 1, "new_w": new_w}
    return out


GLYPH = (16, 16)


def text_pixels(crop, v_min=165, s_max=115, ring_dark=15, min_ring=0.30,
                require_plate=False):
    """从一块画面里提取"带深描边的白字"像素。

    两道判据（都不依赖笔画尺度）：
        1. 外面一圈比字暗  -> 这一块是"白笔 + 深描边"
        2. require_plate 时再看**再外面一圈**是不是"板上色"
           （血条里的数字一定被底板包着；精灵名字压在场景上，过不了这条）

    为什么不用形态学核：tophat 必须知道笔画宽度，估小了描边整条进、
    估大了字被腐蚀掉（两版实测都失败，见 docs/DETECTION_V2.md §7.1）。
    """
    if crop is None or crop.size == 0:
        return np.zeros((1, 1), dtype=np.uint8)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    s = hsv[:, :, 1]
    bright = ((v > v_min) & (s < s_max)).astype(np.uint8)
    if bright.sum() < 8:
        return np.zeros_like(bright)
    n, lab, st, _ = cv2.connectedComponentsWithStats(bright, 8)
    plate = _bar_like(hsv)
    out = np.zeros_like(bright)
    for i in range(1, n):
        x, y, w, h, area = (int(st[i, 0]), int(st[i, 1]), int(st[i, 2]),
                            int(st[i, 3]), int(st[i, 4]))
        if h < 3 or w < 1:
            continue
        if w > 30 * h:                     # 细长亮线 = 贴片描边，不是字
            continue
        if area < max(6, h * 0.6):
            continue
        pad = 3
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1 = min(crop.shape[1], x + w + pad)
        y1 = min(crop.shape[0], y + h + pad)
        sub = (lab[y0:y1, x0:x1] == i).astype(np.uint8)
        if sub.sum() == 0:
            continue
        win_v = v[y0:y1, x0:x1].astype(np.int16)
        dil1 = cv2.dilate(sub, np.ones((5, 5), np.uint8))
        ring1 = (dil1 - sub) > 0
        if int(ring1.sum()) < 8:
            continue
        inside = float(win_v[sub > 0].mean())
        darker = float((win_v[ring1] < inside - ring_dark).mean())
        if darker < min_ring:
            continue
        if require_plate:
            dil2 = cv2.dilate(sub, np.ones((11, 11), np.uint8))
            ring2 = ((dil2 - dil1) > 0)
            if int(ring2.sum()) < 10:
                continue
            frac = float(plate[y0:y1, x0:x1][ring2].mean())
            if frac < 0.40:                # 外面不是底板 -> 不是血条里的数字
                continue
        out[y0:y1, x0:x1][sub > 0] = 1
    return out


def digit_upscale(bar_h, target=28.0):
    """按血条高决定放大倍数：目标让字高约 28px。

    为什么必须放大：1280 帧里血条只有 9~10px，字高 6~7px，
    直接归一化到 16x16 等于从 7px 硬拉，笔画会粘在一起（切分和分类都错）。
    """
    if bar_h <= 0:
        return 1.0
    s = target / max(1.0, 0.62 * bar_h)
    return float(np.clip(s, 1.0, 6.0))


def digit_strip(img, box, pad_frac=0.10):
    """血条 -> 放大后的字条。返回 (crop, scale)。"""
    if isinstance(box, dict):
        x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    else:
        x, y, w, h = box
    pad = int(round(h * pad_frac))
    y0 = max(0, y + int(round(h * 0.12)))
    y1 = min(img.shape[0], y + h - int(round(h * 0.12)))
    x0 = max(0, x - pad)
    x1 = min(img.shape[1], x + w + pad)
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return None, 1.0
    s = digit_upscale(h)
    if s > 1.05:
        crop = cv2.resize(crop, None, fx=s, fy=s,
                          interpolation=cv2.INTER_CUBIC)
    return crop, s


def white_mask(crop):
    """UI 白字（白笔 + 深描边）的粗筛。

    注意：`V>190 & S<110` 会把亮天空/白云也算进来（145014 那帧背景就是白云）。
    试过改成 tophat（"局部更亮"），结果把血条描边整条选进来、反而漏掉了数字。
    要同时满足"亮"和"有深描边"必须 tophat ∧ blackhat 双条件，
    但 stroke 尺度各家不同，还没调稳 —— 见 docs/DETECTION_V2.md §7.1。
    目前保留这个较宽但有界的版本，配合"字形数/结构校验"使用。
    """
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    return ((hsv[:, :, 2] > 190) & (hsv[:, :, 1] < 110)).astype(np.uint8)


def split_glyphs(mask, min_gap):
    """把白字切成一个个字形。返回 [(x0, x1, y0, y1)]。

    为什么不再"取最密的一行当带"
    ----------------------------
    血条里常有一条横跨整条的亮线（描边/基线），它的像素比数字多，
    于是带会落到那条线上，切出来的"字"是线段（实测 145014）。
    改成：把行投影切成若干候选带，每带都切一遍，再按"像不像一行字"打分：
        · 字块数量 3~10
        · 字块高度一致（std/mean 小）
        · 字块高宽比合理（不是细长线）
    """
    h, w = mask.shape[:2]
    if h < 4 or w < 8:
        return []
    rows = mask.sum(axis=1)
    if rows.max() < 3:
        return []
    thr = max(2, int(rows.max() * 0.30))
    # 候选带：行投影里连续超过阈值的段
    bands = []
    y = 0
    while y < h:
        if rows[y] >= thr:
            y0 = y
            while y < h and rows[y] >= thr:
                y += 1
            if y - y0 >= 3:
                bands.append((y0, y - 1))
        else:
            y += 1
    if not bands:
        return []

    best, best_score = [], -1.0
    for (by0, by1) in bands:
        band = mask[by0:by1 + 1]
        bh = band.shape[0]
        col = band.sum(axis=0) > max(1, int(bh * 0.20))
        idx = np.flatnonzero(col)
        if idx.size < 3:
            continue
        runs = []
        start = prev = int(idx[0])
        for i in idx[1:]:
            i = int(i)
            if i - prev <= min_gap:
                prev = i
                continue
            runs.append((start, prev))
            start = prev = i
        runs.append((start, prev))
        glyphs = []
        for x0c, x1c in runs:
            if x1c - x0c < 1:
                continue
            sub = band[:, x0c:x1c + 1]
            rr = np.flatnonzero(sub.any(axis=1))
            if rr.size == 0:
                continue
            glyphs.append((x0c, x1c, by0 + int(rr[0]), by0 + int(rr[-1])))
        n = len(glyphs)
        if n < 2 or n > 12:
            continue
        hs = np.array([g[3] - g[2] + 1 for g in glyphs], dtype=np.float32)
        ws = np.array([g[1] - g[0] + 1 for g in glyphs], dtype=np.float32)
        if hs.mean() < 3:
            continue
        cv_h = float(hs.std() / max(1.0, hs.mean()))
        # 细长线（宽 >> 高）说明这还是横线
        thin = float((ws > 8 * np.maximum(1, hs)).mean())
        score = (min(n, 8) - 4.0 * cv_h - 6.0 * thin
                 + min(1.0, hs.mean() / max(6.0, bh * 0.8)))
        if score > best_score:
            best, best_score = glyphs, score
    return best


def norm_glyph(mask, box):
    """字形 -> GLYPH 大小（保持宽高比，水平居中）。"""
    x0, x1, y0, y1 = box
    sub = mask[y0:y1 + 1, x0:x1 + 1]
    if sub.size == 0:
        return None
    h, w = sub.shape
    th = GLYPH[1]
    tw = max(1, int(round(w * th / float(h))))
    r = cv2.resize(sub.astype(np.float32), (tw, th), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((th, GLYPH[0]), dtype=np.float32)
    if tw >= GLYPH[0]:
        canvas[:] = r[:, :GLYPH[0]]
    else:
        off = (GLYPH[0] - tw) // 2
        canvas[:, off:off + tw] = r
    return canvas


def _bar_like(hsv_patch):
    """"底板内部"的像素：高饱和填充 或 深色低饱和槽底。"""
    s = hsv_patch[:, :, 1].astype(np.int16)
    v = hsv_patch[:, :, 2].astype(np.int16)
    return (s > 60) | ((v > 25) & (v < 165) & (s < 75))


def refine_plate_by_glyphs(img, box, pad_frac=0.75):
    """用"血条上那行数字"当锚，把底板框重新量一遍。

    为什么需要（这是实测的根因）
    ----------------------------
    有些帧血条左端被名字/头像压住，上下沿断掉，边缘配对只拿到右半截，
    于是 measure_fill 判成"左端没填充"(0%)。

    推理链（每一步都有帧验证）
    ------------------------
      · 数字在底板里水平 + 垂直居中
      · 字高 ≈ 0.55 × 板高
      · 板内是"填充色 或 深灰槽底"；**板外 2px 立刻不是**（头像/徽章往外还是彩色）

    第三条是之前两版都缺的"垂直一致性"，也是这次能成的关键。
    """
    if box is None:
        return box
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    py = int(round(h * 0.55))
    # 横向要给足：要修的就是"框只盖住半截"，所以必须能看到整块 HUD 的水平范围
    px = max(int(round(w * 0.15)), int(round(img.shape[1] * 0.12)))
    cx0 = max(0, x - px)
    cy0 = max(0, y - py)
    cx1 = min(img.shape[1], x + w + px)
    cy1 = min(img.shape[0], y + h + py)
    crop = img[cy0:cy1, cx0:cx1]
    if crop.size == 0:
        return box

    m = text_pixels(crop, require_plate=True)
    if m.sum() < 16:
        return box
    gl = split_glyphs(m, min_gap=max(1, int(round(h * 0.10))))
    if len(gl) < 2:
        return box
    # 只保留"落在原框 y 范围内"的字形 —— 排除上方名字、下方能量行
    keep = [g for g in gl
            if abs((g[2] + g[3]) / 2.0 - (y + h / 2.0 - cy0)) <= 0.55 * h]
    if len(keep) >= 3:
        # 一行字的高度是均匀的：只留高度 >= 60% 最大字高的，把零碎噪声剔掉
        hmax = max(g[3] - g[2] + 1 for g in keep)
        keep = [g for g in keep if (g[3] - g[2] + 1) >= 0.6 * hmax]
    if len(keep) < 2:
        return box          # 找不到"这一行数字"就不动它（宁可不动，不要改错）
    tx0 = min(g[0] for g in keep)
    tx1 = max(g[1] for g in keep)
    ty0 = min(g[2] for g in keep)
    ty1 = max(g[3] for g in keep)
    text_h = ty1 - ty0 + 1
    if text_h < 4 or (tx1 - tx0) < 0.08 * w:
        return box
    cx = cx0 + (tx0 + tx1) / 2.0
    cy = cy0 + (ty0 + ty1) / 2.0
    # 只修**横向**：残片问题都是"框只盖住右半截"，纵向由边缘配对给得更可靠。
    # （字高/板高的比例常数在不同皮肤/字体下会变，实测 0.55 在这里不成立，
    #   所以不拿它推板高，直接用原框的高度。）
    plate_h = h
    cy = float(y + h / 2.0)

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hh, ww = hsv.shape[:2]
    mg = max(1, int(round(plate_h * 0.20)))
    py0 = int(round(y - cy0))
    py1 = py0 + plate_h
    ry0, ry1 = py0 + mg, py1 - mg
    ry0, ry1 = max(1, ry0), min(hh - 2, ry1)
    if ry1 - ry0 < 2:
        return box
    inside = _bar_like(hsv[ry0:ry1]).mean(axis=0)
    # 数字压在板上 —— 那些列既有字又有板，字对"是不是板"没有投票权，算通过
    txt = (m[ry0:ry1] > 0).mean(axis=0)
    inside = np.maximum(inside, txt)
    # "板外"必须取板边界之外的 3 行（不能只外扩 2px —— 板高本身就有 mg 的收缩，
    # 外扩 2px 还落在板内，于是整列都被判成"板外不是场景"，永远不通过）
    ay0, ay1 = py0 - 3, py0 - 1
    by0, by1 = py1 + 1, py1 + 3
    above = (_bar_like(hsv[max(0, ay0):max(0, ay1)]).mean(axis=0)
             if ay1 > 0 and ay0 < hh else np.zeros(ww))
    below = (_bar_like(hsv[by0:min(hh, by1)]).mean(axis=0)
             if by0 < hh - 1 else np.zeros(ww))
    # 板内够"条色"，且上下至少有一侧立刻变回场景
    col_ok = (inside > 0.80) & ((above < 0.55) | (below < 0.55))

    ci = int(round(cx - cx0))
    if not (0 <= ci < ww):
        return box
    lo = hi = ci
    miss = 0
    while lo > 0:
        if col_ok[lo - 1]:
            lo -= 1
            miss = 0
        else:
            miss += 1
            if miss > 2:
                break
            lo -= 1
    miss = 0
    while hi < ww - 1:
        if col_ok[hi + 1]:
            hi += 1
            miss = 0
        else:
            miss += 1
            if miss > 2:
                break
            hi += 1
    dl, dr = cx - (cx0 + lo), (cx0 + hi) - cx
    if max(dl, dr) < 0.6 * text_h:
        return box
    half = max(dl, dr)                      # 数字居中 -> 用较长的一侧对称
    new_w = int(round(2 * half))
    out = dict(box)
    out["x"] = int(round(cx - half))
    out["y"] = int(round(cy - plate_h / 2.0))
    out["w"] = new_w
    out["h"] = plate_h
    out["from_glyphs"] = True
    out["evidence"] = dict(box.get("evidence", {}))
    out["evidence"].update({"glyphs": len(keep), "text_h": int(text_h),
                            "dl": int(dl), "dr": int(dr)})
    return out


def measure_fill(img, box, pad_y=0.18):
    """测"从左端开始的填充比例"。返回 (ratio, info)。

    三条实测得出的关键结论，都写进实现里：

    1) 方向：双方都从**左端**往右长（87% 那帧确认）。反了会把 87% 读成 13%。
    2) 两端是圆角，所以"满血"和"95%"在像素上只差几个像素 —— 用"量填充长度"
       永远有 ±4% 的系统误差。改成：**有缝隙才量缝隙，没缝隙就是满**。
    3) 血条中间压着白字（397/417），白字饱和度低，会被误当成空槽。
       所以列剖面只取**上下两条边带**（文字只占垂直中间），这样完全绕开文字。
    """
    if isinstance(box, dict):
        x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    else:
        x, y, w, h = box
    # 只去掉 1~2px 描边。**不能**按"条高的 40%"内缩：
    # 填充是从贴片最左端开始画的（圆角端也在填充里），
    # 内缩会把两侧圆角切掉，满血读成 96%、95% 读成 100%。
    dx = max(2, int(round(h * 0.12)))
    dy = max(1, int(round(h * 0.14)))
    x0, y0 = x + dx, y + dy
    x1, y1 = x + w - 1, y + h - dy
    if x1 - x0 < 8 or y1 - y0 < 3:
        return None, {"why": "box too small"}
    inner = img[max(0, y0):y1, max(0, x0):x1]
    if inner.size == 0:
        return None, {"why": "empty"}
    hsv = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1].astype(np.float32)
    v = hsv[:, :, 2].astype(np.float32)
    # ---- 均匀性检查（这一条来自真值：红条 0% 与 20% 用饱和度根本分不开）
    # 先剔掉白字，再取列中值；如果整条只有一种颜色，说明要么满血要么空槽，
    # 此时**单帧无法靠颜色区分**，只能用亮度粗判，中间地带直接返回 None。
    is_text = (v > 200) & (s < 70)
    with np.errstate(all="ignore"):
        sv = np.nanmedian(np.where(is_text, np.nan, s), axis=0)
        vv = np.nanmedian(np.where(is_text, np.nan, v), axis=0)
    sv = sv[np.isfinite(sv)]
    vv = vv[np.isfinite(vv)]
    if sv.size >= 4 and vv.size >= 4:
        if float(np.percentile(sv, 90) - np.percentile(sv, 10)) < 25:
            vmed = float(np.median(vv))
            if vmed > 170:
                return 1.0, {"why": "uniform bright", "v": round(vmed, 1)}
            if vmed < 140:
                return 0.0, {"why": "uniform dark", "v": round(vmed, 1)}
            return None, {"why": "uniform ambiguous", "v": round(vmed, 1)}
    # 判据："这一列有没有**彩色**像素"。
    #   · 填充 = 绿/蓝/黄/红，饱和度高
    #   · 空槽 = 中性灰，饱和度低
    #   · 白字/描边 = 饱和度接近 0
    # 白字会把它那几列的彩色像素压到只剩 1~2 个（条只有 7~10 行高），
    # 所以只要求 ≥1，再用沿 x 的闭运算把文字造成的短空档补上
    # （闭运算不越过端点，不会把填充延长到空槽里）。
    #
    # 已知缺陷（有数据）：红色/暗红血条的填充与槽底饱和度都高，会失效 ——
    # 实测 20% 读成 0%、0% 读成 9%。试过改成"亮度 + Otsu"，整体更差
    # （≤1pt 从 17/27 掉到 14/27），所以保留这一版，把红条列为待解。
    # 判据："这一列有没有**彩色**像素"。
    # 试过改成"按本条自身饱和度分布取相对阈值"（想救暗红条的 20%），
    # 结果更差：满血条的渐变被切断 → 错报 7→17。绝对阈值 S>110 保留。
    # 暗红低血条列为已知缺陷（有真值记录，别再拍脑袋改）。
    col = ((s > 110) & (hsv[:, :, 2] > 70)).sum(axis=0) >= 1
    n = col.size
    if n < 6:
        return None, {"why": "profile too short"}
    if not col.any():
        return 0.0, {"why": "no colored column", "n": n}
    first_fill = int(np.argmax(col))
    if first_fill > 0.35 * n:
        return 0.0, {"why": "fill starts too far right", "n": n,
                     "first": first_fill}
    k = max(3, int(round(n * 0.20)) | 1)
    row = col.astype(np.uint8).reshape(1, -1)
    row = cv2.morphologyEx(row, cv2.MORPH_CLOSE,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
    col = row.reshape(-1) > 0
    idx = n - 1
    while idx >= 0 and not col[idx]:
        idx -= 1
    if idx < 0:
        return 0.0, {"why": "no fill", "n": n}
    if n - idx <= 2:
        return 1.0, {"n": n, "slot_px": n - idx, "why": "no gap"}
    edge = float(idx + 1)
    ratio = float(np.clip((dx + edge) / float(w), 0.0, 1.0))
    return ratio, {"n": n, "slot_px": n - idx, "edge_px": round(edge, 2),
                   "plate_w": w}


# --------------------------------------------------------------------------
# 数值通道：读血条上印刷的数字
#
# 为什么数值不能靠量像素
# ----------------------
# 1280 宽的帧里血条只有 10px 高，1px = 0.8%；两端还是圆角，
# 满血与 95% 在像素上只差 2~3px，天然有 ±4% 歧义（实测确实如此）。
# 而游戏把答案印在条上：我方 "397/417"，敌方 "69%"。读字是精确的。
# 代价是一次 OCR 0.2~0.4s —— 所以它只在"有新信息"时调用，不放进 25ms 的循环。
# --------------------------------------------------------------------------

_OCR = None


def ocr_engine():
    global _OCR
    if _OCR is None:
        import ocr_cn
        _OCR = ocr_cn.Ocr()
    return _OCR


def glyph_strip(img, box, pad=0.45):
    """血条 -> "一条紧贴字的图"，放大到字高 ~40px（OCR 只需认字，不需要检测）。"""
    if isinstance(box, dict):
        x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    else:
        x, y, w, h = box
    py = int(round(h * pad))
    px = int(round(w * 0.02))
    crop = img[max(0, y - py):y + h + py, max(0, x + px):x + w - px]
    if crop.size == 0 or crop.shape[0] < 6 or crop.shape[1] < 12:
        return None
    sc = min(6.0, max(2.0, 40.0 / max(1, h)))
    big = cv2.resize(crop, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
    return cv2.cvtColor(big, cv2.COLOR_BGR2RGB)


def parse_hp_cur_max(text):
    """OCR 文本 -> (cur, max)。容忍分隔符被识别成数字（412/412 -> 4127412）。"""
    import re
    if not text:
        return None
    digits = "".join(re.findall(r"\d+", text))
    if len(digits) < 4:
        return None
    digits = digits.lstrip("0") or digits
    n = len(digits)

    def ok(a, b):
        try:
            ai, bi = int(a), int(b)
        except ValueError:
            return None
        if bi <= 0 or ai > bi or not (50 <= bi <= 5000):
            return None
        return ai, bi

    if n % 2 == 0:
        mid = n // 2
        got = ok(digits[:mid], digits[mid:])
        if got:
            return got
    else:
        mid = n // 2
        got = ok(digits[:mid], digits[mid + 1:])
        if got:
            return got
    best = None
    for i in range(2, n - 1):
        got = ok(digits[:i], digits[i:])
        if got:
            pen = abs(len(digits[:i]) - len(digits[i:]))
            if best is None or pen < best[0]:
                best = (pen, got)
    return best[1] if best else None


def parse_pct(text):
    """敌方血条只印百分比。"""
    import re
    if not text:
        return None
    t = text.replace("O", "0").replace("o", "0").replace("Q", "0")
    m = re.search(r"(\d{1,3})", t)
    if not m:
        return None
    n = int(m.group(1))
    return None if n > 100 else n / 100.0


def read_hp_text(img, box, side):
    """读血条上的印刷数字 -> (ratio, raw_text)。"""
    strip = glyph_strip(img, box)
    if strip is None:
        return None, ""
    try:
        lines = ocr_engine().read_line(strip)
    except Exception:
        return None, ""
    text = " ".join(t for t, _c in lines)
    if side == "my":
        got = parse_hp_cur_max(text)
        if got:
            return got[0] / float(got[1]), text
        return None, text
    return parse_pct(text), text


# --------------------------------------------------------------------------
# 组合：战斗 HUD（左右血条 + 能量行 + 名字）
# --------------------------------------------------------------------------

DEFAULT_BANDS = {
    "my_hud": (0.010, 0.020, 0.330, 0.150),
    "foe_hud": (0.670, 0.020, 0.995, 0.150),
    "cards": (0.030, 0.240, 0.270, 0.870),
    "countdown": (0.330, 0.000, 0.670, 0.110),
    "bottom": (0.220, 0.840, 0.780, 0.995),
}


def mirror_box(src, img_w):
    """按"左右镜像的同一套 HUD"推出另一侧的框。"""
    return {"x": int(round(img_w - (src["x"] + src["w"]))),
            "y": int(src["y"]), "w": int(src["w"]), "h": int(src["h"]),
            "conf": float(src.get("conf", 0.5)) * 0.9,
            "mirrored": True,
            "evidence": dict(src.get("evidence", {}))}


def _snap_box(img, box, search=0.02):
    """镜像出来的框可能差几像素：在 ±search*W 内按"边缘能量"找一个更好的 x。"""
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    H, W = img.shape[:2]
    band = int(round(w * search))
    x0 = max(0, x - band)
    x1 = min(W, x + w + band)
    y0, y1 = max(0, y - 2), min(H, y + h + 2)
    roi = img[y0:y1, x0:x1]
    if roi.size == 0:
        return box
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    # 每一列上沿+下沿的梯度之和 -> 真正的板边界处会有尖峰
    top = gy[max(0, y - y0 - 1):min(roi.shape[0], y - y0 + 2)].max(axis=0)
    bot = gy[max(0, y + h - y0 - 1):min(roi.shape[0], y + h - y0 + 2)].max(axis=0)
    score = top + bot
    # 用滑动窗口找"左右两个尖峰"，间距最接近 w
    best = None
    for sx in range(0, max(1, score.size - 2)):
        v = score[sx]
        if v < 40:
            continue
        for ex in range(sx + 2, score.size):
            ww = ex - sx
            if ww < w * 0.8:
                continue
            if ww > w * 1.2:
                break
            sc = v + score[ex] - abs(ww - w) * 2.0
            if best is None or sc > best[0]:
                best = (sc, sx, ex)
    if best is None:
        return box
    _sc, sx, ex = best
    out = dict(box)
    out["x"] = x0 + sx
    out["w"] = ex - sx
    return out


def bar_from_text(img, band, partner_w=None):
    """以"血条上那行白字"为锚，直接推出底板框（不靠边缘配对）。

    推理链（每一步都有帧验证）：
      · 数字在底板里**水平+垂直居中**
      · 字高 ≈ 0.55 × 板高（多帧实测）
      · 板内是"填充色 或 深灰槽底"，板外是场景
    所以：字高定板高、字心定板心、向两侧扫"连续条色列"定板宽，
    再用另一侧的板宽做一次交叉校验。

    这条路比边缘配对准，因为**不会被嵌套边（外框沿/填充沿）和头像干扰**。
    """
    x0, y0, bw, bh = band_box(img, band)
    roi = img[y0:y0 + bh, x0:x0 + bw]
    if roi.size == 0:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    m = ((hsv[:, :, 2] > 190) & (hsv[:, :, 1] < 110)).astype(np.uint8)
    if m.sum() < 12:
        return None
    h, w = m.shape[:2]
    comps = _components(m, min_area=max(4, int(h * w * 0.00006)))
    # 只要"像数字"的块：高度在合理范围、不是横条
    digits = [c for c in comps
              if 0.004 * img.shape[0] <= c["h"] <= 0.030 * img.shape[0]
              and 0.25 <= c["w"] / float(max(1, c["h"])) <= 4.0]
    if not digits:
        return None
    # 聚成一行：取 y 中心最密集的一组
    digits.sort(key=lambda c: c["y"] + c["h"] / 2.0)
    best_line = []
    for c in digits:
        cy = c["y"] + c["h"] / 2.0
        line = [d for d in digits
                if abs((d["y"] + d["h"] / 2.0) - cy) <= 0.6 * max(d["h"], c["h"])]
        if len(line) > len(best_line):
            best_line = line
    if len(best_line) < 2:
        return None
    tx0 = min(c["x"] for c in best_line)
    tx1 = max(c["x"] + c["w"] for c in best_line)
    ty0 = min(c["y"] for c in best_line)
    ty1 = max(c["y"] + c["h"] for c in best_line)
    text_h = ty1 - ty0
    if text_h < 4:
        return None
    cx = (tx0 + tx1) / 2.0
    cy = (ty0 + ty1) / 2.0
    bar_h = text_h / 0.55
    bar_h = float(np.clip(bar_h, text_h * 1.2, text_h * 2.6))

    # 板内 = "彩色填充 或 深灰槽底"；在板高的中间 60% 上统计，避开描边
    m2 = max(1, int(round(bar_h * 0.20)))
    ay0 = int(round(cy - bar_h / 2.0 + m2))
    ay1 = int(round(cy + bar_h / 2.0 - m2))
    ay0, ay1 = max(0, ay0), min(h, ay1)
    if ay1 - ay0 < 2:
        return None
    bs = hsv[ay0:ay1, :, 1].astype(np.int16)
    bv = hsv[ay0:ay1, :, 2].astype(np.int16)
    bar_like = ((bs > 60) | ((bv > 25) & (bv < 165) & (bs < 75)))
    col_ok = bar_like.mean(axis=0) > 0.65

    ci = int(round(cx))
    if ci < 0 or ci >= w:
        return None
    left = right = ci
    miss = 0
    while left > 0:
        if col_ok[left - 1]:
            left -= 1
            miss = 0
        else:
            miss += 1
            if miss > 2:
                break
            left -= 1
    miss = 0
    while right < w - 1:
        if col_ok[right + 1]:
            right += 1
            miss = 0
        else:
            miss += 1
            if miss > 2:
                break
            right += 1
    dl, dr = cx - left, right - cx
    if max(dl, dr) < 0.5 * text_h:
        return None
    half = max(dl, dr)                       # 用较长的一侧，再关于字心对称
    if partner_w and abs(2 * half - partner_w) <= 0.30 * partner_w:
        half = partner_w / 2.0
    new_x = int(round(cx - half))
    new_w = int(round(2 * half))
    return {"x": max(0, x0 + new_x), "y": int(round(y0 + cy - bar_h / 2.0)),
            "w": int(new_w), "h": int(round(bar_h)), "conf": 0.8,
            "from_text": True,
            "evidence": {"text_h": int(text_h), "dl": int(dl), "dr": int(dr)}}


def _text_cx(img, box):
    """框内白字的水平中心（血条上的数字是**水平居中**于底板的，实测一致）。"""
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    crop = img[max(0, y):y + h, max(0, x):x + w]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    m = ((hsv[:, :, 2] > 190) & (hsv[:, :, 1] < 110))
    if m.sum() < 8:
        return None
    cols = np.flatnonzero(m.sum(axis=0) > max(1, m.shape[0] * 0.10))
    if cols.size < 3:
        return None
    return x + (int(cols[0]) + int(cols[-1])) / 2.0


def fix_fragment(img, box, partner_w=None):
    """候选只是底板的一截时，用"字宽占比 + 另一侧板宽"把它补全。

    判据（比"宽度差"可靠）：血条里的数字大约占板宽的 **35%~55%**。
    若测出来字宽 > 0.62×框宽，说明框只框住了字的一部分，是残片。
    此时用 max(另一侧板宽, 字宽/0.45) 定宽，并用"字居中"定位置。

    症状来源：有些帧血条左端被名字/头像压住，上下沿断掉，
    边缘配对只拿到右半截，于是 measure_fill 判成"左端没填充"。
    """
    if box is None:
        return box
    tw, cx = _text_span(img, box)
    if tw is None or box["w"] <= 0:
        return box
    ratio = tw / float(box["w"])
    if ratio <= 0.62:
        return box
    need = tw / 0.45
    if partner_w and partner_w > need * 0.85:
        need = float(partner_w)
    if need > box["w"] * 3:
        return box
    out = dict(box)
    out["x"] = int(round(cx - need / 2.0))
    out["w"] = int(round(need))
    out["fixed_fragment"] = True
    out["evidence"] = dict(box.get("evidence", {}))
    out["evidence"]["text_ratio"] = round(ratio, 2)
    return out


def _text_span(img, box):
    """框内白字的宽度与水平中心。"""
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    crop = img[max(0, y):y + h, max(0, x):x + w]
    if crop.size == 0:
        return None, None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    m = ((hsv[:, :, 2] > 190) & (hsv[:, :, 1] < 110))
    if m.sum() < 8:
        return None, None
    cols = np.flatnonzero(m.sum(axis=0) > max(1, m.shape[0] * 0.10))
    if cols.size < 3:
        return None, None
    t0, t1 = int(cols[0]), int(cols[-1])
    return (t1 - t0 + 1), x + (t0 + t1) / 2.0


def locate_hud(img, bands=None, strict=False):
    """定位战斗 HUD。返回 dict，失败字段为 None（绝不填默认值）。"""
    bands = bands or DEFAULT_BANDS
    H, W = img.shape[:2]
    bx, by, bw, bh = band_box(img, bands["my_hud"])
    my_roi = img[by:by + bh, bx:bx + bw]
    fx, fy, fw, fh = band_box(img, bands["foe_hud"])
    foe_roi = img[fy:fy + fh, fx:fx + fw]

    bars_l = [dict(b, x=b["x"] + bx, y=b["y"] + by)
              for b in find_slabs(my_roi, H)]
    bars_r = [dict(b, x=b["x"] + fx, y=b["y"] + fy)
              for b in find_slabs(foe_roi, H)]
    a, b, dy = pick_pair(bars_l, bars_r, img_h=H, require_text=strict)
    # 两侧没能"几何对上"的时候，用镜像补一侧：HUD 是左右镜像的同一套 UI，
    # y/h 相同、x 关于竖直中线对称。这比"退回去用单侧最高分"稳得多
    # （单侧最高分经常是内填充边或能量 pip 行，实测就是这样读错的）。
    if dy is None:
        W = img.shape[1]
        if a is not None and b is not None:
            if a.get("conf", 0) >= b.get("conf", 0):
                b = _snap_box(img, mirror_box(a, W))
            else:
                a = _snap_box(img, mirror_box(b, W))
        elif a is not None:
            b = _snap_box(img, mirror_box(a, W))
        elif b is not None:
            a = _snap_box(img, mirror_box(b, W))
    out = {"my_bar": a, "foe_bar": b,
           "dy": (round(dy, 1) if dy is not None else None),
           "my_ratio": None, "foe_ratio": None,
           "my_ratio_info": None, "foe_ratio_info": None}
    # 残片修复：目前**关闭**。
    # 试过两版（"宽度差"与"字宽占比"），都会误触发：血条的高光/描边本身
    # 就满足"近白"条件，_text_span 会把整条读成"字宽"，于是把框扩到全屏。
    # 结论写在 docs/DETECTION_V2.md §6.3：要修必须先"只认数字字形"（见 §7.2
    # 的数字模板），而不是用颜色判白。这里保留函数，等字形库做完再开。
    # if a is not None and b is not None:
    #     a = fix_fragment(img, a, b["w"])
    #     b = fix_fragment(img, b, a["w"])
    # §7.1 的"用字形行反推底板框"目前**关闭**（函数保留在下面）。
    # 卡点在 white_mask：`V>190 & S<110` 会把亮天空/白云算进字，字形行会落到背景上；
    # 改用 tophat 又会把血条描边整条选进来、反而漏掉数字（实测两版都试过）。
    # 稳定做法必须是 tophat ∧ blackhat（白笔 + 深描边）双条件 + 自适应 stroke 尺度。
    # 详见 docs/DETECTION_V2.md §7.1 的失败记录与判据。
    out["my_bar"], out["foe_bar"] = a, b
    if a is not None:
        out["my_ratio"], out["my_ratio_info"] = measure_fill(img, a)
    if b is not None:
        out["foe_ratio"], out["foe_ratio_info"] = measure_fill(img, b)
    return out


def _disc_once(roi, v_floor, pctl, k_frac, size_frac, need_dark_center):
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]
    h, w = roi.shape[:2]
    thr_v = max(v_floor, float(np.percentile(v, pctl)))
    white = ((v >= thr_v) & (s <= 62)).astype(np.uint8)
    k = _odd(int(round(h * k_frac)), 3)
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    out = []
    for c in _components(white, min_area=max(30, int(h * w * 0.00012))):
        bw, bh = c["w"], c["h"]
        if bh <= 0:
            continue
        if not (size_frac[0] * h <= bh <= size_frac[1] * h):
            continue
        if not (0.72 <= bw / float(bh) <= 1.40):
            continue
        fill = c["area"] / float(bw * bh)
        if fill < 0.35:
            continue
        dark = 0.0
        if need_dark_center:
            cx0 = c["x"] + int(bw * 0.22)
            cy0 = c["y"] + int(bh * 0.22)
            cx1 = c["x"] + int(bw * 0.78)
            cy1 = c["y"] + int(bh * 0.78)
            core = v[max(0, cy0):cy1, max(0, cx0):cx1]
            if core.size == 0:
                continue
            dark = float((core < 120).mean())
            if dark < 0.10:
                continue
        out.append({"x": c["x"], "y": c["y"], "w": bw, "h": bh,
                    "fill": round(fill, 2), "dark": round(dark, 2),
                    "d": round((bw + bh) / 2.0, 1)})
    out.sort(key=lambda d: d["y"])
    return out


def _consistent(badges, expect):
    """一组编号圆是否"像真的"：y 单调、尺寸别差太多、间距别跳。

    阈值刻意放松（宁可选出一组有噪声的，也不要整组丢掉）：
    编号圆的数量和位置是**结构信息**，少一个还能靠相邻行插值，
    整组没了就没法推卡面了。
    """
    if len(badges) < 2:
        return 0.0
    ds = np.array([b["d"] for b in badges], dtype=np.float32)
    pen_d = float(ds.std() / max(1.0, ds.mean()))
    if pen_d > 0.45:
        return 0.0
    ys = [b["y"] + b["h"] / 2.0 for b in badges]
    gaps = np.diff(ys)
    if gaps.size and gaps.min() <= 2:
        return 0.0
    pen_g = float(gaps.std() / max(1.0, gaps.mean())) if gaps.size else 0.0
    if pen_g > 0.60:
        return 0.0
    score = float(len(badges)) - 1.5 * pen_d - 1.0 * pen_g
    return max(0.0, min(score, float(expect)))


def find_disc_badges(roi, size_frac=(0.012, 0.075), need_dark_center=True,
                     expect=4):
    """找"白色圆片 + 深色数字"的编号徽章（技能卡左侧的 1/2/3/4）。

    为什么它是技能卡最好的锚（比白色名牌好）：
      · 四张卡上的编号徽章**完全一样**，不受卡面美术影响
      · 一个个都是孤立的小圆，不会和卡面连成一片
      · 徽章上的数字直接告诉你这是第几号位 → 行列映射不需要猜
      · 尺寸固定，跨分辨率只随窗口等比变化

    为什么是多档尝试
    ----------------
    "近白"的阈值在不同局里差异很大（背景有亮天空、有暗街道）。
    与其找一个万能阈值，不如**试几档，让"四张卡尺寸一致 + y 单调 + 间距均匀"
    这个一致性检验来挑**——判据本身不依赖阈值，这正是它稳的原因。
    """
    if roi is None or roi.size == 0:
        return []
    # 按"经验上最常奏效"的顺序试；一致性检验过关就立刻返回（通常是第一档）
    combos = [(175.0, 92.0, 0.006), (175.0, 92.0, 0.015),
              (168.0, 85.0, 0.010), (190.0, 88.0, 0.018),
              (210.0, 94.0, 0.026), (168.0, 80.0, 0.006),
              (190.0, 80.0, 0.010), (210.0, 88.0, 0.015)]
    best, best_score = [], 0.0
    for (v_floor, pctl, k_frac) in combos:
        cand = _disc_once(roi, v_floor, pctl, k_frac,
                          size_frac, need_dark_center)
        sc = _consistent(cand, expect)
        if sc > best_score:
            best, best_score = cand, sc
            if sc >= expect:            # 4 个全找到且一致：不用再试
                break
    return best[:expect] if best_score > 0 else []


def locate_cards(img, bands=None, expect=4):
    """技能卡：以"编号圆"为锚，向右推卡面，向下推名牌，卡面右下推伤害胶囊。

    整条推导链的每一步都是「上游实测值 × 比例」：
        badge 直径 d  ->  卡面高 ≈ 4.6d，卡面宽 ≈ 4.3d
                      ->  名牌 = 卡面底部，高 ≈ 0.85d
                      ->  胶囊 = 卡面右下，宽 ≈ 2.2d，高 ≈ 0.95d
    比例常数由多帧实测得出，与窗口分辨率无关。
    """
    bands = bands or DEFAULT_BANDS
    bx, by, bw, bh = band_box(img, bands["cards"])
    roi = img[by:by + bh, bx:bx + bw]
    badges = find_disc_badges(roi)
    out = {"badges": [], "cards": [], "nameplates": []}
    if not badges:
        return out
    ds = np.array([b["d"] for b in badges], dtype=np.float32)
    med_d = float(np.median(ds))
    keep = [b for b in badges if abs(b["d"] - med_d) <= 0.30 * med_d]
    # 只保留纵向单调的一组，最多 expect 个
    rows = []
    prev = -1
    for b in keep:
        cy = b["y"] + b["h"] / 2.0
        if cy <= prev:
            continue
        prev = cy
        rows.append(b)
    rows = rows[:expect]
    if len(rows) < 2:
        return out
    ys = [b["y"] + b["h"] / 2.0 for b in rows]
    gaps = np.diff(ys)
    grid_ok = True
    if len(gaps) and gaps.mean() > 0:
        grid_ok = bool(gaps.std() <= 0.55 * gaps.mean())
    out["grid_ok"] = grid_ok

    # 缺行就按间距补：卡列是等距的，补出来的位置比"整组丢弃"有用得多。
    # x 也按线性插值补（卡列是阶梯状略微右移的，直接用中位数会整体偏左）。
    if len(rows) >= 2 and len(rows) < expect and grid_ok:
        step = float(np.median(gaps))
        dd = float(np.median([b["d"] for b in rows]))
        x0b = float(rows[0]["x"])
        dxb = (float(rows[-1]["x"]) - x0b) / max(1, len(rows) - 1)
        y0c = ys[0]
        for i in range(len(rows), expect):
            yc = y0c + i * step
            if yc + dd / 2.0 > bh:
                break
            xb = x0b + i * dxb
            rows.append({"x": int(round(xb)), "y": int(round(yc - dd / 2.0)),
                         "w": int(round(dd)), "h": int(round(dd)),
                         "d": dd, "fill": 0.0, "dark": 0.0,
                         "virtual": True})
    d = med_d
    for i, b in enumerate(rows):
        bimg = {"x": b["x"] + bx, "y": b["y"] + by, "w": b["w"], "h": b["h"],
                "index": i + 1, "d": round(d, 1)}
        out["badges"].append(bimg)
        # 卡面：编号圆右侧 1.30d 起（实测：编号圆到卡面左沿 ≈ 1.2~1.35d）
        cx = b["x"] + int(round(1.30 * d))
        cw = int(round(4.30 * d))
        chh = int(round(4.60 * d))
        cy = int(round(b["y"] + b["h"] / 2.0 - chh * 0.42))
        card = {"x": cx + bx, "y": cy + by, "w": cw, "h": chh, "index": i + 1}
        # 伤害胶囊：卡面右下
        cap = {"x": card["x"] + int(round(cw * 0.40)),
               "y": card["y"] + int(round(chh * 0.68)),
               "w": int(round(cw * 0.58)),
               "h": int(round(chh * 0.24))}
        card["capsule"] = cap
        # 名牌：卡面正下方
        nb = {"x": card["x"] + int(round(cw * 0.02)),
              "y": card["y"] + chh + int(round(0.06 * d)),
              "w": int(round(cw * 0.92)),
              "h": int(round(0.90 * d))}
        card["nameplate"] = nb
        out["cards"].append(card)
        out["nameplates"].append(nb)
    return out
