# -*- coding: utf-8 -*-
"""用原始分辨率战斗帧测试 roco-cal 模型，找出正确的裁剪区域。

原始帧 2586x1671（窗口尺寸，含边框）。
roco-cal 的 enemyImage 是客户区百分比 (15%,15%,25%,35%)。
我们的头像框是客户区像素 (2148,47,84,76)。

需要先确定客户区在窗口内的偏移，再逐一试各种区域。
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

    frames = sorted(glob.glob(os.path.join(ROOT, "logs", "frames2", "*.png")))[-8:]
    print("原始帧: %d 张, 尺寸 %s" % (len(frames), read_img(frames[0]).shape if frames else "?"))

    # 客户区偏移（窗口 2586x1671，客户区 2560x1600，左上各偏 13/58 —— 见 robot 注释）
    OX, OY = 13, 58

    def run(name, crop):
        if crop is None or crop.size == 0:
            return
        lg = sess.run(None, {"images": tensor(crop)})[0][0]
        e = np.exp(lg - lg.max())
        pr = e / e.sum()
        top = np.argsort(pr)[-3:][::-1]
        s = "  ".join("%s %.2f" % (classes[i], pr[i]) for i in top)
        print("    %-22s %s" % (name, s))

    for p in frames[:4]:
        im = read_img(p)
        if im is None:
            continue
        print("\n%s %s" % (os.path.basename(p), im.shape))
        h, w = im.shape[:2]

        # A. 我们的头像框（客户区坐标 -> 窗口坐标）
        x, y, bw, bh = 2148 + OX, 47 + OY, 84, 76
        run("我们的头像框 84x76", im[y:y + bh, x:x + bw])

        # B. roco-cal 的敌方区域（客户区百分比）
        cw, ch = 2560, 1600
        rx, ry, rw, rh = int(0.15 * cw), int(0.15 * ch), int(0.25 * cw), int(0.35 * ch)
        run("roco-cal 敌方 25%x35%",
            im[OY + ry:OY + ry + rh, OX + rx:OX + rx + rw])

        # C. 头像框放大 3 倍（假设模型要更大上下文）
        run("头像框放大3倍",
            im[max(0, y - bh):y + bh * 2, max(0, x - bw):x + bw * 2])

        # D. 敌方角色大致位置（右上，PvP 里敌人站在画面中右）
        run("中右角色区",
            im[int(0.20 * h):int(0.75 * h), int(0.45 * w):int(0.85 * w)])


if __name__ == "__main__":
    main()
