"""Active-pet identification from the top-left HUD bust.

The battle HUD draws a 2D bust of the pet that is currently on the field at a
fixed spot in the top-left corner, with its name, level and *exact* HP next to
it (the user's screenshot shows "恶魔狼  417/417").  The bust is a static
illustration, so a small crop of it identifies the species without OCR - which
matters because OCR on the name was unreliable and the bot must never guess
whether 恶魔狼 is on the field before it triggers the leader transformation.

Coordinates are CLIENT-relative (the game's client area is 2560x1600; the
user's screenshots are 2555x1597, so they map 1:1 to within a pixel).
anything else in robot.py speaks window-relative coordinates, so use
Screen.grab_client() for this box.
"""

import os
import cv2
import numpy as np

# Convention: (x, y, w, h) in client coordinates, taken from the reference
# screenshot of 恶魔狼. Only the head is inside; the ring/background around it
# shifts with the camera, the head does not.
PORTRAIT_BOX = (55, 47, 76, 80)

# 对面 HUD 的头像框（右上角，名字左边）。比我们自己的那个宽一点，
# 所以两个库必须分开存模板，混用会互相拉低相关度。
#
# ⚠ 不要随手改这个框（2026-11-21 实测教训）
# -----------------------------------------
# 旧框 (2148, 47, 84, 76) 确实有个毛病：左边框压住了属性图标
# （两个 26px 圆盘在 x≈2108~2155），所以存下来的模板左下角带着彩色圆点。
#
# 我试过改成 (2166, 50, 68, 96) 避开图标，效果上确实更干净，但实测代价：
#
#     旧框裁 + 旧 target -> 0.988      <- 和已有 145 个模板完全对齐
#     新框裁 + 新 target -> 0.612      <- 分数掉 0.38
#
# 因为 145 个模板都是**用旧框裁的**，换框等于让整个库失效，
# 而原始截图（1280x800 的小图）不足以重裁。
#
# 结论：**框不动**。左下角那点图标残留在匹配时影响很小
# （0.988 就是证据），不值得为此废掉整个模板库。
# 将来如果要换框，必须同时重裁所有模板。
FOE_PORTRAIT_BOX = (2148, 47, 84, 76)

# Same box, normalised size used for comparison.
NORM_W, NORM_H = 64, 64

LIB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "assets", "portraits")
FOE_LIB_DIR = os.path.join(os.path.dirname(LIB_DIR), "portraits_foe")

# The box above was measured on a 2560x1600 client area. If the window is
# resized the HUD scales with it, so scale the box the same way.
BASE_W, BASE_H = 2560, 1600


def box_for(client_w, client_h, margin=0):
    """头像框（**基准分辨率坐标**）。

    以前这里按客户区尺寸缩放；现在统一交给 Screen.grab_base 缩回基准尺寸，
    所以这里只要把 margin 加上即可，不再自己乘比例。
    """
    x, y, w, h = PORTRAIT_BOX
    return (x - margin, y - margin, w + 2 * margin, h + 2 * margin)


def foe_box_for(client_w, client_h, margin=0):
    x, y, w, h = FOE_PORTRAIT_BOX
    return (x - margin, y - margin, w + 2 * margin, h + 2 * margin)


# The bust is drawn a few pixels off the screenshot position on a live window
# (measured +5,+4 on the user's reference frame), so matching searches a small
# neighbourhood instead of assuming pixel-exact placement.
SEARCH_MARGIN = 10

_lib_cache = {"mtime": None, "items": []}


def _prepare(img_rgb):
    """BGR float copy with a light blur.

    NOTE: no resizing here. The search crop is deliberately larger than the
    template (SEARCH_MARGIN), and matchTemplate needs that size difference to
    slide the template. Normalising both to 64x64 squashed the search window
    onto the template and collapsed the score to ~0.06.
    """
    g = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
    # A light blur forgives one-pixel jitter from UI scaling / DPI rounding and
    # suppresses the fine texture on the bust that the renderer dithers.
    g = cv2.GaussianBlur(g, (3, 3), 0)
    return g.astype(np.float32)


