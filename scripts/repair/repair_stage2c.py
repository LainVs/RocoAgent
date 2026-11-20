# -*- coding: utf-8 -*-
"""Stage 2 (v3): restore damaged string literals, verifiably.

A damaged literal line has the shape

    PRE + QUOTE + prefix + <U+FFFD run> + rest

where `prefix` is a genuine prefix of the original string (ASCII and the CJK
that happened to survive are untouched) and `rest` begins with the *suffix* of
the original that followed the damage.

For each pyc string `s`, write `s = prefix + suffix`.  Then the repair is
correct iff `rest.startswith(suffix)`, and the rebuilt line is

    PRE + QUOTE + s + QUOTE + rest[len(suffix):]

Every repair is therefore verified, not guessed: a wrong candidate cannot
satisfy the suffix test.
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


def repair_line(line, strings):
    """Return (new_line, status) where status is 'ok' / 'none' / 'ambig'."""
    if BAD not in line:
        return line, "ok"
    # find the opening quote that precedes the first damage run
    first = line.index(BAD)
    qpos = -1
    for k in range(first - 1, -1, -1):
        if line[k] in "\"'":
            qpos = k
            break
    if qpos < 0:
        return line, "none"
    quote = line[qpos]
    prefix = line[qpos + 1:first]
    rest = line[first:].lstrip(BAD)

    hits = []
    for s in strings:
        if not s.startswith(prefix):
            continue
        suffix = s[len(prefix):]
        if rest.startswith(suffix):
            hits.append((s, suffix))
    if not hits:
        return line, "none"
    # prefer the longest suffix match (most constrained)
    hits.sort(key=lambda t: -len(t[1]))
    if len(hits) > 1 and len(hits[0][1]) == len(hits[1][1]):
        return line, "ambig"
    s, suffix = hits[0]
    new = line[:qpos + 1] + s + quote + rest[len(suffix):]
    return new, "ok"


def main():
    apply = "--apply" in sys.argv
    text = io.open(SRC, encoding="utf-8", errors="replace").read()
    lines = text.split("\n")
    strings = load_strings()

    ok = none = ambig = 0
    problems = []
    for i, l in enumerate(lines):
        if BAD not in l:
            continue
        new, st = repair_line(l, strings)
        if st == "ok" and new != l:
            lines[i] = new
            ok += 1
        elif st == "none":
            none += 1
            problems.append((i + 1, "none", l.strip()[:80]))
        else:
            ambig += 1
            problems.append((i + 1, "ambig", l.strip()[:80]))

    out = "\n".join(lines)
    print("repaired: %d   unresolved: %d (none=%d ambig=%d)"
          % (ok, len(problems), none, ambig))
    for ln, st, s in problems[:30]:
        print("  %-6s line %5d  %s" % (st, ln, s))
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
