# -*- coding: utf-8 -*-
"""Strict equivalence check, ignoring code-object addresses.

The pyc was compiled at 21:42:05.  Edits made after that (acted_key rename,
the 8 s gate early-exit, the energy field in the turn log) are expected to
differ.  Everything else must match byte for byte.

Reports only genuine differences: co_code, co_names, co_varnames, and
non-string non-code constants.
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


def flatten(code):
    out = {}

    def walk(co, qn=""):
        name = (qn + "/" if qn else "") + co.co_name
        key, n = name, 1
        while key in out:
            n += 1
            key = "%s#%d" % (name, n)
        out[key] = co
        for c in co.co_consts:
            if isinstance(c, types.CodeType):
                walk(c, name)

    walk(code)
    return out


def plain_consts(co):
    """Constants that actually affect behaviour (drop str and code objects)."""
    out = []
    for c in co.co_consts:
        if isinstance(c, (str, types.CodeType)):
            continue
        out.append(c)
    return tuple(out)


def main():
    old = flatten(marshal.loads(open(PYC, "rb").read()[16:]))
    new = flatten(compile(io.open(SRC, encoding="utf-8").read(),
                          "robot.py", "exec"))

    print("code objects: pyc=%d repaired=%d" % (len(old), len(new)))

    diffs = []
    for k in sorted(set(old) & set(new)):
        o, n = old[k], new[k]
        if o.co_code != n.co_code:
            diffs.append((k, "co_code", "%d -> %d bytes"
                          % (len(o.co_code), len(n.co_code))))
        elif o.co_names != n.co_names:
            diffs.append((k, "co_names", "+%s -%s"
                          % ([x for x in n.co_names if x not in o.co_names],
                             [x for x in o.co_names if x not in n.co_names])))
        elif o.co_varnames != n.co_varnames:
            diffs.append((k, "co_varnames", "+%s -%s"
                          % ([x for x in n.co_varnames if x not in o.co_varnames],
                             [x for x in o.co_varnames if x not in n.co_varnames])))
        elif plain_consts(o) != plain_consts(n):
            diffs.append((k, "consts", "%r -> %r"
                          % (plain_consts(o)[:3], plain_consts(n)[:3])))

    print("behaviour differences: %d" % len(diffs))
    for k, what, detail in diffs:
        print("  %-46s %-11s %s" % (k, what, detail[:80]))

    only_old = sorted(set(old) - set(new))
    only_new = sorted(set(b for b in new if b not in old))
    if only_old:
        print("\nonly in pyc: %s" % only_old)
    if only_new:
        print("\nonly in repaired: %s" % only_new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
