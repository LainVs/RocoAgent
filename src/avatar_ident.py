# -*- coding: utf-8 -*-
"""avatar_ident.py —— 用 `assets/pet_avatars` 当「这是谁」的识别通道。

为什么可行
----------
`assets/pet_avatars/<图鉴id>.png` 和实机 PvP HUD 上的头像**是同一张插画**，
HUD 只是做了约 1:1 的居中裁切（实测最优缩放 0.97，见
`docs/img/avatar_align.png`）。因此把头像按同样的几何归一化之后，可以直接
做相关匹配，覆盖 639 个图鉴 id —— 比现有 162 张已标注 HUD 模板大得多。

和别的通道怎么分工
------------------
本模块只做「打分」，融合由调用方决定（`foe_ident` / `robot._check_foe`）。
实测置信度标定（141 张实机真值）：

    score >= 0.60 且 margin >= 0.03   精确率 100%，覆盖 36%
    score >= 0.55 且 margin >= 0.03   精确率 96.6%，覆盖 41%
    score >= 0.45                     精确率 75.5%，覆盖 72%

所以分三档：high 才允许单独定名；low 只参与排序；none 不投票。
异色/炫彩不在头像库里（只有普通配色），会自然落到 low/none，由特性面板兜底。

关键预处理（踩过的坑）
----------------------
1. **不能直接用 BGRA 的 RGB**：透明像素的 RGB 是未定义值，大片黑/白会压过主体。
   必须先按 alpha 合成到假想背景。
2. **HUD 背景占画面约 40%**（场景 + 属性图标），用中心椭圆 mask 去掉。
3. **缩放关系是 1:1**，不是放大；最初按 1.5~2.0 倍搜索时正确头像反而排在后面。
4. **通道顺序**：`Screen.grab_client()` 返回 RGB，模板来自 PNG（BGR）。
   查询必须 RGB->BGR，否则 R/B 错配会明显掉分。

接口
----
    available()                       -> bool
    score_map(img, side="foe")        -> ndarray(len(ids),)  每个物种的最高分
    identify(img, side="foe", candidates=None)
        -> [{"id","name","score","margin","conf"}, ...]
    identify_screen(screen, side="foe", candidates=None)
    bank_info(side="foe")             -> {"n_ids","n_views","built","path"}

    side 只影响几何：foe 框 84x76，self 框 76x80，宽高比不同，所以要两套模板。

缓存
----
模板银行是派生数据，落在 `data/cache/avatar_bank_<side>.npz`，按
`assets/pet_avatars` 的文件数 + 最大 mtime 失效。首次构建 ~1.7 s。
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import threading
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AV_DIR = os.path.join(ROOT, "assets", "pet_avatars")
CACHE_DIR = os.path.join(ROOT, "data", "cache")
DB = os.path.join(ROOT, "data", "roco.sqlite3")

S = 32                       # 归一化边长
# 几何：HUD 是「比 alpha 外接框更高」的窗口，实测最优 z_h≈1.37、中心 (0.5w, 0.42h)。
# 下面的 12 个视角（4 缩放 × 3 位置 × 1 背景）是消融出来的平衡点：
# plain 73.2% / all 66.7% / variant 56.9%，银行 90MB、构建 2.8s。
# 减去偏移只剩 71.1%，减少缩放掉 2~3 点，所以保留 3 个水平偏移。
ZOOMS = (1.0, 1.2, 1.4, 1.6)
CY = 0.42
OFFS = ((0.0, 0.0), (-0.05, 0.0), (0.05, 0.0))
BGS = ((14, 14, 18),)
ASPECT = {"foe": 84.0 / 76.0, "self": 76.0 / 80.0}

# 置信度分档（数值来自 docs/PLAN_AVATAR_IDENT.md §五）
HIGH_SCORE = 0.60
HIGH_MARGIN = 0.03
LOW_SCORE = 0.50

BANK_VERSION = 3

_LOCK = threading.Lock()
_BANKS = {}          # side -> {"ids","AT","owner","name","n_views"}
_NAMES = {"at": 0.0, "id2name": {}, "name2id": {}}
_SIG = {"at": 0.0, "sig": None}
SIG_TTL = 30.0       # 目录指纹缓存：10Hz 轮询时不要每次都 stat 639 个文件


# ---------------------------------------------------------------- 基础
def _mask():
    m = np.zeros((S, S), np.uint8)
    cv2.ellipse(m, (S // 2, int(S * 0.47)), (int(S * 0.44), int(S * 0.46)),
                0, 0, 360, 255, -1)
    return m


MASK = _mask()


def _imread_any(path, flags=cv2.IMREAD_UNCHANGED):
    """cv2.imread 在 Windows 上按 ANSI 解析路径，中文目录会失败。"""
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        if buf.size == 0:
            return None
        return cv2.imdecode(buf, flags)
    except (OSError, ValueError):
        return None


def _trim_alpha(im, t=8):
    a = im[:, :, 3]
    ys, xs = np.nonzero(a > t)
    if not len(xs):
        return im
    return im[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _composite(view, bg):
    a = (view[:, :, 3].astype(np.float32) / 255.0)[:, :, None]
    return view[:, :, :3].astype(np.float32) * a + \
        np.array(bg, np.float32) * (1 - a)


def _descriptor(bgr):
    """BGR(float) -> 中心椭圆内的逐通道零均值单位向量。"""
    f = cv2.resize(bgr.astype(np.float32), (S, S),
                   interpolation=cv2.INTER_AREA)
    mu = f[MASK > 0].reshape(-1, 3).mean(axis=0)
    v = (f - mu) * (MASK[:, :, None] > 0)
    v = v.ravel()
    return v / (np.linalg.norm(v) + 1e-6)


def _views_of(bgra, aspect):
    im = _trim_alpha(bgra)
    h, w = im.shape[:2]
    out = []
    for z in ZOOMS:
        wh = max(10.0, h / z)
        ww = wh * aspect
        if ww > w:
            ww, wh = float(w), w / aspect
        for ox, oy in OFFS:
            cx = w * (0.5 + ox)
            cy = h * CY
            x0 = min(max(0, int(round(cx - ww / 2))), max(0, w - int(ww)))
            y0 = min(max(0, int(round(cy - wh / 2))), max(0, h - int(wh)))
            view = im[y0:y0 + int(wh), x0:x0 + int(ww)]
            if view.shape[0] < 10 or view.shape[1] < 10:
                continue
            for bg in BGS:
                out.append(_descriptor(_composite(view, bg)))
    return out


def _species_names():
    """id2name / 规范化名字 -> id，进程内缓存 5 分钟。"""
    now = time.time()
    if _NAMES["id2name"] and now - _NAMES["at"] < 300.0:
        return _NAMES["id2name"], _NAMES["name2id"]
    id2name, name2id = {}, {}
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    try:
        for r in db.execute("SELECT id,name,implemented,total FROM species "
                            "ORDER BY implemented DESC, total DESC"):
            sid, nm = int(r["id"]), r["name"]
            if not nm:
                continue
            id2name.setdefault(sid, nm)
            key = normalize(nm)
            if key and key not in name2id:
                name2id[key] = sid
    finally:
        db.close()
    _NAMES.update({"at": now, "id2name": id2name, "name2id": name2id})
    return id2name, name2id


def normalize(name):
    """去掉括号后缀和「首领-」前缀，只用于对齐名字。"""
    n = re.sub(r"[（(][^）)]*[）)]", "", name or "")
    return n.replace("首领-", "").strip()


# ---------------------------------------------------------------- 银行
def _signature():
    now = time.time()
    if _SIG["sig"] is not None and now - _SIG["at"] < SIG_TTL:
        return _SIG["sig"]
    files = glob.glob(os.path.join(AV_DIR, "*.png"))
    if not files:
        return None
    ms = max(os.path.getmtime(f) for f in files)
    sig = "%d-%d" % (len(files), int(ms))
    _SIG.update({"at": now, "sig": sig})
    return sig


def _bank_path(side):
    return os.path.join(CACHE_DIR, "avatar_bank_%s.npz" % side)


def _load_bank_file(side, sig):
    p = _bank_path(side)
    if not os.path.exists(p):
        return None
    try:
        with np.load(p) as z:
            if str(z["sig"]) != sig or int(z["version"]) != BANK_VERSION \
                    or int(z["S"]) != S:
                return None
            return {"ids": z["ids"].astype(np.int32),
                    "AT": z["AT"].astype(np.float32),
                    "owner": z["owner"].astype(np.int32)}
    except Exception:
        return None


def _build_bank(side, sig):
    aspect = ASPECT.get(side, ASPECT["foe"])
    ids, vecs, owner = [], [], []
    for f in sorted(glob.glob(os.path.join(AV_DIR, "*.png"))):
        stem = os.path.splitext(os.path.basename(f))[0]
        if not stem.isdigit():
            continue
        bgra = _imread_any(f)
        if bgra is None or bgra.ndim != 3 or bgra.shape[2] != 4:
            continue
        sid = int(stem)
        views = _views_of(bgra, aspect)
        if not views:
            continue
        ids.append(sid)
        for v in views:
            vecs.append(v)
            owner.append(len(ids) - 1)
    if not ids:
        return None
    A = np.array(vecs, np.float32)
    AT = np.ascontiguousarray(A.T)          # (dim, n_views)：矩阵乘不用再复制
    bank = {"ids": np.array(ids, np.int32),
            "AT": AT,
            "owner": np.array(owner, np.int32)}
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = _bank_path(side) + ".tmp.npz"
        np.savez(tmp, ids=bank["ids"], AT=bank["AT"], owner=bank["owner"],
                 sig=np.array(sig), version=np.array(BANK_VERSION),
                 S=np.array(S))
        os.replace(tmp, _bank_path(side))
    except Exception:
        pass
    return bank


def bank(side="foe"):
    """取（必要时构建）模板银行。线程安全。"""
    side = "self" if side == "self" else "foe"
    sig = _signature()
    if sig is None:
        return None
    hit = _BANKS.get(side)
    if hit is not None and hit.get("sig") == sig:
        return hit
    with _LOCK:
        hit = _BANKS.get(side)
        if hit is not None and hit.get("sig") == sig:
            return hit
        data = _load_bank_file(side, sig) or _build_bank(side, sig)
        if data is None:
            return None
        id2name, _ = _species_names()
        data.update({"sig": sig, "side": side,
                     "name": [id2name.get(s) for s in data["ids"]]})
        _BANKS[side] = data
        return data


def available():
    return bank("foe") is not None


def bank_info(side="foe"):
    b = bank(side)
    if not b:
        return {"available": False}
    return {"available": True, "n_ids": int(len(b["ids"])),
            "n_views": int(b["AT"].shape[1]), "dim": int(b["AT"].shape[0]),
            "path": _bank_path(side), "sig": b["sig"]}


# ---------------------------------------------------------------- 打分
def _score(img, b, is_rgb=True):
    if img is None or getattr(img, "size", 0) == 0:
        return None
    if img.ndim == 3 and img.shape[2] == 4:
        a = img[:, :, 3:4].astype(np.float32) / 255.0
        bgr = img[:, :, :3].astype(np.float32) * a
    else:
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        bgr = img[:, :, ::-1] if is_rgb else img

    q = _descriptor(np.ascontiguousarray(bgr))
    base = q.reshape(S, S, 3)
    acc = None
    for dy in (-2, 0, 2):
        for dx in (-2, 0, 2):
            m = np.roll(np.roll(base, dy, axis=0), dx, axis=1)
            mu = m[MASK > 0].reshape(-1, 3).mean(axis=0)
            m = (m - mu) * (MASK[:, :, None] > 0)
            f = m.ravel()
            f = f / (np.linalg.norm(f) + 1e-6)
            r = f @ b["AT"]
            acc = r if acc is None else np.maximum(acc, r)

    # owner 是「第几个 id」，不是 id 本身
    out = np.full(len(b["ids"]), -1.0, np.float32)
    np.maximum.at(out, b["owner"], acc)
    return out


def score_map(img, side="foe", is_rgb=True):
    """一张 HUD 头像裁图 -> 每个物种 id 的最高分。

    参数
    ----
    img    : HxWx3（Screen.grab_client 返回 RGB）或 HxWx4
    is_rgb : True 时先 RGB->BGR；用 cv2.imread 读进来的图传 False。

    返回与 bank["ids"] 对齐的 ndarray；银行不可用时返回 None。
    """
    b = bank(side)
    if b is None:
        return None
    return _score(img, b, is_rgb)


def identify(img, side="foe", candidates=None, topk=5, is_rgb=True,
             margin=0):
    """头像裁图 -> [{"id","name","score","margin","conf"}, ...]（降序）。

    candidates 给名字列表时只在其中排序（线上应传属性过滤后的候选池）。
    conf: high / low / none —— 见模块文档的三档阈值。

    margin: 传进来的图如果带了搜索边（例如 robot 的 104x96 裁图），
    用这个参数把边裁掉。**银行是在紧框（84x76 / 76x80）上标定的**，
    带 10px 边会把头部占比压小，实测分数从 0.86 掉到 0.43。
    """
    b = bank(side)
    if b is None:
        return []
    if margin and img is not None and getattr(img, "ndim", 0) >= 2:
        h, w = img.shape[:2]
        if h > 2 * margin and w > 2 * margin:
            img = img[margin:h - margin, margin:w - margin]
    sc = _score(img, b, is_rgb)
    if sc is None:
        return []
    names = b["name"]

    idx = list(range(len(b["ids"])))
    if candidates:
        want = {normalize(n) for n in candidates}
        keep = [i for i in idx if normalize(names[i]) in want]
        if keep:
            idx = keep
        # 候选里一个都没有：不要退化成全库乱猜，返回空
        else:
            return []

    order = sorted(idx, key=lambda i: -sc[i])[:max(1, topk)]
    top = float(sc[order[0]]) if order else 0.0
    second = float(sc[order[1]]) if len(order) > 1 else 0.0
    margin = top - second
    out = []
    for rank, i in enumerate(order):
        conf = "none"
        if rank == 0:
            if top >= HIGH_SCORE and margin >= HIGH_MARGIN:
                conf = "high"
            elif top >= LOW_SCORE:
                conf = "low"
        out.append({"id": int(b["ids"][i]), "name": names[i],
                    "score": round(float(sc[i]), 4),
                    "margin": round(margin, 4) if rank == 0 else None,
                    "conf": conf})
    return out


def identify_screen(screen, side="foe", candidates=None, topk=5,
                    margin=0):
    """直接按 HUD 框抓图并识别。screen 需要 grab_client / client_size。

    **默认 margin=0**：银行是在紧框上标定的。`portrait_onnx.hud_crop` 用
    margin=10 是它自己的训练约定，两套通道不要共用同一个裁框。
    """
    try:
        import portraits
        box_fn = portraits.foe_box_for if side != "self" else portraits.box_for
        cw, ch = getattr(screen, "client_size", None) or (2560, 1600)
        img = screen.grab_client(box_fn(cw, ch, margin=margin))
    except Exception:
        return []
    if img is None:
        return []
    return identify(img, side=side, candidates=candidates, topk=topk,
                    is_rgb=True)


# ---------------------------------------------------------------- 自检
def _selftest():
    print("bank:", bank_info("foe"))
    print("bank(self):", bank_info("self"))
    import portrait_db as P

    def pool_of(name):
        """模拟线上：属性过滤后的候选池（这里用真值属性）。"""
        db = sqlite3.connect(DB)
        try:
            r = db.execute("SELECT type_names FROM species WHERE name=? "
                           "ORDER BY implemented DESC, total DESC LIMIT 1",
                           (name,)).fetchone()
            want = set(json.loads(r[0] or "[]")) if r else set()
            if not want:
                return None
            out = []
            for (nm, tn) in db.execute("SELECT name,type_names FROM species"):
                try:
                    ts = set(json.loads(tn or "[]"))
                except ValueError:
                    continue
                if want <= ts:
                    out.append(nm)
            return out
        finally:
            db.close()

    ok = 0
    for stem in ("音速犬.png", "寂灭骨龙.png", "冰钻布鲁斯.png", "水灵.png"):
        p = os.path.join(ROOT, "assets", "portraits_foe", stem)
        img = _imread_any(p, cv2.IMREAD_COLOR)
        if img is None:
            continue
        sid = P.species_of(stem, "foe")
        want = P.species_name(sid)
        res = identify(img, side="foe", candidates=pool_of(want), is_rgb=False)
        got = res[0] if res else {}
        hit = normalize(got.get("name")) == normalize(want)
        ok += 1 if hit else 0
        print("  %-14s -> %-14s %.3f %-4s %s"
              % (stem, got.get("name"), got.get("score") or 0,
                 got.get("conf"), "OK" if hit else "**MISS**"))
    print("SELFTEST %d/4" % ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
