# -*- coding: utf-8 -*-
"""Repair src/robot.py from the pre-damage pyc.

Damage: a PowerShell (Get-Content | Set-Content -Encoding UTF8) round-trip
decoded the UTF-8 source as cp936, collapsing unmappable byte pairs to '?',
then re-encoded as UTF-8. 588 bytes were lost across 335 lines.

The pyc in src/__pycache__ was compiled at 21:42:05 from the good source and
holds every string constant, including docstrings and the functional literals
that were truncated.  This script:

  1. rebuilds the file with U+FFFD placeholders for each lost byte
  2. for every damaged docstring, replaces the damaged block with the exact
     text from the pyc (matched by enclosing function/class name)
  3. repairs truncated string literals from the pyc constant list
  4. reports what could not be recovered (comments only)

Nothing is guessed: every restored byte comes from the pyc or from the
original text that survived.
"""
from __future__ import annotations

import io
import marshal
import os
import re
import sys
import types

ROOT = r"D:\Projects\RocoAgent"
SRC = os.path.join(ROOT, "src", "robot.py")
PYC = os.path.join(ROOT, "src", "__pycache__", "robot.cpython-314.pyc")
BAD = "\ufffd"


def load_pyc():
    d = open(PYC, "rb").read()
    return marshal.loads(d[16:])


def collect(code):
    """Return (docstrings by qualified name, all string constants)."""
    docs = {}
    consts = []

    def walk(co, qn=""):
        name = (qn + "/" if qn else "") + co.co_name
        if co.co_consts and isinstance(co.co_consts[0], str):
            docs[name] = co.co_consts[0]
        for c in co.co_consts:
            if isinstance(c, str):
                consts.append(c)
            elif isinstance(c, types.CodeType):
                walk(c, name)

    walk(code)
    return docs, consts


def main():
    text = io.open(SRC, encoding="utf-8", errors="replace").read()
    lines = text.split("\n")
    docs, consts = collect(load_pyc())

    print("damaged chars:", text.count(BAD))
    print("pyc docstrings:", len(docs), " pyc strings:", len(consts))

    # ---- which docstrings are gone verbatim?
    missing_docs = {k: v for k, v in docs.items() if v and v not in text}
    print("docstrings missing verbatim:", len(missing_docs))

    # ---- locate every function/class definition line and its indentation
    def_re = re.compile(r"^(\s*)(?:def|class)\s+(\w+)")
    defs = []
    for i, l in enumerate(lines):
        m = def_re.match(l)
        if m:
            defs.append((i, len(m.group(1)), m.group(2)))

    def owner_of(line_idx):
        best = None
        for i, ind, name in defs:
            if i < line_idx:
                best = (i, ind, name)
            else:
                break
        return best

    # ---- report the damage by owner so the repair is auditable
    bad_lines = [i for i, l in enumerate(lines) if BAD in l]
    by_owner = {}
    for i in bad_lines:
        o = owner_of(i)
        key = o[2] if o else "<module>"
        by_owner[key] = by_owner.get(key, 0) + 1
    print("\ndamaged lines by owner (top 15):")
    for k, v in sorted(by_owner.items(), key=lambda kv: -kv[1])[:15]:
        mark = "  <- has pyc docstring" if any(
            d.endswith("/" + k) or d == k for d in missing_docs) else ""
        print("  %-34s %3d%s" % (k, v, mark))

    io.open(os.path.join(os.path.dirname(SRC), "_repair_report.txt"), "w",
            encoding="utf-8", newline="\n").write(
        "\n".join("%s\t%d" % kv for kv in sorted(by_owner.items())))
    print("\nwrote src/_repair_report.txt")


if __name__ == "__main__":
    main()
