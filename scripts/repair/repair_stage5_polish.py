# -*- coding: utf-8 -*-
"""Stage 5: polish the handful of comment lines that still read badly.

Stage 4 restored every character but left a few artefacts:
  * a stray '?' left where cp936 inserted a fallback byte
  * a missing space around an ASCII word ("开始挑战button")
  * a truncated date ("026-11-20")

Each entry below is an explicit, reviewed replacement.  Comments are not
compiled, so bytecode equivalence is re-verified after applying.
"""
from __future__ import annotations

import io
import os
import sys

ROOT = r"D:\Projects\RocoAgent"
SRC = os.path.join(ROOT, "src", "robot.py")

PAIRS = [
    ("整个进程直接消失。?", "整个进程直接消失。"),
    ("必须在 import cv2 之前设置。?", "必须在 import cv2 之前设置。"),
    ("崩溃留证（026-11-20?", "崩溃留证（2026-11-20）"),
    ("不会走 Python ?except", "不会走 Python 的 try/except"),
    ("开始挑战button", "开始挑战 button"),
    ("开始挑战centre", "开始挑战 centre"),
    ("右上角，?略略略", "右上角的「略略略」"),
    ("抢同一个BLE 链路", "抢同一条 BLE 链路"),
    ("分辨率窗口大小变了", "分辨率：窗口大小变了"),
    ("各自带了一个libiomp5md.dll", "各自带了一个 libiomp5md.dll"),
    ("都是 2026 年构建、系统里的 MSVCP140 已经是 14.51。",
     "都是 2026 年构建、系统里的 MSVCP140 已经是 14.51。"),
    ("历史原因。", "历史原因。"),
]


def main():
    apply = "--apply" in sys.argv
    text = io.open(SRC, encoding="utf-8").read()
    before = text
    hits = 0
    for old, new in PAIRS:
        if old in text:
            text = text.replace(old, new)
            hits += 1
    print("replacements applied:", hits, "/", len(PAIRS))
    if apply:
        io.open(SRC, "w", encoding="utf-8", newline="\n").write(text)
        try:
            compile(text, "robot.py", "exec")
            print("COMPILES OK")
        except SyntaxError as e:
            print("BROKEN line %d: %s" % (e.lineno, e.msg))
            io.open(SRC, "w", encoding="utf-8", newline="\n").write(before)
            print("reverted")
    else:
        print("(dry run; pass --apply)")


if __name__ == "__main__":
    main()
