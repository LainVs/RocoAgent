# -*- coding: utf-8 -*-
"""Stage 2: repair truncated string literals from the pre-damage pyc.

The damage ate characters *inside* literals, including the closing quote, so a
line like

    log("BLE FAILED - 180s 内没等到板子，退�?)

lost both a character and the quote.  Every such literal still exists verbatim
in the pyc.  For each damaged line we therefore:

  1. strip the U+FFFD placeholders to get the surviving prefix
  2. find the unique pyc string constant that starts with that prefix
     (a short ASCII-anchored prefix is enough; we require uniqueness)
  3. rewrite the literal in place, keeping any trailing syntax (`, x)` etc.)

Anything that cannot be matched uniquely is reported, never guessed.
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

# quote characters that can open/close a Python string
QUOTES = ('"""', "'''", '"', "'")


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


def literal_spans(line):
    """[(start, end, quote)] for every string literal on this line.

    Deliberately simple: we only need spans that are damaged, and a damaged
    literal has lost its closing quote, so we also emit open-ended spans.
    """
    spans = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch in "\"'":
            q = ch * 3 if line[i:i + 3] == ch * 3 else ch
            j = line.find(q, i + len(q))
            if j == -1:
                spans.append((i, n, q))          # unterminated: runs to EOL
                break
            spans.append((i, j + len(q), q))
            i = j + len(q)
            continue
        if ch == "#":
            break                                # rest is a comment
        i += 1
    return spans


def main():
    apply = "--apply" in sys.argv
    text = io.open(SRC, encoding="utf-8", errors="replace").read()
    lines = text.split("\n")
    strings = load_strings()

    # index by first 6 chars for fast candidate lookup
    by_head = {}
    for s in strings:
        if len(s) >= 4:
            by_head.setdefault(s[:6], []).append(s)

    fixed = 0
    unresolved = []
    for i, l in enumerate(lines):
        if BAD not in l:
            continue
        for start, end, q in literal_spans(l):
            body = l[start + len(q):end]
            if BAD not in body:
                continue
            prefix = body.replace(BAD, "")
            # strip a trailing comma/paren that the span may have swallowed
            core = prefix.rstrip()
            cands = [s for s in strings
                     if s.startswith(core[:min(len(core), 24)])]
            if len(cands) == 1:
                new_body = cands[0]
                new_l = l[:start] + q + new_body + q + l[end:]
                lines[i] = new_l
                l = new_l
                fixed += 1
            else:
                unresolved.append((i + 1, core[:60], len(cands)))

    out = "\n".join(lines)
    print("literals fixed:", fixed)
    print("unresolved:", len(unresolved))
    for ln, core, n in unresolved[:25]:
        print("  line %5d cands=%d  %r" % (ln, n, core))
    print("\nremaining U+FFFD:", out.count(BAD))

    if apply:
        io.open(SRC, "w", encoding="utf-8", newline="\n").write(out)
        try:
            compile(out, "robot.py", "exec")
            print("COMPILES OK")
        except SyntaxError as e:
            print("still broken: line %d: %s" % (e.lineno, e.msg))
            L = out.split("\n")
            for k in range(max(0, e.lineno - 3), min(len(L), e.lineno + 2)):
                print("  %5d %s" % (k + 1, L[k][:110]))
    else:
        print("(dry run; pass --apply to write)")


if __name__ == "__main__":
    main()
