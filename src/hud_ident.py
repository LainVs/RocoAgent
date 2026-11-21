# -*- coding: utf-8 -*-
"""hud_ident.py —— 从战斗 HUD 直接读「这是谁」（不依赖机器人进程）。

为什么单独一个模块
------------------
`standalone.py`（独立计算器）原来只有立绘 ONNX 一条通道，实测在静止画面上
置信度 1.3e-07（等于瞎猜），所以计算器一打开就显示"未知"。

但 HUD 上其实有三个信号，其中两个是**改名也改不掉**的硬约束：

    [属性图标:龙+幽] [头像] 寂灭骨龙 ♂ 60级  [血条 87%]

| 信号 | 玩家能否改 | 实测可靠性 | 歧义度 |
|---|---|---|---|
| 属性图标 | 不能 | 图标清晰，留一验证 43/43 | 龙+幽=4 只，幽+恶=2 只 |
| 头像 | 不能 | 模板 0.60 / 立绘 conf 低 | 敌方模板库 95 只 |
| 名字文字 | **能改（昵称）** | OCR 64%（108 帧回归） | 需 nickname_map 反查 |

**⚠ 属性名不要靠肉眼判断图标。** 我第一眼把寂灭骨龙的红色恶魔脸图标看成
「恶系」，但数据库和 roco-cal 的 `elements` 三方一致都是 **龙+幽**
（U+9F99 + U+5E7D）。所以图标标签一律由 `scripts/build_type_icons.py`
从数据库**自监督**生成，绝不手写。

**名字不能当种族名。** 排位里有大量改名精灵（nickname_map 里已记录 22 条：
「漆黑的冥古龙」「黑武士」「玲珑的妙脆角」…）。名字只能当
「昵称 -> 种族」的映射键，且必须先确认过才写入。

本模块只做**读**，不做判断。融合逻辑在 `foe_ident.py`。

坐标（2560x1600 客户区基准，全部在真实对战帧上验证过）
------------------------------------------------------
    MY_NAME_BOX   = (130, 40, 260, 60)    ±4px 就 OCR 空，必须滑动搜索
    FOE_NAME_BOX  = (2225, 50, 210, 55)   config.py 已有
    MY_ICON_CENTERS  = [(56,98), (72,124)]     Hough 实测
    FOE_ICON_CENTERS = [(2142,96), (2158,124)] Hough 实测

第二个图标不存在时（单属性精灵）该位置是空的，读出来是 None。
"""
from __future__ import annotations

import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

ROOT = os.path.dirname(_HERE)

# ------------------------------------------------------------------ 坐标
MY_NAME_BOX = (130, 40, 260, 60)
FOE_NAME_BOX = (2225, 50, 210, 55)

# 属性图标中心（Hough 圆检测在 093446_00.jpg 上实测）。
# 两个图标沿对角线排列：第一个在左上，第二个在右下。
MY_ICON_CENTERS = ((56, 98), (72, 124))
FOE_ICON_CENTERS = ((2142, 96), (2158, 124))
ICON_R = 15

# 名字条 OCR 对坐标极敏感：同一帧 (126,36,270,60) 读成 "604"，
# (130,40,260,60) 才读出 "银月狼王"。所以在 ±8px 内滑动取最佳。
SEARCH_OFFSETS = tuple(range(-8, 9, 4))

# ------------------------------------------------------------------ 属性
# 18 个属性的中文名（与 DB 的 type_names 一致）
TYPE_NAMES = ("普通", "火", "水", "草", "电", "冰", "武", "毒", "地", "翼",
              "萌", "幻", "虫", "幽", "机械", "龙", "恶", "光")

TEMPLATE_PATH = os.path.join(ROOT, "assets", "type_icons", "templates.npz")

# 官方 18 系别图标（198x198 RGBA，来自游戏解包，`assets/ext/type_icons/`）。
# 文件名是英文（type_dragon.png），映射到中文属性名。
OFFICIAL_ICON_DIR = os.path.join(ROOT, "assets", "ext", "type_icons")
OFFICIAL_MAP = {
    "normal": "普通", "fire": "火", "water": "水", "grass": "草",
    "electric": "电", "ice": "冰", "fighting": "武", "poison": "毒",
    "ground": "地", "wing": "翼", "cute": "萌", "illusion": "幻",
    "bug": "虫", "ghost": "幽", "machine": "机械", "dragon": "龙",
    "dark": "恶", "light": "光",
}

