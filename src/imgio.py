# -*- coding: utf-8 -*-
"""imgio.py —— 支持中文路径的图片读写。

为什么需要
----------
`cv2.imread(path)` 在 Windows 上**读不了中文路径**：OpenCV 内部走
ANSI 代码页，非 ASCII 文件名会直接返回 None（并打印
`can't open/read file: check file path/integrity`）。

本项目的头像库文件名就是精灵名，全是中文：

    assets/portraits_foe/水灵.png
    assets/portraits_foe/迪莫#2.png

实测（2026-11-21）：

    cv2.imread("水灵.png")                        -> None
    cv2.imdecode(np.fromfile("水灵.png"), ...)     -> (76, 84, 3)  ✓

`cv2.imwrite` 同理。所以所有涉及"文件路径"的图片读写都应该走这里。

为什么不用 `images.py` 之类的名字
-------------------------------
`portraits.py` 里已有同名用途的 `_imread/_imwrite`（那是它自己的私有实现）。
这里独立出来是给 `ui_server` / `ocr_batch` / `skill_names` 这些**不 import
portraits** 的模块用的。两边实现一致，但不去动已经跑通的 portraits.py。

用法
----
    import imgio
    im = imgio.read(path)                       # 缺省 IMREAD_COLOR
    gray = imgio.read(path, cv2.IMREAD_GRAYSCALE)
    imgio.write(path, bgr)
"""
from __future__ import annotations

import os


def read(path, flags=None):
    """读图，支持中文路径。失败返回 None（不抛异常）。

    flags 缺省 cv2.IMREAD_COLOR。传入 cv2.IMREAD_GRAYSCALE 等也可以。
    """
    import cv2
    import numpy as np
    try:
        buf = np.fromfile(path, dtype=np.uint8)
    except (OSError, ValueError):
        return None
    if buf.size == 0:
        return None
    try:
        return cv2.imdecode(buf, cv2.IMREAD_COLOR if flags is None else flags)
    except Exception:
        return None


def write(path, img, params=None):
    """写图，支持中文路径。返回是否成功。"""
    import cv2
    if img is None:
        return False
    ext = os.path.splitext(path)[1] or ".png"
    try:
        ok, buf = cv2.imencode(ext, img, params or [])
        if not ok:
            return False
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        buf.tofile(path)
        return True
    except (OSError, ValueError):
        return False
