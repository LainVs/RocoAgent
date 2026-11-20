# -*- coding: utf-8 -*-
"""digitmat.py —— 血条数字匹配器（字形模板，<1ms）

为什么要自己认字
----------------
血条是 7~20px 高的一条，"数字"在里面只占 7~12px。像素测量在这个尺度上
天然有 ±4% 歧义（圆角端），而 OCR 一次 0.2~0.4s、成功率 ~50%。

但这两个问题是**互补**的：
    · OCR 慢、但离线跑几千次无所谓 → 当标注器
    · 字形匹配快、但需要模板     → 当在线通道
所以：离线用 OCR 建模板（`scripts/build_digit_templates.py`），
在线只做 16x16 的 NCC 匹配。

用法
----
    import digitmat
    r, text, conf = digitmat.read_bar_digits(img, box, "my")
    # -> 0.9517, "397/417", 0.93
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import matdet  # noqa: E402  (几何原语统一在 matdet，避免两处实现漂移)

GLYPH = matdet.GLYPH
white_mask = matdet.white_mask
split_glyphs = matdet.split_glyphs
norm_glyph = matdet.norm_glyph
TPL_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "assets", "digits_hp", "templates.npz")

_TPL = None          # (N,256) float32，已中心化 + 单位化
_LAB = None          # numpy array of str


def templates():
    global _TPL, _LAB
    if _TPL is None:
        try:
            d = np.load(TPL_PATH)
            t = d["tpl"].astype(np.float32)
            if t.ndim == 3:
                t = t.reshape(t.shape[0], -1)
            t = t - t.mean(axis=1, keepdims=True)
            nrm = np.linalg.norm(t, axis=1, keepdims=True)
            nrm[nrm < 1e-6] = 1.0
            _TPL = t / nrm
            _LAB = d["labels"]
        except Exception:
            _TPL = np.zeros((0, GLYPH[0] * GLYPH[1]), dtype=np.float32)
            _LAB = np.zeros((0,), dtype="<U1")
    return _TPL, _LAB





def _ncc(a, b):
    a = a.reshape(-1) - a.mean()
    b = b.reshape(-1) - b.mean()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-6 or nb < 1e-6:
        return 0.0
    return float(a.dot(b) / (na * nb))


def classify_topk(g, k=3, knn=5):
    """单个字形 -> [(label, score)]，按分降序。

    **k-NN 投票**：模板库存的是全部标注样本（每类几十个），
    取最像的 knn 个投票，比"每类一个质心"稳得多
    （实测质心版把 `354` 认成 `95`、`100` 认成 `78`）。
    """
    T, L = templates()
    if T.shape[0] == 0:
        return []
    a = np.asarray(g, dtype=np.float32).reshape(-1)
    a = a - a.mean()
    na = float(np.linalg.norm(a))
    if na < 1e-6:
        return []
    sims = T.dot(a / na)
    order = np.argsort(sims)[::-1][:max(k, knn)]
    votes, best = {}, {}
    for i in order[:knn]:
        lab = str(L[i])
        votes[lab] = votes.get(lab, 0.0) + float(sims[i])
        if lab not in best or sims[i] > best[lab]:
            best[lab] = float(sims[i])
    ranked = sorted(votes.items(), key=lambda kv: -kv[1])[:k]
    return [(lab, best[lab]) for lab, _ in ranked]


def _digit_candidates(img, box, side, x_pad_frac=0.10):
    """切字 + 每个字给 top-k 候选。

    **先放大再切字**：1280 帧里血条只有 9px、字高 6~7px，
    直接在原尺度切分会把笔画粘/断，切出来的字数经常不对（7 个字变 10 个）。
    按 `matdet.digit_upscale` 放大到字高 ~28px 后再走同一套白字 mask 与切分。

    横向只留 10% 余量：试过放宽到 55%，结果把等级文字、pip 一起切进来。
    """
    crop, s = matdet.digit_strip(img, box, pad_frac=x_pad_frac)
    if crop is None:
        return None
    h = box["h"] if isinstance(box, dict) else box[3]
    m = white_mask(crop)
    if m.sum() < 16:
        return None
    gl = split_glyphs(m, min_gap=max(2, int(round(h * s * 0.10))))
    if not (2 <= len(gl) <= 12):
        return None
    out = []
    for g in gl:
        gimg = norm_glyph(m, g)
        if gimg is None:
            continue
        cands = classify_topk(gimg, 3)
        if not cands:
            return None
        x0, x1, y0, y1 = g
        sub = m[y0:y1 + 1, x0:x1 + 1]
        diag = 0.0
        ys, xs = np.nonzero(sub)
        if ys.size >= 6:
            ys = ys.astype(np.float32)
            xs = xs.astype(np.float32)
            if xs.std() > 1e-3 and ys.std() > 1e-3:
                diag = abs(float(np.corrcoef(xs, ys)[0, 1]))
        out.append((cands, diag, (x1 - x0 + 1) / float(max(1, y1 - y0 + 1))))
    # ---- 分隔符用**几何**判定，不靠分类、也不靠宽高比
    # 斜杠是一撇：像素坐标的 x-y 相关系数接近 ±1（数字是横竖结构，接近 0）。
    # 实测宽高比不可靠（`7` 和 `/` 差不多），相关系数可靠得多。
    if len(out) >= 3:
        n = len(out)
        lo = max(1, n // 3)
        hi = min(n - 1, (2 * n) // 3)
        if hi <= lo:
            lo, hi = 1, n - 1
        seg = [out[k][1] for k in range(lo, hi + 1)]
        k = lo + int(np.argmax(seg))
        if side == "my" and out[k][1] > 0.75 and 0 < k < n - 1:
            out[k] = ([("/", 1.0)], out[k][1], out[k][2])
        elif side == "foe" and out[-1][2] > 0.85 and out[-1][1] < 0.5:
            out[-1] = ([("%", 1.0)], out[-1][1], out[-1][2])
    return [c for c, _d, _a in out]


def _struct_bonus(chars):
    """结构分：合法写法（`数字/数字`、`数字%`）加分，乱七八糟的序列扣分。"""
    if not chars:
        return -0.5
    b = 0.0
    if chars[0] in ("/", "%"):
        b -= 0.35                    # 分隔符不可能在最前面
    if chars[-1] == "/":
        b -= 0.35
    sep = [i for i, c in enumerate(chars) if c in ("/", "%")]
    if len(sep) == 1:
        i = sep[0]
        a = chars[:i]
        z = chars[i + 1:]
        if chars[i] == "/":
            if a and z and all(c.isdigit() for c in a) and all(c.isdigit() for c in z):
                b += 0.20
                if len(a) == len(z):
                    b += 0.10        # 400/417 这种"位数相同"最常见
        else:
            if a and all(c.isdigit() for c in a) and not z:
                b += 0.20
    elif len(sep) > 1:
        b -= 0.40
    if len(chars) > 9:
        b -= 0.20
    return b


def _enumerate(cands, side, prior, top=400):
    """在"每个字的 top-k 候选"里做束搜索，用**像素比例当先验**消歧。

    这就是两条通道互补的地方：
        · 字形给精确值，但会在 4/7、4/9、0/8 上混淆
        · 像素比例 ±5%，粗糙但**不会认错量级**
    目标函数 = 字分均值 + 结构分 - 6 * |比例 - 先验|（1% 误差扣 0.06 分）
    """
    n = len(cands)
    beam = [([], 0.0)]
    for i in range(n):
        nxt = []
        for labels, sc in beam:
            for (L, s) in cands[i]:
                nxt.append((labels + [L], sc + s))
        nxt.sort(key=lambda t: -t[1])
        beam = nxt[:top]
    best = None
    for (labels, sc) in beam:
        r = _interpret(labels, side)
        if r is None:
            continue
        conf = sc / max(1, n)
        pen = 0.0 if prior is None else 6.0 * abs(r - prior)
        score = conf + _struct_bonus(labels) - pen
        if best is None or score > best[0]:
            best = (score, r, "".join(labels), conf)
    if best is None:
        return None, "", 0.0, -9.9
    return best[1], best[2], best[3], best[0]


def _interpret(chars, side):
    """字符序列 -> 比例；结构不合法返回 None。"""
    if not chars:
        return None
    idx = [i for i, c in enumerate(chars) if c in ("/", "%")]
    if idx:
        cut = idx[-1] if ("/" in chars) else idx[0]
        if chars[cut] == "/":
            a = "".join(c for c in chars[:cut] if c.isdigit())
            b = "".join(c for c in chars[cut + 1:] if c.isdigit())
            if a and b:
                cur, mx = int(a), int(b)
                if 0 < mx <= 5000 and 0 <= cur <= mx:
                    return cur / float(mx)
            return None
        body = "".join(c for c in chars[:cut] if c.isdigit())
        if body:
            n = int(body)
            if 0 <= n <= 100:
                return n / 100.0
        return None
    digits = "".join(c for c in chars if c.isdigit())
    if not digits:
        return None
    if side == "foe":
        n = int(digits)
        return (n / 100.0) if 0 <= n <= 100 else None
    # 我方没有斜杠时：按"两半"猜（400/417 -> 400417）
    m = len(digits)
    if m % 2 == 0:
        a, b = digits[:m // 2], digits[m // 2:]
        if a and b and int(a) <= int(b) and 0 < int(b) <= 5000:
            return int(a) / float(int(b))
    elif m >= 5:
        a, b = digits[:m // 2], digits[m // 2 + 1:]
        if a and b and int(a) <= int(b) and 0 < int(b) <= 5000:
            return int(a) / float(int(b))
    return None


def fuse(px, dg, tol=0.03):
    """像素通道与字形通道融合 -> (ratio, source, agree)。

    规则（按可靠性，不是按平均值）：
      · 字形读出来了 -> 用字形（它是精确值），像素只当"有没有读错量级"的守卫
      · 两者差 > tol -> 标记 agree=False，上层可以拒绝出招
      · 字形没读出来 -> 退回像素（±5%），标记置信度低
    """
    if dg is not None and px is not None:
        if abs(dg - px) <= tol:
            return dg, "digit+px", True
        return dg, "digit(px disagrees)", False
    if dg is not None:
        return dg, "digit", None
    if px is not None:
        return px, "px", None
    return None, "none", None


def read_hp(img, box, side, px_ratio=None):
    """便捷入口：字形优先，像素兜底。返回 dict。

    px_ratio 由调用方传入（matdet.measure_fill 的结果），避免这里再依赖 matdet。
    """
    r, text, conf = read_bar_digits(img, box, side, prior=px_ratio)
    ratio, src, agree = fuse(px_ratio, r)
    return {"ratio": ratio, "text": text, "conf": conf,
            "source": src, "agree": agree, "px": px_ratio, "digit": r}


def read_bar_digits(img, box, side, prior=None, min_conf=0.45):
    """读血条数字 -> (ratio, text, conf)。

    prior: 像素通道给的粗比例（可选）。给了就用它消歧，非常有效。
    读不准返回 None —— 绝不猜。
    """
    cands = _digit_candidates(img, box, side)
    if not cands:
        return None, "", 0.0
    # 允许丢掉首/尾 0~2 个字形：条边缘常有"碎片字形"（左端高光、右侧装饰），
    # 多一个就会把 `cur/max` 判成非法（实测 412/412 被读成 1412/412）。
    best = None
    for dl in (0, 1, 2):
        for dr in (0, 1, 2):
            if dl + dr >= len(cands):
                continue
            sub = cands[dl:len(cands) - dr] if dr else cands[dl:]
            if len(sub) < 2:
                continue
            r, text, conf, sc = _enumerate(sub, side, prior)
            if r is None:
                continue
            sc -= 0.55 * (dl + dr)          # 丢字要付代价
            if best is None or sc > best[0]:
                best = (sc, r, text, conf)
    if best is None:
        r, text, conf, _sc = _enumerate(cands, side, prior)
        return (r, text, conf) if (r is not None and conf >= min_conf) else (None, text, conf)
    _sc, r, text, conf = best
    if conf < min_conf:
        return None, text, conf
    return r, text, conf
