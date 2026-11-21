# -*- coding: utf-8 -*-
"""portrait_onnx.py —— 用 roco-cal 的 ONNX 模型识别精灵（角色立绘通道）。

为什么需要它
------------
原来的头像识别（`portraits.py`）靠模板匹配，只有 71 张模板，覆盖极低。
roco-cal 训练了一个 620 类的分类器，实测在我们的真实战斗帧上
置信度 0.76~1.00，覆盖面远超模板匹配。

关键：**模型认的是战场上的 3D 角色立绘，不是 HUD 小头像。**
实测喂 HUD 头像 84x76 会输出 "000无"（框里没内容）。

双通道设计
----------
    A. HUD 头像（portraits.py，快 <5ms）—— 每帧轮询
    B. 角色立绘（本模块，准 0.76+）    —— 换人/需要确认时

两者由 `identify_fused()` 融合，冲突时以 B 为准（立绘信息量大）。

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


def predict_region(screen, side="foe", topk=3):
    """从整屏里按标定区域裁剪后识别。

    screen 需要提供 grab_client((x,y,w,h)) -> RGB。
    """
    if not available() or screen is None:
        return []
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


# ------------------------------------------------------------------ 双通道融合

# 立绘通道（ONNX）的"可以直接压过模板通道"阈值。
#
# 依据（2026-11-21 定标，src/portrait_calib.py --compare）：
#   145 张已标注敌方头像，两条通道在同一批数据上对比（模板通道留一验证）
#
#     立绘 ONNX   Top-1 82.8%    conf>=0.90 时 93.2%（覆盖 81%）
#     模板匹配     Top-1 42.1%    score>=0.85 时 94.6%（但只覆盖 26%）
#
# 所以"模板通道更权威"这个旧结论是反的。取 0.92 比 0.90 更保守一点，
# 和下面"已有认定时改用新答案"的门槛保持一致。
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

    hud_result      : portraits.identify() 返回的名字或 None
    portrait_result : predict_region() 返回的 [(name, conf), ...]
    hud_conf        : 模板通道的相似度分（0~1）
    prev            : 当前已认定的名字（防抖用）
    stable_count    : 立绘通道连续给出同一答案的次数

    实测教训（2026-11-21 01:12）
    ---------------------------
    原策略"立绘 >=0.50 就覆盖"导致每 2 秒抖动：
        FOE -> 绿草精灵(0.83) -> 白瑟(0.72) -> 燃薪虫(0.96) ...
    因为立绘框里混了背景和 NPC，相邻帧答案不同。所以必须要求"连续 2 帧"。

    当前策略（2026-11-21 定标后）
    ----------------------------
      1. 立绘 conf >= 0.92 且连续 2 帧 -> 用立绘（定标 93.2%）
      2. 否则模板通道 conf >= 0.60  -> 用模板
      3. 否则立绘 conf >= 0.85 且连续 2 帧 -> 用立绘
      4. 都不满足 -> 保持 prev（绝不抖）
    """
    top = portrait_result[0] if portrait_result else (None, 0.0)
    p_name, p_conf = top
    auth = ONNX_OVERRIDE_CONF if onnx_override is None else onnx_override
    out = {"name": None, "conf": 0.0, "source": None, "conflict": False,
           "hud": hud_result, "hud_conf": round(hud_conf, 3),
           "portrait": p_name, "portrait_conf": round(p_conf, 3),
           "kept": False}

    # 1. 立绘高置信 + 稳定 -> 优先（定标 93.2%，比模板通道高一个量级）
    if p_name and p_conf >= auth and stable_count >= 2:
        out.update(name=p_name, conf=p_conf, source="portrait")
        if hud_result and hud_result != p_name:
            out["conflict"] = True
        return out

    # 2. 模板通道有结果 -> 用它（立绘不够自信时）
    if hud_result and hud_conf >= 0.60:
        out.update(name=hud_result, conf=hud_conf, source="hud")
        if p_name and p_name != hud_result and p_conf >= 0.85:
            out["conflict"] = True
        return out

    # 3. 模板没有 -> 立绘要"高置信 + 稳定"才敢用
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
    ok = 0
    for p in shots:
        im = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
        if im is None:
            continue
        h, w = im.shape[:2]
        for side in ("foe", "self"):
            d = region(side)
            x, y = int(d["x"] * w), int(d["y"] * h)
            bw, bh = int(d["w"] * w), int(d["h"] * h)
            r = predict(im[y:y + bh, x:x + bw])
            top = r[0] if r else ("(无)", 0.0)
            print("  %-22s %-5s -> %-14s %.2f"
                  % (os.path.basename(p), side, top[0], top[1]))
            ok += 1
    print("\n识别 %d 次" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
