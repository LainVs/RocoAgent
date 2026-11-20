# -*- coding: utf-8 -*-
"""用真实战斗帧 + 角色立绘区域测试 roco-cal 模型。

从 234153_T2_switch3.jpg 目视确认：
    对面精灵（帕帕，紫白龙）  约 x 790-1060, y 270-510 （1280x800 图）
    我方精灵（捕尘长绒）      约 x 300-500,  y 430-640
换算成百分比后在多张真实战斗帧上测。
"""
from __future__ import annotations

import glob
import io
import json
import os
import sys

import numpy as np

ROOT = r"D:\Projects\RocoAgent"
MODEL = (r"D:\GameData\洛克王国世界\roco-cal-latest\src-tauri"
         r"\resources\image-classifier\all_classes.onnx")
CLASSES = (r"D:\GameData\洛克王国世界\roco-cal-latest\src-tauri"
           r"\resources\image-classifier\all_classes.classes.json")
W, H = 76, 86


def tensor(img):
    import cv2
    if img.ndim == 3 and img.shape[2] == 4:
        a = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3].astype(np.float32) * a).astype(np.uint8)
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
    print("战斗截图: %d 张（0.5 缩放，1280x800）" % len(shots))

    def predict(crop):
        if crop is None or crop.size == 0:
            return None, 0.0
        lg = sess.run(None, {"images": tensor(crop)})[0][0]
        e = np.exp(lg - lg.max())
        pr = e / e.sum()
        i = int(pr.argmax())
        return classes[i], float(pr[i])

    # 区域（百分比，基于目视标定）
    REGIONS = {
        "对面立绘 62-83%,34-64%": (0.62, 0.34, 0.21, 0.30),
        "对面立绘 55-90%,25-70%": (0.55, 0.25, 0.35, 0.45),
        "我方立绘 23-39%,54-80%": (0.23, 0.54, 0.16, 0.26),
        "我方立绘 18-45%,45-85%": (0.18, 0.45, 0.27, 0.40),
        "roco-cal 默认 15%,15%,25%,35%": (0.15, 0.15, 0.25, 0.35),
    }

    for p in shots[-10:]:
        im = read_img(p)
        if im is None:
            continue
        h, w = im.shape[:2]
        print("\n%s" % os.path.basename(p))
        for name, (rx, ry, rw, rh) in REGIONS.items():
            x, y = int(rx * w), int(ry * h)
            bw, bh = int(rw * w), int(rh * h)
            lab, conf = predict(im[y:y + bh, x:x + bw])
            print("    %-32s -> %-18s %.2f" % (name, lab, conf))


if __name__ == "__main__":
    main()