# 图标匹配门槛（NCC，越高越像）。
#
# 实测（2026-11-21，43 个「已确认是圆盘」的位置，用数据库真值验证）：
#   NCC >= 0.80    40 个，**100% 正确**
#   NCC 0.50~0.60   3 个，**0% 正确**（全是被头像遮挡的噪声）
#   NCC 0.60~0.80   0 个  <- 完全空白
#
# 分布是干净的双峰，所以门槛取 0.80。
# 之前取 0.40 会把 0.51 的噪声当成信号（实测把迪莫的光系读成"地"）。
MATCH_MIN = 0.80

_ocr = None
_icon_tpl = None


# ------------------------------------------------------------------ OCR
def _get_ocr():
    """惰性建 OCR（初始化 1~2 秒，别放模块级）。"""
    global _ocr
    if _ocr is None:
        import ocr_cn
        _ocr = ocr_cn.Ocr()
    return _ocr


def _species_vocab():
    """种族名词表（995 条）。读失败返回空表 -> 只返回原文不纠错。"""
    try:
        import foe_panel
        return foe_panel.default_vocab()["species"]
    except Exception:
        return []


def _read_box(screen, box):
    """裁一块 -> RGB 数组。失败返回 None。"""
    try:
        img = screen.grab_client(box)
    except Exception:
        return None
    if img is None or getattr(img, "size", 0) == 0:
        return None
    return img


def read_name(screen, side="foe", vocab=None, exact_box=None, fast=True):
    """读 HUD 名字文字。

    返回 (text, conf)：
        text  屏幕上的原文（**可能是玩家昵称，不是种族名**）
        conf  与种族词表的相似度（0~1）；词表为空时是 OCR 自身置信度

    为什么要滑动搜索
    ----------------
    实测（2026-11-21，logs/resultshots 108 帧）：
        框 (126,36,270,60) -> 读出 "604"        （错，框到了等级）
        框 (130,40,260,60) -> 读出 "银月狼王"   （对）
    只差 4 像素。写死单点等于赌运气。

    但 OCR 一次 1~2 秒，81 组滑动要两分钟，不能无脑全搜。
    所以 fast=True（默认）先用基准框试一次，**只有失败才滑动**。
    批量离线任务可以传 fast=False 做完整搜索。
    """
    ocr = _get_ocr()
    base = exact_box or (MY_NAME_BOX if side == "my" else FOE_NAME_BOX)

    def _try(box):
        img = _read_box(screen, box)
        if img is None:
            return ("", 0.0)
        try:
            res = ocr.read_region(img)
        except Exception:
            return ("", 0.0)
        if not res:
            return ("", 0.0)
        raw, ocr_conf = res[0][0], float(res[0][1])
        if vocab:
            import ocr_cn
            name, sc = ocr_cn.Ocr.match(raw, vocab)
            return (raw, float(sc)) if name else ("", 0.0)
        return (raw, ocr_conf)

    hit = _try(base)
    if hit[1] >= 0.60 or fast:
        return hit

    x, y, w, h = base
    best = hit
    for dy in SEARCH_OFFSETS:
        for dx in SEARCH_OFFSETS:
            if dx == 0 and dy == 0:
                continue
            got = _try((x + dx, y + dy, w, h))
            if got[1] > best[1]:
                best = got
            if best[1] >= 0.98:
                return best
    return best


