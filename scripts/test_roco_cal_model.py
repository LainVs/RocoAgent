# -*- coding: utf-8 -*-
"""验证 roco-cal 的精灵识别模型能否直接用。

模型：all_classes.onnx  输入 [1,3,86,76]  输出 [1,620]
预处理（从 inference.rs 的 classifier_tensor 逐行翻译）：
  1. 等比缩放到不超过 76x86（Lanczos）
  2. 居中填充到 76x86，空白处为黑
  3. /255 归一化，通道优先
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


def tensor_from_rgba(img):
    """img: HxWx4 uint8 (RGBA) 或 HxWx3。返回 [1,3,86,76] float32。"""
    import cv2

    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        rgb = img[:, :, :3].astype(np.float32) * alpha   # 预乘
        img = rgb.astype(np.uint8)
    h, w = img.shape[:2]
    nw, nh = w, h
    if w > W or h > H:
        aspect = w / float(h)
        if W / float(H) >= aspect:
            nh = H
            nw = max(1, int(round(H * aspect)))
        else:
            nw = W
            nh = max(1, int(round(W / aspect)))
        img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LANCZOS4)

    plane = W * H
    out = np.zeros((3, H, W), dtype=np.float32)
    ox = (W - nw) // 2
    oy = (H - nh) // 2
    for c in range(3):
        out[c, oy:oy + nh, ox:ox + nw] = img[:, :, c].astype(np.float32) / 255.0
    return out[None, ...]


def main():
    import cv2
    import onnxruntime as ort

    sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    classes = json.load(io.open(CLASSES, encoding="utf-8"))
    print("类别数: %d" % len(classes))

    # 用我们已标注的头像做真值测试
    tests = []
    for side, d in (("foe", os.path.join(ROOT, "assets", "portraits_foe")),
                    ("self", os.path.join(ROOT, "assets", "portraits"))):
        for p in glob.glob(os.path.join(d, "*.png")):
            name = os.path.basename(p)[:-4].split("#")[0]
            if name.startswith("_"):
                continue
            tests.append((p, name, side))

    print("可用测试样本: %d" % len(tests))
    if not tests:
        return

    hit = 0
    tested = 0
    for p, want, side in tests:
        # cv2.imread 读不了中文路径，必须用 fromfile + imdecode
        try:
            buf = np.fromfile(p, dtype=np.uint8)
            im = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
        except Exception:
            continue
        if im is None:
            continue
        if im.ndim == 2:
            im = cv2.cvtColor(im, cv2.COLOR_GRAY2RGB)
        t = tensor_from_rgba(im)
        logits = sess.run(None, {"images": t})[0][0]
        # softmax
        e = np.exp(logits - logits.max())
        prob = e / e.sum()
        idx = int(prob.argmax())
        label = classes[idx]
        # 类别名形如 "001迪莫"，去掉前缀数字
        got = label.lstrip("0123456789")
        ok = got == want or want in got or got in want
        hit += 1 if ok else 0
        tested += 1
        print("  %-4s %-16s -> %-18s %.2f  %s"
              % (side, want, label, prob[idx], "OK" if ok else "MISS"))

    print("\n命中 %d / %d" % (hit, tested))


if __name__ == "__main__":
    main()
