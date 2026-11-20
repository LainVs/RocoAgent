# -*- coding: utf-8 -*-
"""验证假设：roco-cal 模型认的是"角色立绘区域"，不是"头像小图"。

我们用 logs/shot/ 里的真实战斗截图测试：
  A. 用我们的头像框（2148,47,84,76）  -> 小图
  B. 用 roco-cal 的区域（15%,15%,25%,35%）-> 大图
对比两者的识别率。
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
    try:
        return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    except Exception:
        return None


def main():
    import cv2
    import onnxruntime as ort
    sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    classes = json.load(io.open(CLASSES, encoding="utf-8"))

    shots = sorted(glob.glob(os.path.join(ROOT, "logs", "shot", "*.jpg")))
    print("战斗截图: %d 张" % len(shots))
    if not shots:
        return

    # 两张图对比
    print("\n=== A. 我们的头像小框 (2148,47,84,76) ===")
    for p in shots[-5:]:
        im = read_img(p)
        if im is None:
            continue
        # shot 是 0.5 缩放（1280x800），换算
        h, w = im.shape[:2]
        sx, sy = w / 2560.0, h / 1600.0
        box = (int(2148 * sx), int(47 * sy), int(84 * sx), int(76 * sy))
        crop = im[box[1]:box[1] + box[3], box[0]:box[0] + box[2]]
        if crop.size == 0:
            continue
        lg = sess.run(None, {"images": tensor(crop)})[0][0]
        e = np.exp(lg - lg.max())
        pr = e / e.sum()
        i = int(pr.argmax())
        print("  %-24s -> %-18s %.2f"
              % (os.path.basename(p), classes[i], pr[i]))

    print("\n=== B. roco-cal 的敌方区域 (15%%,15%%,25%%,35%%) ===")
    for p in shots[-5:]:
        im = read_img(p)
        if im is None:
            continue
        h, w = im.shape[:2]
        box = (int(0.15 * w), int(0.15 * h), int(0.25 * w), int(0.35 * h))
        crop = im[box[1]:box[1] + box[3], box[0]:box[0] + box[2]]
        if crop.size == 0:
            continue
        lg = sess.run(None, {"images": tensor(crop)})[0][0]
        e = np.exp(lg - lg.max())
        pr = e / e.sum()
        i = int(pr.argmax())
        print("  %-24s -> %-18s %.2f"
              % (os.path.basename(p), classes[i], pr[i]))


if __name__ == "__main__":
    main()