def _imread(path):
    """支持中文/Unicode 路径的读图（cv2.imread 在 Windows 上按 ANSI 解析路径）。"""
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        if buf.size == 0:
            return None
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except (OSError, ValueError):
        return None


def _imwrite(path, bgr):
    """支持中文/Unicode 路径的写图。"""
    try:
        ok, buf = cv2.imencode(os.path.splitext(path)[1] or ".png", bgr)
        if not ok:
            return False
        buf.tofile(path)
        return True
    except (OSError, ValueError):
        return False


def load_library(lib_dir=LIB_DIR):
    """Load every <name>.png in the portrait folder, rebuilding on change."""
    try:
        mtime = max(os.path.getmtime(os.path.join(lib_dir, f))
                    for f in os.listdir(lib_dir) if f.lower().endswith(".png"))
    except (OSError, ValueError):
        return []
    # The cache key must include the folder: 我方和对面是两个库，只按 mtime
    # 缓存会让先读的那个库被另一个目录复用。
    key = (os.path.abspath(lib_dir), mtime)
    if _lib_cache.get("key") == key:
        return _lib_cache["items"]
    items = []
    for f in sorted(os.listdir(lib_dir)):
        if not f.lower().endswith(".png"):
            continue
        if f.startswith("_"):
            # "_unknown_*.png" are unlabelled samples kept for later review.
            # They must never become match candidates: a stray sample would
            # win the correlation and report a pet name that isn't one.
            continue
        if f.lower().endswith("_name.png"):
            # 名字条截图（210x55），不是头像。见下面的形状检查。
            continue
        p = os.path.join(lib_dir, f)
        img = _imread(p)
        if img is None or img.shape[0] < 8 or img.shape[1] < 8:
            continue
        # 按**形状**过滤，而不是只靠文件名（2026-11-21 实测）
        # --------------------------------------------------
        # 目录里混了 13 个 210x55 的名字条，文件名是「冥古龙_name.png」
        # 这种（不以 _ 开头），逃过了上面的前缀检查，被当成头像模板加载。
        #
        # 后果：它们参与相关性计算，横条形状会干扰真正的头像匹配。
        # 实测 load_library 加载 158 个模板，其中 13 个是这种脏数据。
        #
        # 头像基本是方的（76x84 或 68x96），宽高比 0.5~1.5；
        # 名字条是 210x55（3.8）。这个判据不依赖命名习惯，更稳。
        h, w = img.shape[:2]
        ar = w / float(max(1, h))
        if ar > 1.5 or ar < 0.5:
            continue
        items.append((os.path.splitext(f)[0], _prepare(img[:, :, ::-1])))
    _lib_cache["key"] = key
    _lib_cache["items"] = items
    return items


def score(img_rgb, template):
    """Best normalised correlation of one portrait against a template.

    `img_rgb` may be a little larger than the template: the live HUD lands a
    few pixels off the position measured on the reference screenshot, so the
    template slides inside the crop and the best position wins.
    """
    a = _prepare(img_rgb)
    b = template
    if a.shape[0] < b.shape[0] or a.shape[1] < b.shape[1]:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    r = cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)
    return float(r.max())


def identify(img_rgb, items=None, target=None):
    """Return (name, score) of the best match, or (None, best_score).

    `target` is the template size expected inside `img_rgb` (the crop minus
    the search margin). It lets the same templates work when the game window is
    not at the reference size: the template is rescaled to the box the HUD
    should occupy, then slides inside the margin.
    """
    if img_rgb is None or img_rgb.size == 0:
        return None, -1.0
    if items is None:
        items = load_library()
    if not items:
        return None, -1.0
    a = _prepare(img_rgb)
    best_name, best = None, -1.0
    for name, tpl in items:
        b = tpl
        if target is not None and tuple(b.shape[:2][::-1]) != tuple(target):
            b = cv2.resize(b, (int(target[0]), int(target[1])))
        if a.shape[0] < b.shape[0] or a.shape[1] < b.shape[1]:
            b = cv2.resize(b, (a.shape[1], a.shape[0]))
        r = cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)
        s = float(r.max())
        if s > best:
            best_name, best = name, s
    return best_name, best


def save_template(img_rgb, path):
    """Store a portrait crop as a new library template."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return _imwrite(path, cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR))
