# -*- coding: utf-8 -*-
"""Stage 1: restore every damaged docstring from the pre-damage pyc.

Key insight: the cp936 damage only substituted *characters*. Newlines are
ASCII and survived, so the damaged file still occupies exactly the same number
of lines as the good source.  That makes the repair exact and index-free of
guesswork:

    docstring block = [signature_end + 1, signature_end + rendered_len]

where rendered_len is computed from the pyc docstring itself.  No quote
counting (the closing \"\"\" is one of the things the damage destroyed).

Run with --apply to write; default is a dry run.
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
TQ = '"""'


def load_docs():
    code = marshal.loads(open(PYC, "rb").read()[16:])
    docs = {}

    def walk(co, qn=""):
        name = (qn + "/" if qn else "") + co.co_name
        if co.co_consts and isinstance(co.co_consts[0], str):
            docs[name] = co.co_consts[0]
        for c in co.co_consts:
            if isinstance(c, types.CodeType):
                walk(c, name)

    walk(code)
    return docs


def render_doc(doc, indent):
    pad = " " * indent
    if "\n" not in doc:
        return ['%s%s%s%s' % (pad, TQ, doc, TQ)]
    body = doc[:-1] if doc.endswith("\n") else doc
    parts = body.split("\n")
    out = ['%s%s%s' % (pad, TQ, parts[0])]
    for p in parts[1:]:
        out.append(pad + p if p else "")
    out.append(pad + TQ)
    return out


def signature_end(lines, def_idx):
    """Index of the line that ends the def/class signature."""
    depth = 0
    for j in range(def_idx, min(len(lines), def_idx + 25)):
        s = lines[j]
        depth += s.count("(") - s.count(")")
        depth += s.count("[") - s.count("]")
        if depth <= 0 and s.rstrip().endswith(":"):
            return j
    return None


def main():
    apply = "--apply" in sys.argv
    text = io.open(SRC, encoding="utf-8", errors="replace").read()
    lines = text.split("\n")
    docs = load_docs()

    defs = []
    for i, l in enumerate(lines):
        m = re.match(r"^(\s*)(?:def|class)\s+(\w+)", l)
        if m:
            defs.append((i, len(m.group(1)), m.group(2)))

    fixes = []
    for idx, indent, name in defs:
        cands = [(k, v) for k, v in docs.items()
                 if (k == name or k.endswith("/" + name)) and v
                 and v not in text]
        if not cands:
            continue
        sig = signature_end(lines, idx)
        if sig is None:
            print("  !! no signature end for %s (line %d)" % (name, idx + 1))
            continue
        # pick the candidate whose first line still matches what survived
        cur_block_head = "\n".join(lines[sig + 1:sig + 4])
        best = None
        for k, v in cands:
            head = v.split("\n")[0][:10]
            score = 1 if head and head in cur_block_head else 0
            if best is None or score > best[0]:
                best = (score, k, v)
        k, v = best[1], best[2]
        rendered = render_doc(v, indent + 4)
        start = sig + 1
        end = start + len(rendered) - 1
        if end >= len(lines):
            print("  !! span past EOF for %s" % name)
            continue
        fixes.append((start, end, rendered, k))

    # overlap guard: two defs must never claim the same lines
    fixes.sort()
    overlap = []
    for a, b in zip(fixes, fixes[1:]):
        if a[1] >= b[0]:
            overlap.append((a[3], a[0] + 1, a[1] + 1, b[3], b[0] + 1))
    print("docstring blocks to rewrite:", len(fixes))
    if overlap:
        print("!! OVERLAPS:")
        for o in overlap:
            print("   %s lines %d-%d  vs  %s at %d" % o)
        return

    for start, end, rendered, k in fixes:
        print("  lines %4d-%-4d %-44s %r"
              % (start + 1, end + 1, k, rendered[0][:52]))

    if not apply:
        print("\n(dry run; pass --apply to write)")
        return

    for start, end, rendered, _k in sorted(fixes, key=lambda f: -f[0]):
        lines[start:end + 1] = rendered

    out = "\n".join(lines)
    print("\nremaining U+FFFD:", out.count(BAD))
    io.open(SRC, "w", encoding="utf-8", newline="\n").write(out)
    try:
        compile(out, "robot.py", "exec")
        print("COMPILES OK")
    except SyntaxError as e:
        print("still broken: line %d: %s" % (e.lineno, e.msg))
        L = out.split("\n")
        for i in range(max(0, e.lineno - 3), min(len(L), e.lineno + 2)):
            print("  %5d %s" % (i + 1, L[i][:110]))


if __name__ == "__main__":
    main()
