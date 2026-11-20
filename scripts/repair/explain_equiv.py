# -*- coding: utf-8 -*-
"""Explain each behaviour difference between the pyc and the repaired source.

The pyc was compiled at 21:42:05; the last few edits (energy double-check,
acted_card_key rename, advance budget 12, energy field in turns.jsonl) came
after that.  So the expected result is: every diff maps to one of those edits,
and everything else matches exactly.
"""
from __future__ import annotations

import dis
import io
import marshal
import os
import types

ROOT = r"D:\Projects\RocoAgent"
SRC = os.path.join(ROOT, "src", "robot.py")
PYC = os.path.join(ROOT, "src", "__pycache__", "robot.cpython-314.pyc")


def flatten(code):
    out = {}

    def walk(co, qn=""):
        name = (qn + "/" if qn else "") + co.co_name
        key = name
        n = 1
        while key in out:
            n += 1
            key = "%s#%d" % (name, n)
        out[key] = co
        for c in co.co_consts:
            if isinstance(c, types.CodeType):
                walk(c, name)

    walk(code)
    return out


def nonstring(co):
    return tuple(c for c in co.co_consts if not isinstance(c, str))


def names_of(co):
    return list(co.co_names)


def main():
    old = flatten(marshal.loads(open(PYC, "rb").read()[16:]))
    new = flatten(compile(io.open(SRC, encoding="utf-8").read(), "robot.py", "exec"))

    for k in sorted(set(old) & set(new)):
        o, n = old[k], new[k]
        notes = []
        if o.co_names != n.co_names:
            extra = [x for x in n.co_names if x not in o.co_names]
            gone = [x for x in o.co_names if x not in n.co_names]
            notes.append("names +%s -%s" % (extra, gone))
        if o.co_varnames != n.co_varnames:
            notes.append("varnames %s -> %s" % (o.co_varnames, n.co_varnames))
        if nonstring(o) != nonstring(n):
            a = [repr(c)[:40] for c in nonstring(o)]
            b = [repr(c)[:40] for c in nonstring(n)]
            extra = [x for x in b if x not in a]
            gone = [x for x in a if x not in b]
            notes.append("consts +%s -%s" % (extra, gone))
        if o.co_code != n.co_code:
            notes.append("code %d -> %d bytes" % (len(o.co_code), len(n.co_code)))
        if notes:
            print("\n%s" % k)
            for x in notes:
                print("    ", x)


if __name__ == "__main__":
    main()
