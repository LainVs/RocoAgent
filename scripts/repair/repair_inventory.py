# -*- coding: utf-8 -*-
"""Inventory: every damaged line, classified as comment / docstring / literal,
with the pyc string that should be restored where applicable.

ASCII survives the cp936 round-trip untouched, so each damaged line still has
its exact ASCII skeleton.  That is the anchor we match on.
"""
from __future__ import annotations

import io
import marshal
import os
import re
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


def main():
    text = io.open(SRC, encoding="utf-8", errors="replace").read()
    lines = text.split("\n")
    strings = load_strings()

    # lines still carrying damage
    bad = [i for i, l in enumerate(lines) if BAD in l]
    print("lines still damaged:", len(bad))

    # classify: comment vs inside a string literal (code)
    in_tri = False
    TQ = '"""'
    comments, literals = [], []
    for i, l in enumerate(lines):
        if BAD in l:
            s = l.strip()
            if not in_tri and s.startswith("#"):
                comments.append(i)
            elif in_tri:
                comments.append(i)          # damaged docstring prose
            elif s.startswith(TQ):
                comments.append(i)
            else:
                literals.append(i)
        if l.count(TQ) % 2:
            in_tri = not in_tri

    print("  comment/docstring prose:", len(comments))
    print("  code lines w/ literals :", len(literals))
    print()
    for i in literals:
        l = lines[i]
        # ASCII skeleton of the damaged line
        skel = "".join(ch for ch in l if ord(ch) < 128)
        print("%5d  %s" % (i + 1, l.strip()[:100]))
        print("        ascii: %r" % skel.strip()[:100])
        # candidate pyc strings: same ASCII skeleton, longer
        cands = [s for s in strings
                 if "".join(ch for ch in s if ord(ch) < 128) == skel.strip()]
        if len(cands) == 1:
            print("        PY C: %r" % cands[0][:100])
        elif cands:
            print("        PY C: %d candidates %r" % (len(cands), cands[0][:60]))
        else:
            print("        PY C: (no skeleton match)")
        print()


if __name__ == "__main__":
    main()