# ------------------------------------------------------------------ 属性图标
def _load_icons():
    """加载 18 属性图标模板。

    优先用**官方图标**（`assets/ext/type_icons/`，198x198 RGBA，游戏解包），
    没有才退回自监督模板（`assets/type_icons/templates.npz`）。

    返回 {属性名: ndarray(30,30,3) RGB}；都没有时返回 {}。

    为什么官方图标优先
    ------------------
    自监督模板是从真实帧里提取的，受动画、压缩噪声、背景干扰，
    而且只覆盖名字能读出来的那几种属性（实测只有 龙/幽/光/火 四种）。
    官方图标 18 种齐全、边缘干净，直接缩放到 30x30 就能用。
    """
    global _icon_tpl
    if _icon_tpl is not None:
        return _icon_tpl

    _icon_tpl = {}

    # ---- 优先：官方 18 图标
    try:
        import glob as _glob
        files = _glob.glob(os.path.join(OFFICIAL_ICON_DIR, "type_*.png"))
        for p in files:
            stem = os.path.basename(p)[5:-4]          # type_dragon.png -> dragon
            name = OFFICIAL_MAP.get(stem)
            if not name:
                continue
            raw = np.fromfile(p, dtype=np.uint8)
            if raw.size == 0:
                continue
            import cv2
            img = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
            if img is None:
                continue
            # RGBA -> RGB，透明部分用中灰垫底（和 HUD 上的深色背景接近）
            if img.ndim == 3 and img.shape[2] == 4:
                bgr = img[:, :, :3].astype(np.float32)
                a = (img[:, :, 3:4].astype(np.float32) / 255.0)
                bgr = bgr * a + 60.0 * (1.0 - a)
                rgb = bgr[:, :, ::-1].astype(np.uint8)
            else:
                rgb = img[:, :, ::-1] if img.ndim == 3 else img
            _icon_tpl[name] = cv2.resize(rgb, (30, 30))
    except Exception:
        _icon_tpl = {}

    if _icon_tpl:
        return _icon_tpl

    # ---- 兜底：自监督模板
    try:
        data = np.load(TEMPLATE_PATH, allow_pickle=False)
        for key in data.files:
            _icon_tpl[key] = data[key]
    except Exception:
        pass
    return _icon_tpl


def _crop_icon(img, cx, cy, r=ICON_R):
    """从整帧里裁一个圆形的方框。越界返回 None。"""
    h, w = img.shape[:2]
    x0, y0 = cx - r, cy - r
    x1, y1 = cx + r, cy + r
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return None
    return img[y0:y1, x0:x1]


def read_types(screen, side="my"):
    """读 HUD 上的属性图标 -> ['幽', '恶']。

    读不出返回 []（调用方必须把 [] 当成"未知"而不是"无属性"）。

    单属性精灵的处理
    ----------------
    单属性时第二个图标位置是**空的**，但空位不一定干净：
    实测迪莫（**光系**，U+5149）的第二个位置被头像挡住，饱和度仍然很高
    （sat75=174，比真图标的 128 还高），所以「靠饱和度判空」会误判。

    正确做法：**看匹配分数 + 圆盘检测**。
    实测真图标 NCC 0.45~0.91，空位/头像碎片 <0.70 且圆盘不完整。

    另外：我方 HUD 的属性图标**只画在场精灵的**，第二个位置经常是头像的
    一部分（实测冰钻布鲁斯单属性冰，第一个位置读到的是头像边缘，
    被误判成"水" 0.692）。所以这里加一道圆盘完整性检查。
    """
    tpl = _load_icons()
    if not tpl:
        return []
    img = _read_box(screen, (0, 0, 2560, 1600))
    if img is None:
        return []

    centers = MY_ICON_CENTERS if side == "my" else FOE_ICON_CENTERS
    out = []
    for cx, cy in centers:
        patch = _crop_icon(img, cx, cy)
        if patch is None:
            continue
        if not _is_disc(patch):
            continue          # 不是完整的圆盘 -> 这个位置是空的
        name, score = _match_icon(patch, tpl)
        if name and score >= MATCH_MIN:
            out.append(name)
    return out


def _is_disc(patch):
    """判断这块是不是一个完整的图标圆盘（而不是头像/背景碎片）。

    判据：**内圈与外圈的亮度差**。图标是实心色盘压在 HUD 背景上，
    边界明显；头像碎片则是连续过渡，几乎没有边界。

    实测（2026-11-21 实盘，内圈 r<0.60 与外圈 0.85<r<1.05 的灰度均值差）：
        我方 冰钻布鲁斯 冰系图标       69.0
        敌方 仪式巨像   地系图标      101.3
        敌方 仪式巨像   幻系图标       70.6
        我方 第一个位置（头像碎片）     14.8   <- 唯一的非图标

    18 与 69 之间有 4 倍余量，判据很稳。

    为什么不用饱和度：幻系图标的淡紫底在 HUD 半透明蒙层下饱和度只有 48，
    和背景接近，会漏判。亮度差不受蒙层影响（蒙层是乘法，内外同比缩放）。
    """
    import cv2
    if patch is None or patch.size == 0:
        return False
    g = cv2.cvtColor(np.ascontiguousarray(patch).astype(np.uint8),
                     cv2.COLOR_RGB2GRAY).astype(np.float32)
    h, w = g.shape
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt((yy - h / 2.0 + 0.5) ** 2
                + (xx - w / 2.0 + 0.5) ** 2) / (min(h, w) / 2.0)
    inner = g[r < 0.60]
    ring = g[(r > 0.85) & (r < 1.05)]
    if inner.size < 20 or ring.size < 20:
        return False
    return abs(float(inner.mean()) - float(ring.mean())) > 18.0


