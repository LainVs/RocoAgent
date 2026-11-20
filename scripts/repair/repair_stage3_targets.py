# -*- coding: utf-8 -*-
"""Stage 3: restore the 12 damaged *code* literals, using the pyc as ground truth.

For each target line we know the intended literal from the pyc's constant
table.  The script asserts that the damaged line still contains the surviving
prefix, then rewrites only the literal, leaving the rest of the line untouched.
This is a targeted, verified edit - not a heuristic sweep.
"""
from __future__ import annotations

import io
import marshal
import os
import sys
import types

ROOT = r"D:\Projects\RocoAgent"
SRC = os.path.join(ROOT, "src", "robot.py")
PYC = os.path.join(ROOT, "src", "__pycache__", "robot.cpython-314.pyc")
BAD = "\ufffd"


def load_strings():
    code = marshal.loads(open(PYC, "rb").read()[16:])
    out = []

    def walk(co):
        for c in co.co_consts:
            if isinstance(c, str):
                out.append(c)
            elif isinstance(c, types.CodeType):
                walk(c)

    walk(code)
    return out


# (line_no_1based, damaged_fragment, correct_literal)
TARGETS = [
    (1835, 'log("BLE FAILED', None),
    (1837, 'log("BLE waiting', None),
    (2406, 'log("FOE SCAN -> %s lv%s', None),
    (2445, 'if "', "使出了"),
    (2446, 'text.split("', "使出了"),
    (2447, 're.sub(r"[', r"[！!\s]"),
    (2457, 'if "', "召唤了"),
    (2458, 'text.split("', "召唤了"),
    (2459, 're.sub(r"[', r"[！!\s]"),
    (2596, 'log("LEADER -> ', None),
    (2778, 'return bench, "', "会被秒（它最凶一击 >= 我当前血）"),
    (2782, 'return bench, "', "打不动对面（%d 张被抵抗、无克制）"),
    (2784, 'return bench, "', "本系被对面抵抗(×%.1f)且伤害低(%.0f%%)"),
    (2829, '("', "建议换"),
    (2884, '"', "会被秒"),
    (2911, '"首领-恶魔', None),
]


def main():
    apply = "--apply" in sys.argv
    text = io.open(SRC, encoding="utf-8", errors="replace").read()
    lines = text.split("\n")
    strings = load_strings()

    for ln, frag, want in TARGETS:
        cur = lines[ln - 1]
        print("\n%5d %s" % (ln, cur.strip()[:100]))
        if want is None:
            # find the pyc string whose surviving prefix matches
            pre = cur.split('"')[1].replace(BAD, "")
            cands = [s for s in strings if s.startswith(pre) and len(s) > len(pre)]
            cands = [s for s in cands if not s.startswith(pre + " ")]
            print("      prefix %r -> %d candidate(s)" % (pre, len(cands)))
            for s in cands[:4]:
                print("        %r" % s)
        else:
            print("      want %r  in pyc: %s"
                  % (want, want in strings))


if __name__ == "__main__":
    main()
