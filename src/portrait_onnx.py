# -*- coding: utf-8 -*-
"""portrait_onnx.py —— 用 roco-cal 的 ONNX 模型识别精灵。

为什么需要它
------------
原来的头像识别（`portraits.py`）靠模板匹配，只有 71 张模板，覆盖极低。
roco-cal 训练了一个 620 类的分类器，实测在我们的真实战斗帧上
置信度 0.76~1.00，覆盖面远超模板匹配。

关键：**输入框必须取 HUD 头像，不能取固定百分比的立绘框。**
实测喂 HUD 头像 84x76 会输出 "000无"（框里没内容）。

★ 输入框该取哪里（2026-11-21 重大修正）
---------------------------------------
上面那句**是错的**。实测 165 张 HUD 头像上 Top-1 82.9%/89.5%，
模型完全认 HUD 头像。

真正该避免的是**固定百分比的立绘框**（`region()`）：

    config/portrait_region.json
        foe  x=0.645 y=0.355 w=0.075 h=0.195
        self x=0.225 y=0.55  w=0.09  h=0.15

这些框框的是**会在场地里走动的 3D 精灵模型**。实测在真实对战帧上
把框画出来看：

    233445  REG-self 框住我方精灵的尾巴/身体中段
    233747  REG-self 框住我方精灵的后腿
    234212  REG-self 框住人物 NPC 的腰

同一批帧上三通道对比（帧 `_fit` 到基准 2560x1600，真值取自帧内名字条）：

    A. region() 立绘框          0/8 =   0%   置信 0.52~0.99 随机
    B. HUD 头像紧框             5/8 =  62%
    C. HUD 头像 + margin=10     6/8 =  75%   命中时多为 1.00  <- 最佳

所以本模块的**默认输入改为 HUD 头像框**，`region()` 降级为显式开关
（`source="region"`），只留给 `--selftest` 和以后换目标检测时用。

★ 高置信也会错
--------------
HUD 通道实测有一例 `帕帕斯卡 -> 龙息帕尔 conf=1.00`。所以**不能只信置信度**，
调用方（`foe_ident`）必须保留属性硬约束和连续帧防抖。

双通道设计
----------
    A. HUD 头像模板（portraits.py，快 <5ms）—— 每帧轮询
    B. HUD 头像 ONNX（本模块）           —— 每帧轮询

两者由 `identify_fused()` 融合。注意 `identify_fused()` 里的
`portrait_result` 现在默认也来自 HUD 框，所以"立绘更权威"的旧假设不再成立，
融合靠的是**置信度门槛 + 连续帧稳定**，不是通道优先级。

模型规格（与上游 Rust 代码逐行对齐）
------------------------------------
    输入  [batch, 3, 86, 76]  float32  通道优先，/255
    输出  [batch, 620]        logits
    预处理：等比缩放到不超过 76x86（Lanczos）→ 居中填充黑底
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "data", "external", "roco-cal", "models",
                     "all_classes.onnx")
CLASSES = os.path.join(ROOT, "data", "external", "roco-cal", "models",
                       "classes.json")
REGION_CFG = os.path.join(ROOT, "config", "portrait_region.json")

W, H = 76, 86
_LOCK = threading.Lock()
_SESS = None
_CLASSES = None
_REGION = None


# ------------------------------------------------------------------ 模型

def _session():
    global _SESS
    with _LOCK:
        if _SESS is None:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1
            opts.inter_op_num_threads = 1
            _SESS = ort.InferenceSession(
                MODEL, sess_options=opts, providers=["CPUExecutionProvider"])
        return _SESS


def classes():
    global _CLASSES
    if _CLASSES is None:
        _CLASSES = json.load(io.open(CLASSES, encoding="utf-8"))
    return _CLASSES


def available():
    return os.path.exists(MODEL) and os.path.exists(CLASSES)


def region(side="foe"):
    """立绘区域（百分比）。缺配置时用标定得到的默认值。"""
    global _REGION
    if _REGION is None:
        try:
            _REGION = json.load(io.open(REGION_CFG, encoding="utf-8"))
        except Exception:
            _REGION = {}
    d = _REGION.get(side)
    if not d:
        d = ({"x": 0.62, "y": 0.34, "w": 0.20, "h": 0.30} if side == "foe"
             else {"x": 0.15, "y": 0.45, "w": 0.25, "h": 0.35})
    return d


# ------------------------------------------------------------------ 预处理

def to_tensor(img):
    """RGB(A) 图 -> [1,3,86,76] float32。逐行对齐上游 classifier_tensor。"""
    import cv2
    if img is None or img.size == 0:
        return None
    if img.ndim == 3 and img.shape[2] == 4:
        a = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3].astype(np.float32) * a).astype(np.uint8)
    elif img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)

    h, w = img.shape[:2]
    nw, nh = w, h
    if w > W or h > H:
        asp = w / float(h)
        if W / float(H) >= asp:
            nh, nw = H, max(1, int(round(H * asp)))
        else:
            nw, nh = W, max(1, int(round(W / asp)))
        img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LANCZOS4)

    plane = W * H
    out = np.zeros((3, H, W), dtype=np.float32)
    ox, oy = (W - nw) // 2, (H - nh) // 2
    for c in range(3):
        out[c, oy:oy + nh, ox:ox + nw] = \
            img[:, :, c].astype(np.float32) / 255.0
    return out[None, ...]


def _softmax(x):
    e = np.exp(x - x.max())
    return e / e.sum()


# ------------------------------------------------------------------ 识别

def _clean(label):
    """'001迪莫' -> '迪莫'；'000无' -> None"""
    s = (label or "").strip()
    if not s or s.startswith("000"):
        return None
    # 去掉前导数字（类别编号）
    i = 0
    while i < len(s) and s[i].isdigit():
        i += 1
    return s[i:] or None


def predict(img, topk=3):
    """对一张图（RGB 或 RGBA）分类。

    返回 [(名字, 置信度), ...]；名字已去掉类别编号。识别为"无"时返回 []。
    """
    if not available() or img is None:
        return []
    t = to_tensor(img)
    if t is None:
        return []
    try:
        logits = _session().run(None, {"images": t})[0][0]
    except Exception:
        return []
    pr = _softmax(logits)
    idx = np.argsort(pr)[-topk:][::-1]
    cls = classes()
    out = []
    for i in idx:
        name = _clean(cls[int(i)])
        if name:
            out.append((name, float(pr[int(i)])))
    return out


def hud_crop(screen, side="foe", margin=None):
    """按 HUD 头像框裁一张图（RGB）。失败返回 None。

    这是**默认输入**。依据见模块 docstring：
    HUD 头像框 6/8 = 75%（命中时多为 1.00），而立绘百分比框 0/8 = 0%。

    `margin` 默认取 `portraits.SEARCH_MARGIN`（10）。实测带 margin 比紧框
    好（62% -> 75%）：紧框差几个像素就把头像边缘切掉，ONNX 看到的构图
    和训练集不一致；留一圈边让它"居中"更接近训练分布。

    `screen` 需要提供 `grab_client((x,y,w,h)) -> RGB` 和 `client_size`。
    """
    if screen is None:
        return None
    try:
        import portraits
        if margin is None:
            margin = portraits.SEARCH_MARGIN
        box_fn = (portraits.foe_box_for if side == "foe"
                  else portraits.box_for)
        cw, ch = getattr(screen, "client_size", None) or (2560, 1600)
        box = box_fn(cw, ch, margin=margin)
        img = screen.grab_client(box)
    except Exception:
        return None
    if img is None or getattr(img, "size", 0) == 0:
        return None
    return img


def predict_region(screen, side="foe", topk=3, source="hud"):
    """从整屏里裁剪后识别。

    source
    ------
    "hud"    （默认）HUD 头像框 —— 实测 6/8 = 75%，命中时多为 1.00
    "region" 旧的百分比立绘框 —— 实测 0/8 = 0%，只在排查时用

    `screen` 需要提供 `grab_client((x,y,w,h)) -> RGB`。

    为什么要换默认
    --------------
    `region()` 框的是会在场地里走动的 3D 精灵，同一批帧里有时框到尾巴、
    有时框到 NPC 的腰，识别结果等于瞎猜（置信度却仍是 0.5~0.99）。
    详见模块 docstring 的实测表。
    """
    if not available() or screen is None:
        return []

    if source == "region":
        d = region(side)
        img = screen.grab_client((0, 0, 2560, 1600))
        if img is None or img.size == 0:
            return []
        h, w = img.shape[:2]
        x, y = int(d["x"] * w), int(d["y"] * h)
        bw, bh = int(d["w"] * w), int(d["h"] * h)
        crop = img[y:y + bh, x:x + bw]
        if crop.size == 0:
            return []
        return predict(crop, topk=topk)

    crop = hud_crop(screen, side)
    if crop is None:
        return []
    return predict(crop, topk=topk)


# ------------------------------------------------------------------ 双通道融合

# ONNX 通道的"可以直接压过模板通道"阈值。
#
# 依据（2026-11-21 定标，165 张已标注头像留一验证）：
#
#     模板匹配     Top-1 42.1%    score>=0.85 时 94.6%（但只覆盖 26%）
#     ONNX         Top-1 85.6%    conf>=0.90 时 96.6%（覆盖 72%）
#
# ★ 2026-11-21 重要变更：两条通道现在看**同一张图**
# -------------------------------------------------
# 以前 ONNX 喂的是固定百分比立绘框（框 3D 精灵），模板喂 HUD 头像框，
# 所以"两条一致"是强证据。现在 `predict_region()` 默认也走 HUD 头像框，
# 两条通道看的是同一张图 —— 一致率实测 49%（81/165）。
#
# 这不影响下面的融合顺序，但**改变了语义**：
#   · 一致 -> 两个不同模型对同一张图给出同一答案，比单模型可靠
#   · 不一致 -> 只是"两个模型看法不同"，不代表哪条通道"看错了东西"
#
# 融合顺序本身仍然成立，因为下面第 2 步（优先模板）是**实测出来的**：
#   ONNX conf<0.92 且 模板 conf>=0.60 的 16 张里，模板对 15 张（94%），
#   ONNX 只对 7 张（44%）。模板在这个区间确实更准（模板对同类变体更敏感，
#   ONNX 倾向把相似种认成常见种）。
ONNX_OVERRIDE_CONF = 0.92


def identify_fused(hud_result, portrait_result, hud_conf=0.0,
                   prev=None, stable_count=0, onnx_override=None):
    """融合两条通道的结果。

    ⚠ 命名先说清楚（旧的注释有误解）
    ---------------------------------
    `hud_result` **不是**读游戏界面上的名字，而是 `portraits.identify()`
    对着 `assets/portraits_foe/` 这个**自己收集的头像库**做模板匹配的结果。
    日志里写成 "hud" 是历史遗留。

    所以"游戏界面显示的，绝对正确"这个理由不成立 —— 实测留一验证
    Top-1 只有 42.1%（同库里的帧除外，那些会被自己以 1.00 匹配到）。

    ⚠ 2026-11-21：两条通道现在看同一张图
    ------------------------------------
    `portrait_result` 默认也来自 HUD 头像框（见模块 docstring），
    所以这里融合的是"模板匹配"和"ONNX 分类"两个模型对**同一张图**的看法，
    不再是"HUD 头像 vs 3D 立绘"。

    hud_result      : portraits.identify() 返回的名字或 None
    portrait_result : predict_region() 返回的 [(name, conf), ...]
    hud_conf        : 模板通道的相似度分（0~1）
    prev            : 当前已认定的名字（防抖用）
    stable_count    : ONNX 通道连续给出同一答案的次数

    实测教训（2026-11-21 01:12）
    ---------------------------
    原策略"ONNX >=0.50 就覆盖"导致每 2 秒抖动：
        FOE -> 绿草精灵(0.83) -> 白瑟(0.72) -> 燃薪虫(0.96) ...
    那时 ONNX 框的是 3D 精灵，框里混了背景和 NPC，相邻帧答案不同。
    所以必须要求"连续 2 帧"。（现在框已经换成 HUD 头像，不会这样抖，
    但防抖保留 —— HUD 头像在换人瞬间也会有一两帧过渡。）

    当前策略（2026-11-21 定标后）
    ----------------------------
      1. ONNX conf >= 0.92 且连续 2 帧 -> 用 ONNX（定标 96.6%）
      2. 否则模板通道 conf >= 0.60  -> 用模板（实测 94%，见 ONNX_OVERRIDE_CONF）
      3. 否则 ONNX conf >= 0.85 且连续 2 帧 -> 用 ONNX
      4. 都不满足 -> 保持 prev（绝不抖）

    第 3 步的现状（2026-11-21 实测）
    -------------------------------
    165 张里**没有一张**落进第 3 步的生效区间（模板<0.60 且 ONNX in
    [0.85,0.92)），所以它现在等于死代码。而模板无效时 ONNX 在
    [0.70,0.85) 只有 50% 准确 —— 说明 0.85 这个门槛偏松。
    没改是因为没有实测样本支撑新门槛，等积累到样本再收紧。
    """
    top = portrait_result[0] if portrait_result else (None, 0.0)
    p_name, p_conf = top
    auth = ONNX_OVERRIDE_CONF if onnx_override is None else onnx_override
    out = {"name": None, "conf": 0.0, "source": None, "conflict": False,
           "hud": hud_result, "hud_conf": round(hud_conf, 3),
           "portrait": p_name, "portrait_conf": round(p_conf, 3),
           "kept": False}

    # 1. ONNX 高置信 + 稳定 -> 优先（定标 96.6%，比模板通道高一个量级）
    if p_name and p_conf >= auth and stable_count >= 2:
        out.update(name=p_name, conf=p_conf, source="portrait")
        if hud_result and hud_result != p_name:
            out["conflict"] = True
        return out

    # 1b. ONNX 压倒性领先模板 -> 不要求"连续 2 帧"
    #
    # 为什么需要（2026-11-21 实盘踩到）
    # ------------------------------
    # 那一局对面是「里拉瑶」，日志显示：
    #     FOE -> id conflict hud=白发路路(0.65) portrait=里拉瑶(1.00) -> use portrait
    #     FOE -> 白发路路 (0.65)          <- 最终却用了低分那个
    #
    # 因为 ONNX 还没连续 2 帧稳定（刚换上场），掉到了规则 2
    # （模板 conf >= 0.60 就采纳）。模板 0.65 是"勉强及格"，
    # ONNX 1.00 是"几乎确定"，却让 0.65 赢了。
    #
    # "连续 2 帧"本来是为了压抖动，但它不该拦住**压倒性**的答案：
    # ONNX 1.00 同时模板只有 0.65，两者差距 0.30，抖动几乎不可能
    # 造成这种组合。所以给一条快速通道。
    if (p_name and p_conf >= 0.95
            and hud_result and hud_result != p_name
            and p_conf - hud_conf >= 0.25):
        out.update(name=p_name, conf=p_conf, source="portrait-strong")
        out["conflict"] = True
        return out

    # 2. 模板通道有结果 -> 用它（ONNX 不够自信时）
    if hud_result and hud_conf >= 0.60:
        out.update(name=hud_result, conf=hud_conf, source="hud")
        if p_name and p_name != hud_result and p_conf >= 0.85:
            out["conflict"] = True
        return out

    # 3. 模板没有 -> ONNX 要"较高置信 + 稳定"才敢用
    #    实测这一步从未生效（0/165），保留是为了模板库没覆盖的物种
    if p_name and p_conf >= 0.85 and stable_count >= 2:
        # 已有认定时，除非显著更高，否则不改
        if prev and prev != p_name and p_conf < auth:
            out.update(name=prev, conf=0.0, source="kept", kept=True)
            return out
        out.update(name=p_name, conf=p_conf, source="portrait")
        return out

    # 4. 都不满足 -> 保持原判，绝不抖
    if prev:
        out.update(name=prev, conf=0.0, source="kept", kept=True)
        return out
    if hud_result:
        out.update(name=hud_result, conf=hud_conf, source="hud-low")
    return out


# ------------------------------------------------------------------ 自检

def _selftest():
    """自检：同一帧上同时跑 region 和 HUD 两条路径，便于肉眼对比。

    注意测试帧 `logs/shot/*.jpg` 是 1280x800（基准的一半），
    所以 HUD 框要按比例缩放。线上 `grab_client` 会 `_fit` 到 2560x1600，
    真实准确率比这里高（见模块 docstring 的 75%）。
    """
    import glob
    import cv2
    if not available():
        print("模型不存在:", MODEL)
        return 1
    print("模型:", os.path.basename(MODEL), " 类别:", len(classes()))
    shots = sorted(glob.glob(os.path.join(ROOT, "logs", "shot", "*.jpg")))
    shots = [p for p in shots if "skill" in os.path.basename(p)][-6:]
    if not shots:
        print("没有测试帧")
        return 0

    import portraits
    ok = 0
    for p in shots:
        im = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
        if im is None:
            continue
        h, w = im.shape[:2]
        # 测试帧不是基准分辨率，box_for 的坐标要按比例缩
        sx, sy = w / float(portraits.BASE_W), h / float(portraits.BASE_H)
        print("  %s (%dx%d)" % (os.path.basename(p), w, h))
        for side in ("foe", "self"):
            d = region(side)
            x, y = int(d["x"] * w), int(d["y"] * h)
            bw, bh = int(d["w"] * w), int(d["h"] * h)
            r_region = predict(im[y:y + bh, x:x + bw])

            m = portraits.SEARCH_MARGIN
            box_fn = (portraits.foe_box_for if side == "foe"
                      else portraits.box_for)
            hx, hy, hw, hh = box_fn(w, h, margin=m)
            hx, hy = int(hx * sx), int(hy * sy)
            hw, hh = max(1, int(hw * sx)), max(1, int(hh * sy))
            r_hud = predict(im[max(0, hy):hy + hh, max(0, hx):hx + hw])

            def fmt(r):
                if not r:
                    return "(无)"
                return "%s %.2f" % (r[0][0][:12], r[0][1])

            print("    %-5s region=%-20s hud=%-20s"
                  % (side, fmt(r_region), fmt(r_hud)))
            ok += 1
    print("\n识别 %d 次" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
