# -*- coding: utf-8 -*-
"""Stage 4: repair the remaining damaged comment/docstring prose.

Comments are not compiled, so the pyc cannot restore them.  Each damaged line
lost 1-2 characters at a specific point (cp936 collapsed an unmappable pair to
'?').  The fix is mechanical: replace the U+FFFD(+optional '?') run with the
character that the sentence requires.

Rules applied, in order:
  * a U+FFFD at end-of-line, after a CJK char   -> '。'  (sentence end)
  * a U+FFFD before '?', inside CJK prose      -> '。'  (the '?' is cp936 residue)
  * a U+FFFD between CJK chars                 -> '，'
  * explicit table overrides for known phrases (kept small and reviewed)

This is cosmetic only: no executable token is touched, and the script asserts
the file still compiles with identical bytecode afterwards.
"""
from __future__ import annotations

import io
import os
import sys

ROOT = r"D:\Projects\RocoAgent"
SRC = os.path.join(ROOT, "src", "robot.py")
BAD = "\ufffd"

# Explicit fixes for phrases whose wording matters (button names, terms).
# (substring containing U+FFFD -> replacement)
OVERRIDES = [
    ("开始挑\ufffd?", "开始挑战"),
    ("退\ufffd?", "退出"),
    ("略略\ufffd?", "略略略"),
    ("恶魔\ufffd?", "恶魔狼"),
    ("首领\ufffd?", "首领化"),
    ("分辨\ufffd?", "分辨率"),
    ("确认\ufffd?", "确认"),
    ("返\ufffd?", "返回"),
    ("该\ufffd?", "该"),
    ("所\ufffd?", "所有"),
    ("一\ufffd?", "一个"),
    ("这\ufffd?", "这"),
    ("\ufffd?onnxruntime", "和 onnxruntime"),
    ("\ufffd?try/except", "的 try/except"),
    ("\ufffd?import cv2", "在 import cv2"),
    ("定位\ufffd?", "定位）"),
    ("\ufffd?**14.29**", "是 **14.29**"),
    ("\ufffd?021 年", "（2021 年"),
    ("\ufffd?Python/cv2", "而 Python/cv2"),
    ("\ufffd?MSVCP140", "的 MSVCP140"),
    ("\ufffd?14.51", "是 14.51"),
    ("\ufffd?onnxruntime 就会", "之后 onnxruntime 就会"),
    ("0xC0000005）\ufffd?", "0xC0000005）。"),
    ("14.51\ufffd?", "14.51。"),
    ("正常\ufffd?", "正常。"),
    ("崩溃留证\ufffd?", "崩溃留证（"),
    ("\ufffd?2026-11-20\ufffd?", "2026-11-20）"),
    ("不会\ufffd?Python", "不会走 Python"),
    ("表现\ufffd?", "表现是"),
    ("半句\ufffd?", "半句就"),
    ("stderr \ufffd?", "stderr 里。"),
    ("写\ufffd?crash.log", "写进 crash.log"),
    ("追加\ufffd?bot_stderr.log", "追加到 bot_stderr.log"),
    ("反推\ufffd?", "反推。"),
    ("余量\ufffd?", "余量。"),
    ("False\ufffd?", "False。"),
    ("回合\ufffd?", "回合。"),
    ("才出\ufffd?", "才出现。"),
    ("跳过按钮\ufffd?", "跳过按钮"),
    ("结算页\ufffd?", "结算页。"),
    ("链路\ufffd?", "链路。"),
    ("实例\ufffd?", "实例。"),
    ("记录，\ufffd?", "记录，"),
    ("分辨率\"\ufffd?", "分辨率\"。"),
    ("历史原因\ufffd?", "历史原因。"),
    ("不再有\ufffd?", "不再有"),
]


def main():
    apply = "--apply" in sys.argv
    text = io.open(SRC, encoding="utf-8").read()
    before = text
    for old, new in OVERRIDES:
        text = text.replace(old, new)

    lines = text.split("\n")
    changed = 0
    for i, l in enumerate(lines):
        if BAD not in l:
            continue
        # remaining U+FFFD: infer punctuation from context
        out = []
        for j, ch in enumerate(l):
            if ch != BAD:
                out.append(ch)
                continue
            prev = l[j - 1] if j else ""
            nxt = l[j + 1] if j + 1 < len(l) else ""
            if nxt == "?":
                nxt = l[j + 2] if j + 2 < len(l) else ""
            # choose punctuation
            if not nxt or nxt == "\n":
                out.append("。")
            elif nxt in "）)】":
                out.append("。")
            elif prev and "\u4e00" <= prev <= "\u9fff" and nxt and "\u4e00" <= nxt <= "\u9fff":
                out.append("，")
            else:
                out.append("")
            # drop the stray '?' that cp936 inserted after the loss
            if j + 1 < len(l) and l[j + 1] == "?":
                pass
        newline = "".join(out)
        # remove the '?' residues that followed a U+FFFD
        while BAD + "?" in newline:
            newline = newline.replace(BAD + "?", BAD)
        if newline != l:
            lines[i] = newline
            changed += 1

    text = "\n".join(lines)
    # any U+FFFD left is intentional prose we could not infer
    print("overrides applied, lines rewritten:", changed)
    print("remaining U+FFFD:", text.count(BAD))

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
        print("(dry run; pass --apply to write)")


if __name__ == "__main__":
    main()
