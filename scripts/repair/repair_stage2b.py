# -*- coding: utf-8 -*-
"""Stage 2 (v2): repair damaged string literals by prefix/suffix matching.

The damage replaced characters with U+FFFD, including closing quotes.  Rather
than trying to parse the broken literal, match against the pyc's string table:

    text before the first U+FFFD in the literal  ==  a PREFIX of the original
    text after the last U+FFFD in the literal    ==  a SUFFIX of the original

so the original is the unique pyc string satisfying both.  This is exact and
never guesses: ambiguous or unmatched cases are reported, not edited.
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


def best_match(prefix, suffix, strings):
    """Pyc strings that start with `prefix` and end with `suffix`."""
    hits = []
    for s in strings:
        if not s.startswith(prefix):
            continue
        if suffix and not s.endswith(suffix):
            continue
        hits.append(s)
    return hits


def main():
    apply = "--apply" in sys.argv
    text = io.open(SRC, encoding="utf-8", errors="replace").read()
    lines = text.split("\n")
    strings = load_strings()
    print("pyc strings:", len(strings))

    fixed = 0
    unresolved = []

    for i, l in enumerate(lines):
        if BAD not in l:
            continue
        # Try every contiguous damaged run: take the prefix before the run and
        # the suffix after it, bounded by quote/space heuristics.
        parts = l.split(BAD)
        # candidate: expand prefix leftwards to the nearest quote
        for pi in range(len(parts) - 1):
            pre = parts[pi]
            suf = parts[pi + 1]
            # trim to the last quote on the left, first quote on the right
            qs = [k for k, ch in enumerate(pre) if ch in "\"'"]
            if not qs:
                continue
            qpos = qs[-1]
            quote = pre[qpos]
            core_pre = pre[qpos + 1:]
            # on the right: cut at the first quote that closes the literal
            m = re.search(r"[\"']", suf) if (re := __import__("re")) else None
            core_suf = suf[:m.start()] if m else suf
            # trailing code after the closing quote must be preserved
            tail = suf[m.start():] if m else ""
            hits = best_match(core_pre, core_suf, strings)
            if len(hits) != 1:
                unresolved.append((i + 1, core_pre[-40:], core_suf[:20],
                                   len(hits)))
                continue
            new_l = pre[:qpos + 1] + hits[0] + quote + tail
            # stitch remaining parts back
            rest = BAD.join(parts[pi + 2:])
            new_l = new_l + rest
            lines[i] = new_l
            l = new_l
            fixed += 1
            break

    out = "\n".join(lines)
    print("literals fixed:", fixed)
    print("unresolved:", len(unresolved))
    seen = set()
    for ln, p, s, n in unresolved:
        key = (ln, p[-20:])
        if key in seen:
            continue
        seen.add(key)
        print("  line %5d cands=%d  pre=%r suf=%r" % (ln, n, p, s))
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