def _match_icon(patch, tpl):
    """模板匹配一个图标，返回 (属性名, 分数)。

    为什么不能直接 resize 后比对
    ---------------------------
    官方图标 198x198，白色图案只占中间约 60%，周围是较宽的彩色底边；
    HUD 上截到的图标直径只有 26px，图案几乎占满整个圆。
    直接 resize 到同一尺寸后，两者的图案**尺度完全不同**，实测所有属性的
    匹配分都挤在 0.60~0.65（根本分不开）。

    为什么不能只靠二值 IoU / Hu 矩
    -----------------------------
    内切圆 + 二值化 + 外接框归一化之后，**实心形状（山、雪花、星）很准**
    （地系 0.94），但**细线条形状（螺旋）不稳定**：
        · 二值 IoU：螺旋相位一错位就掉到 0.5，第一名变成"普通"
        · Hu 矩前 6 项：幻系排到第 4 名（武 1.57 / 恶 1.82 / 地 2.04 / 幻 2.90）
    原因是螺旋的笔画只有 2~3px，二值化后碎成轮廓，形状特征完全变样。

    最终方案：灰度 NCC + 旋转搜索
    ---------------------------
        1. 灰度（不二值化）—— 保留笔画粗细，避免细线条碎裂
        2. 自适应阈值拉伸到 0~1 —— 抹掉 HUD 半透明蒙层带来的亮度漂移
        3. 图案外接框 -> 正方形 -> 32x32 —— 消掉尺度差异
        4. 15° 步长旋转搜索取最大 NCC —— 螺旋/星形对旋转敏感

    实测（2026-11-21 实盘，对面 仪式巨像 = 地+幻）：
        幻系位置  幻 0.451（第二名普通 0.367）
        地系位置  地 0.798（第二名武   0.647）
    两个位置都判对，且第一名领先明显。

    返回的分数是 NCC（-1~1），越高越像。
    """
    if patch is None or patch.size == 0:
        return (None, -1.0)

    a = _gray_norm(patch)
    if a is None:
        return (None, -1.0)

    best = (None, -1.0)
    for name, t in tpl.items():
        b = _gray_norm(t)
        if b is None:
            continue
        sc = _ncc_rot(a, b)
        if sc > best[1]:
            best = (name, sc)
    return best


