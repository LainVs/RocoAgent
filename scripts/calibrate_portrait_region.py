# -*- coding: utf-8 -*-
"""calibrate_portrait_region.py —— 标定"角色立绘"区域。

背景
----
roco-cal 的识别模型认的是**战场上的 3D 角色立绘**，不是 HUD 小头像。
实测喂 HUD 头像 84x76 会输出 "000无"，喂立绘区域能到 0.94~1.00。

本脚本在真实战斗帧上做网格搜索，找出覆盖率与置信度都最好的固定框。

用法
----
    python scripts\\calibrate_portrait_region.py            # 搜索
    python scripts\\calibrate_portrait_region.py --save     # 写入 config
"""
from __future__ import annotations

import glob
import io
import json
import os
import sys

import numpy as np

ROOT = r"D:\Projects\RocoAgent"
MODEL = os.path.join(ROOT, "data", "external", "roco-cal", "models",
                     "all_classes.onnx")
CLASSES = os.path.join(ROOT, "data", "external", "roco-cal", "models",
                       "classes.json")
W, H = 76, 86


def tensor(img):
    import cv2
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
        out[c, oy:oy + nh, ox:ox + nw] = img[:, :, c].astype(np.float32) / 255.0
    return out[None, ...]


def read_img(p):
    import cv2
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)


def main():
    import onnxruntime as ort

    sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    classes = json.load(io.open(CLASSES, encoding="utf-8"))

    shots = sorted(glob.glob(os.path.join(ROOT, "logs", "shot", "*.jpg")))
    # 只取"技能/换人"动作的帧，这些一定是战斗画面
    shots = [p for p in shots
             if any(k in os.path.basename(p) for k in ("skill", "switch"))]
    print("战斗截图: %d 张" % len(shots))
    if not shots:
        return

    def score_region(rx, ry, rw, rh, limit=60):
        """返回 (平均置信度, 非'无'比例, 有效样本数)"""
        confs, valid = [], 0
        for p in shots[:limit]:
            im = read_img(p)
            if im is None:
                continue
            h, w = im.shape[:2]
            x, y = int(rx * w), int(ry * h)
            bw, bh = int(rw * w), int(rh * h)
            crop = im[y:y + bh, x:x + bw]
            if crop.size == 0:
                continue
            lg = sess.run(None, {"images": tensor(crop)})[0][0]
            e = np.exp(lg - lg.max())
            pr = e / e.sum()
            i = int(pr.argmax())
            confs.append(float(pr[i]))
            if not classes[i].startswith("000"):
                valid += 1
        if not confs:
            return 0.0, 0.0, 0
        return float(np.mean(confs)), valid / float(len(confs)), len(confs)

    print("\n=== 敌方立绘：网格搜索 ===")
    print("%-28s %8s %8s" % ("区域", "平均置信", "非空率"))
    print("-" * 48)
    best = None
    # 战场右半边，敌人站在中右
    for rx in (0.45, 0.50, 0.55, 0.60, 0.62):
        for ry in (0.25, 0.30, 0.34, 0.40):
            for rw, rh in ((0.20, 0.30), (0.25, 0.35), (0.30, 0.40)):
                if rx + rw > 0.98:
                    continue
                c, v, n = score_region(rx, ry, rw, rh)
                if n < 10:
                    continue
                key = (v, c)          # 先比非空率，再比置信度
                if best is None or key > best[0]:
                    best = (key, rx, ry, rw, rh, c, v)
    if best:
        _k, rx, ry, rw, rh, c, v = best
        print("\n最佳敌方区域: x=%.2f y=%.2f w=%.2f h=%.2f  "
              "置信=%.3f 非空率=%.2f" % (rx, ry, rw, rh, c, v))

    print("\n=== 我方立绘：网格搜索 ===")
    best2 = None
    for rx in (0.15, 0.20, 0.23, 0.25):
        for ry in (0.45, 0.50, 0.54, 0.60):
            for rw, rh in ((0.16, 0.26), (0.20, 0.30), (0.25, 0.35)):
                if rx + rw > 0.60:
                    continue
                c, v, n = score_region(rx, ry, rw, rh)
                if n < 10:
                    continue
                key = (v, c)
                if best2 is None or key > best2[0]:
                    best2 = (key, rx, ry, rw, rh, c, v)
    if best2:
        _k, rx, ry, rw, rh, c, v = best2
        print("\n最佳我方区域: x=%.2f y=%.2f w=%.2f h=%.2f  "
              "置信=%.3f 非空率=%.2f" % (rx, ry, rw, rh, c, v))

    if "--save" in sys.argv and best and best2:
        out = {"foe": {"x": best[1], "y": best[2], "w": best[3], "h": best[4]},
               "self": {"x": best2[1], "y": best2[2],
                        "w": best2[3], "h": best2[4]},
               "note": "百分比坐标，基于客户区；由 calibrate_portrait_region.py 生成"}
        p = os.path.join(ROOT, "config", "portrait_region.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            json.dumps(out, ensure_ascii=False, indent=1))
        print("\n已写入 %s" % p)


if __name__ == "__main__":
    main()
