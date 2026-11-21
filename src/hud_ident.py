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

# 图标匹配门槛。
#
# 实测（2026-11-21，用官方 18 图标做留一验证）：
#   真图标（同一属性的不同帧）      0.95 ~ 1.00
#   空位 / 被头像遮挡的位置          0.60 ~ 0.75
# 0.85 落在中间，两边都留足余量。
#
# 为什么必须靠分数而不能靠"位置是否为空"：
#   迪莫是**光系单属性**，第二个图标位置被头像挡住，
#   饱和度 sat75=174（比真图标的 128 还高），靠颜色判空会误判成双属性。
MATCH_MIN = 0.85

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


def _icon_is_empty(patch):
    """判断这个位置是不是空的（单属性精灵只有 1 个图标）。

    实测：空位是背景色，饱和度低且方差小；有图标的位置饱和度明显更高。
    """
    if patch is None or patch.size == 0:
        return True
    import cv2
    hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
    sat = hsv[:, :, 1].astype(np.float32)
    # 图标是饱和的彩色圆盘；背景（天空/地面）饱和度低得多
    return float(np.percentile(sat, 75)) < 90.0


def read_types(screen, side="my"):
    """读 HUD 上的属性图标 -> ['幽', '恶']。

    读不出返回 []（调用方必须把 [] 当成"未知"而不是"无属性"）。

    单属性精灵的处理
    ----------------
    单属性时第二个图标位置是**空的**，但空位不一定干净：
    实测迪莫（**光系**，U+5149）的第二个位置被头像挡住，饱和度仍然很高
    （sat75=174，比真图标的 128 还高），所以「靠饱和度判空」会误判。

    正确做法：**看模板匹配分数**。真图标匹配分 0.95+，空位/遮挡只有 0.6~0.7。
    所以这里统一用分数门槛（MATCH_MIN），不再依赖 `_icon_is_empty`。
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
        name, score = _match_icon(patch, tpl)
        if name and score >= MATCH_MIN:
            out.append(name)
    return out


def _match_icon(patch, tpl):
    """模板匹配一个图标，返回 (属性名, 分数)。

    为什么不能直接 resize 后比对
    ---------------------------
    官方图标 198x198，白色图案只占中间约 60%，周围是较宽的彩色底边；
    HUD 上截到的图标直径只有 26px，图案几乎占满整个圆。
    直接 resize 到同一尺寸后，两者的图案**尺度完全不同**，实测所有属性的
    匹配分都挤在 0.60~0.65（根本分不开）。

    正确做法：三步归一化
        1. 圆内二值化 —— 图案比底色亮，取亮度阈值分离出白色图案
        2. 外接框裁切 —— 消掉图案周围的空白/彩底
        3. 缩放到 48x48 —— 消掉尺度差异，只比形状

    实测效果（寂灭骨龙=龙+幽，真实帧）：
        位置1 龙 0.781（第二名普通 0.682，差 0.10）
        位置2 幽 0.833（第二名龙   0.673，差 0.16）
    区分度从"全部挤在 0.6"变成"第一名明显领先"。
    """
    import cv2
    if patch is None or patch.size == 0:
        return (None, -1.0)

    a = _norm_pattern(_pattern_mask(patch))
    if a is None:
        return (None, -1.0)

    best = (None, -1.0)
    for name, t in tpl.items():
        b = _norm_pattern(_pattern_mask(t))
        if b is None:
            continue
        inter = float((a * b).sum())
        union = float(a.sum() + b.sum() - inter)
        sc = inter / union if union > 0 else 0.0
        if sc > best[1]:
            best = (name, sc)
    return best


def _pattern_mask(im, size=120):
    """圆内的白色图案 -> 二值掩码。

    图案比彩色底亮，所以取圆内亮度的 (中位 + 99 分位)/2 作阈值。
    用中位和 99 分位的中间值而不是固定阈值，是因为 HUD 会蒙一层半透明，
    整体亮度会漂。
    """
    import cv2
    g = cv2.cvtColor(np.ascontiguousarray(im).astype(np.uint8),
                     cv2.COLOR_RGB2GRAY).astype(np.float32)
    g = cv2.resize(g, (size, size), interpolation=cv2.INTER_LINEAR)
    h, w = g.shape
    yy, xx = np.mgrid[0:h, 0:w]
    rr = np.sqrt((yy - h / 2.0 + 0.5) ** 2 + (xx - w / 2.0 + 0.5) ** 2) / (h / 2.0)
    inside = rr < 0.92
    v = g[inside]
    if v.size == 0:
        return None
    thr = (float(np.percentile(v, 50)) + float(np.percentile(v, 99))) / 2.0
    return ((g > thr) & inside).astype(np.float32)


def _norm_pattern(mask, out=48):
    """图案外接框 -> 正方形 -> 缩放到 out x out（消掉尺度差异）。"""
    import cv2
    if mask is None:
        return None
    ys, xs = np.nonzero(mask)
    if len(ys) < 8:
        return None
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    crop = mask[y0:y1, x0:x1]
    s = max(crop.shape)
    pad = np.zeros((s, s), np.float32)
    oy = (s - crop.shape[0]) // 2
    ox = (s - crop.shape[1]) // 2
    pad[oy:oy + crop.shape[0], ox:ox + crop.shape[1]] = crop
    return cv2.resize(pad, (out, out), interpolation=cv2.INTER_AREA)


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