def _gray_norm(im, out=32):
    """灰度归一化：不二值化，只做亮度拉伸 + 图案外接框归一化。

    保留笔画粗细是这里的关键 —— 二值化会把 2~3px 宽的螺旋线条切成碎块，
    形状特征完全失真（实测幻系因此掉到第 4 名）。
    """
    import cv2
    g = cv2.cvtColor(np.ascontiguousarray(im).astype(np.uint8),
                     cv2.COLOR_RGB2GRAY).astype(np.float32)
    g = cv2.resize(g, (120, 120), interpolation=cv2.INTER_LINEAR)

    v = g.ravel()
    lo = float(np.percentile(v, 50))
    hi = float(np.percentile(v, 99))
    thr = (lo + hi) / 2.0
    span = max(1.0, hi - thr)
    gl = np.clip((g - thr) / span, 0.0, 1.0)

    ys, xs = np.nonzero(gl > 0.35)
    if len(ys) < 10:
        return None
    crop = gl[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    s = max(crop.shape)
    pad = np.zeros((s, s), np.float32)
    oy = (s - crop.shape[0]) // 2
    ox = (s - crop.shape[1]) // 2
    pad[oy:oy + crop.shape[0], ox:ox + crop.shape[1]] = crop
    return cv2.resize(pad, (out, out), interpolation=cv2.INTER_AREA)


def _ncc(a, b):
    """归一化互相关（零均值）。"""
    a2 = a - a.mean()
    b2 = b - b.mean()
    d = float(np.linalg.norm(a2) * np.linalg.norm(b2))
    return float((a2 * b2).sum() / d) if d > 0 else 0.0


def _ncc_rot(a, b, step=15, scales=(1.0, 1.10, 0.92)):
    """旋转 + 缩放搜索取最大 NCC。

    为什么要缩放
    -----------
    官方图标是 198x198 的矢量级清晰图，HUD 上只有 26px 再放大到 32x32。
    放大过程会让笔画变粗，直接比 NCC 只有 0.46。
    实测（月牙雪熊的幻系螺旋）：
        旋转 315° + 缩放 1.00 -> NCC 0.461
        旋转 315° + 缩放 1.10 -> NCC 0.564   <- 提升 0.10
        旋转 315° + 缩放 1.15 -> NCC 0.524
    所以 1.10 附近是笔画粗细的最佳匹配点。

    为什么要旋转
    -----------
    HUD 图标有浮动动画，螺旋这类图案对相位极敏感。

    代价：3 个缩放 x 24 个角度 = 72 次 NCC，单次约 0.02ms，
    18 个模板合计约 26ms，可接受。
    """
    import cv2
    best = _ncc(a, b)
    h, w = b.shape
    c = (w / 2.0, h / 2.0)
    for sc in scales:
        for ang in range(0, 360, step):
            if sc == 1.0 and ang == 0:
                continue
            M = cv2.getRotationMatrix2D(c, ang, sc)
            br = cv2.warpAffine(b, M, (w, h), flags=cv2.INTER_LINEAR)
            s = _ncc(a, br)
            if s > best:
                best = s
    return best


# ------------------------------------------------------------------ 组合
def identify(screen, side="foe"):
    """读一侧的身份线索（不做融合判断）。

    返回：
        {
          "nickname": str|None,   # 屏幕原文，可能是玩家昵称
          "name":     str|None,   # 词表匹配到的种族名（昵称会匹配失败）
          "name_conf": float,
          "types":    list[str],  # 属性图标（硬约束，改名改不掉）
          "source":   "name" | "types" | "none",
        }
    """
    vocab = _species_vocab()
    raw, conf = read_name(screen, side, vocab=vocab)
    types = read_types(screen, side)

    import ocr_cn
    name = None
    if raw and vocab:
        name, _ = ocr_cn.Ocr.match(raw, vocab)

    return {
        "nickname": raw or None,
        "name": name,
        "name_conf": round(float(conf), 3),
        "types": types,
        "source": "name" if name else ("types" if types else "none"),
    }


# ------------------------------------------------------------------ 自检
def _selftest():
    import glob
    import cv2

    ok = True
    print("属性图标模板：%d 个" % len(_load_icons()))
    if not _load_icons():
        print("  (没有模板，先跑 scripts/build_type_icons.py)")

    frames = sorted(glob.glob(os.path.join(ROOT, "logs", "resultshots", "*.jpg")))
    frames = [p for p in frames if cv2.imdecode(
        np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR).shape[1] >= 2400]
    print("回归帧：%d 张" % len(frames))

    # 用静态图片模拟 screen 接口
    class _Shot:
        def __init__(self, img):
            self.img = img

        def grab_client(self, box):
            x, y, w, h = box
            return self.img[y:y + h, x:x + w][:, :, ::-1].copy()

    hit_f = hit_m = n = 0
    for p in frames[::4][:20]:
        img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
        scr = _Shot(img)
        n += 1
        f = read_name(scr, "foe", _species_vocab())
        m = read_name(scr, "my", _species_vocab())
        hit_f += 1 if f[0] else 0
        hit_m += 1 if m[0] else 0
        print("  %-18s foe=%-10s my=%-10s"
              % (os.path.basename(p), f[0] or "-", m[0] or "-"))

    print("\n抽样 %d 帧：敌方 %d，我方 %d" % (n, hit_f, hit_m))
    if n and hit_f == 0:
        print("**FAIL** 敌方名字一帧都没读到")
        ok = False
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
