# -*- coding: utf-8 -*-
"""test_imgio.py —— 中文路径读写。

为什么值得单独测
----------------
`cv2.imread("水灵.png")` 在 Windows 上返回 None（OpenCV 走 ANSI 代码页），
而本项目的**头像文件名就是精灵名**，全是中文。这类失败**不报错**，
只是"读不到图"，上游通常当成"这张是空的"直接跳过 —— 属于沉默失效。

实测（2026-11-21）：
    cv2.imread("水灵.png")                      -> None
    cv2.imdecode(np.fromfile("水灵.png"), ...)   -> (76, 84, 3)

用法：python src/test_imgio.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import cv2          # noqa: E402

import imgio  # noqa: E402

fails = 0


def check(cond, name, detail=""):
    global fails
    if not cond:
        fails += 1
    print("  %-4s %-44s %s" % ("OK" if cond else "FAIL", name, detail))


def main():
    tmp = tempfile.mkdtemp()
    try:
        img = np.zeros((24, 32, 3), np.uint8)
        img[:, :, 2] = 200          # BGR 红
        img[4:10, 6:20] = 255

        print("[1] 中文路径读写")
        for name in ("水灵.png", "迪莫#2.png", "黑化加尔（黑化的样子）.png",
                     "普通英文.png"):
            p = os.path.join(tmp, "深目录", name)
            okw = imgio.write(p, img)
            check(okw, "write %s" % name)
            back = imgio.read(p)
            check(back is not None and back.shape == img.shape,
                  "read %s" % name,
                  "None" if back is None else str(back.shape))
            if back is not None:
                check(int(np.abs(back.astype(int) - img.astype(int)).max()) == 0,
                      "像素无损 %s" % name)

        print()
        print("[2] 对照：cv2.imread 在中文路径上会失败")
        zh = os.path.join(tmp, "对照水灵.png")
        imgio.write(zh, img)
        direct = cv2.imread(zh)
        # 这不是断言"一定失败"（不同 OpenCV 构建可能修复），而是记录现象
        check(True, "cv2.imread(中文) = %s"
              % ("None（复现）" if direct is None else "可读（该构建已修）"))

        print()
        print("[3] 灰度 flag 透传")
        gp = os.path.join(tmp, "灰度图.png")
        imgio.write(gp, img)
        g = imgio.read(gp, cv2.IMREAD_GRAYSCALE)
        check(g is not None and g.ndim == 2, "灰度读入是 2 维",
              "None" if g is None else str(g.shape))

        print()
        print("[4] 失败路径不抛异常")
        check(imgio.read(os.path.join(tmp, "不存在.png")) is None,
              "不存在的文件返回 None")
        check(imgio.write(os.path.join(tmp, "x.png"), None) is False,
              "写 None 返回 False")
        # 空文件
        empty = os.path.join(tmp, "空.png")
        open(empty, "wb").close()
        check(imgio.read(empty) is None, "空文件返回 None")

        print()
        print("[5] 自动建目录")
        deep = os.path.join(tmp, "a", "b", "c", "水灵.png")
        check(imgio.write(deep, img) and os.path.exists(deep),
              "多层中文目录自动创建")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        print("%d FAILURE(S)" % fails)
        return 1
    print("all imgio cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
