# -*- coding: utf-8 -*-
"""fingerprint.py —— 记录/对比模块的字节码指纹，用于验证"纯搬移"没改行为。

指纹 = 每个顶层函数的 co_code + co_names + co_varnames + 非字符串常量。
注释、文档字符串、代码位置都不影响指纹。

用法：
    python scripts/fingerprint.py --save before.json   # 拆分前
    python scripts/fingerprint.py --diff before.json   # 拆分后对比
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import types

ROOT = r"D:\Projects\RocoAgent"


def const_sig(co):
    """非字符串、非 code 常量（字符串可能是文档字符串，code 有地址）。"""
    out = []
    for c in co.co_consts:
        if isinstance(c, str):
            out.append(("str", c))
        elif isinstance(c, types.CodeType):
            out.append(("code", c.co_name, len(c.co_code)))
        else:
            out.append(("val", repr(c)))
    return out


def walk(co, prefix=""):
    """{qualified name: signature}"""
    out = {}
    name = (prefix + "." if prefix else "") + co.co_name
    sig = {
        "code": hashlib.sha1(co.co_code).hexdigest()[:16],
        "names": list(co.co_names),
        "varnames": list(co.co_varnames),
        "consts": const_sig(co),
    }
    out[name] = sig
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            out.update(walk(c, name))
    return out


def fingerprint_source(src_text, label):
    co = compile(src_text, label, "exec")
    return walk(co)


def main():
    src = os.path.join(ROOT, "src", "robot.py")
    text = io.open(src, encoding="utf-8").read()

    if "--save" in sys.argv:
        path = sys.argv[sys.argv.index("--save") + 1]
        fp = fingerprint_source(text, "robot.py")
        io.open(path, "w", encoding="utf-8", newline="\n").write(
            json.dumps(fp, ensure_ascii=False, indent=1, sort_keys=True))
        print("saved %d code objects -> %s" % (len(fp), path))
        return 0

    if "--diff" in sys.argv:
        path = sys.argv[sys.argv.index("--diff") + 1]
        before = json.load(open(path, encoding="utf-8"))
        after = fingerprint_source(text, "robot.py")

        only_before = sorted(set(before) - set(after))
        only_after = sorted(set(after) - set(before))
        diffs = []
        for k in sorted(set(before) & set(after)):
            if before[k] != after[k]:
                diffs.append(k)

        print("拆分前 %d 个 code object，拆分后 %d 个"
              % (len(before), len(after)))
        if only_before:
            print("\n消失的 (%d):" % len(only_before))
            for k in only_before[:20]:
                print("   ", k)
        if only_after:
            print("\n新增的 (%d):" % len(only_after))
            for k in only_after[:20]:
                print("   ", k)
        print("\n内容有差异的: %d" % len(diffs))
        for k in diffs[:20]:
            b, a = before[k], after[k]
            for field in ("code", "names", "varnames", "consts"):
                if b[field] != a[field]:
                    print("   %-52s %s" % (k, field))
        ok = not only_before and not only_after and not diffs
        print("\n%s" % ("IDENTICAL - 纯搬移，行为未变" if ok else "DIFFERS"))
        return 0 if ok else 1

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
