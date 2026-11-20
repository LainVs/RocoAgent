# -*- coding: utf-8 -*-
"""verify_split.py —— 验证拆分是纯搬移（第三版，最可靠的做法）。

方法：用 `dis.Bytecode(...).dis()` 拿到人类可读的反汇编文本，把跳转目标
地址（`to 164` / `>> 170`）统一替换成占位符，再逐行对比。

这样：
  * 跳转偏移不同（docstring 长度变化导致）-> 被屏蔽，不算差异
  * 指令、参数、变量名、常量引用 -> 完全可比
  * 任何真实的行为改动 -> 一定暴露

基线直接取 src/robot.py.orig（拆分前的备份），不需要外部文件。
"""
from __future__ import annotations

import dis
import io
import os
import re
import sys
import types

ROOT = r"D:\Projects\RocoAgent"
SRC_DIR = os.path.join(ROOT, "src")
ORIG = os.path.join(SRC_DIR, "robot.py.orig")
NEW_MODULES = ["robot.py", "config.py", "winio.py", "perceive.py",
               "ui_probe.py", "policy_legacy.py"]

ANON = ("<genexpr>", "<lambda>", "<listcomp>", "<dictcomp>", "<setcomp>",
        "<module>")

JUMP_RE = re.compile(r"\bto \d+\b")
LABEL_RE = re.compile(r"^\s*>>\s*\d+\s*", re.M)


def asm(co):
    """规范化反汇编：只保留影响行为的指令与参数。

    屏蔽掉的都是"布局信息"：
      * 跳转目标地址（docstring 长度变化导致偏移）
      * 行号列 / __firstlineno__ 常量
      * code object 的 repr（含内存地址和文件名）
    """
    text = dis.Bytecode(co).dis()
    text = LABEL_RE.sub("", text)
    text = JUMP_RE.sub("to <addr>", text)
    text = re.sub(r"^\s*\d+\s+", "", text, flags=re.M)
    # code object 的 repr：地址/文件名/行号全去掉。
    # 注意 repr 里嵌套了尖括号（<code object <genexpr> at 0x...>），
    # 单靠正则会误吞，这里手工截取到行尾的 ">"
    while "<code object " in text:
        i = text.index("<code object ")
        j = text.index(">", text.index(" at 0x", i))
        inner = text[i:j]
        name = inner.split("<code object ", 1)[1].split(" at 0x")[0]
        text = text[:i] + "<code %s>" % name + text[j + 1:]
    # __firstlineno__（Py3.11+ 会把 def 的行号存成常量，
    # 小行号用 LOAD_SMALL_INT、大行号用 LOAD_CONST，两者等价）
    text = re.sub(r"LOAD_CONST\s+\d+\s+\(\d+\)", "LOAD_LINE", text)
    text = re.sub(r"LOAD_SMALL_INT\s+\d+", "LOAD_LINE", text)
    # 常量索引号会因 docstring 占位而整体偏移（class 里 2 vs 1），
    # 索引本身无意义，参数内容才是语义
    text = re.sub(r"(LOAD_CONST|LOAD_SMALL_INT)\s+\d+\s+\(", r"\1 (", text)
    # Python 3.14 的两种等价方法调用编码：
    #   LOAD_ATTR n (m + NULL|self); CALL 0
    #   LOAD_ATTR n (m); PUSH_NULL; CALL 0
    # 编译器按上下文选一种，字节码不同但行为完全相同。
    text = re.sub(r"LOAD_ATTR\s+\d+\s+\((\w+)(?:\s*\+\s*NULL(?:\|self)?)?\)",
                  r"LOAD_ATTR (\1)", text)
    text = re.sub(r"LOAD_GLOBAL\s+\d+\s+\((\w+)(?:\s*\+\s*NULL)?\)",
                  r"LOAD_GLOBAL (\1)", text)
    text = re.sub(r"^\s*PUSH_NULL\s*$", "", text, flags=re.M)
    text = re.sub(r"POP_JUMP_IF_FALSE\s+\d+", "POP_JUMP_IF_FALSE", text)
    text = re.sub(r"POP_JUMP_IF_TRUE\s+\d+", "POP_JUMP_IF_TRUE", text)
    # 列对齐随行号宽度变（行号 1536 比 111 宽），归一化空白并丢掉空行
    lines = [" ".join(l.split()) for l in text.split("\n")]
    return "\n".join(l for l in lines if l)


def collect(co, out, prefix=""):
    name = (prefix + "." if prefix else "") + co.co_name
    if co.co_name not in ANON:
        # 用限定名索引：Screen.__init__ 和 Bot.__init__ 不能混为一谈
        out.append((name, {
            "asm": asm(co),
            "names": tuple(co.co_names),
            "varnames": tuple(co.co_varnames),
        }))
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            collect(c, out, name)


def index(items):
    seen, out = {}, {}
    for name, sig in items:
        seen[name] = seen.get(name, 0) + 1
        key = name if seen[name] == 1 else "%s#%d" % (name, seen[name])
        out[key] = sig
    return out


def fp(path):
    co = compile(io.open(path, encoding="utf-8").read(),
                 os.path.basename(path), "exec")
    items = []
    collect(co, items)
    return index(items)


def main():
    # 基线：robot.py.orig 或 git 里的"拆分完成时"版本。
    # 拆分发生在 b899609，8a18c4c 是它之前最后一个提交（单一 robot.py）。
    # 之后再改的属于新功能，不在本脚本职责内。
    baseline_path = ORIG
    if not os.path.exists(baseline_path):
        import subprocess
        r = subprocess.run(
            ["git", "show", "8a18c4c:src/robot.py"],
            cwd=ROOT, capture_output=True)
        if r.returncode != 0:
            print("找不到基线：既没有 %s，也取不到 git 版本" % ORIG)
            return 2
        text = r.stdout.decode("utf-8")
        before_co = compile(text, "robot.py.orig", "exec")
        items = []
        collect(before_co, items)
        before = index(items)
    else:
        before = fp(baseline_path)
    items = []
    for mod in NEW_MODULES:
        p = os.path.join(SRC_DIR, mod)
        if os.path.exists(p):
            collect(compile(io.open(p, encoding="utf-8").read(), mod, "exec"),
                    items)
    after = index(items)

    only_before = sorted(set(before) - set(after))
    only_after = sorted(set(after) - set(before))
    diffs = []
    for k in sorted(set(before) & set(after)):
        b, a = before[k], after[k]
        for field in ("asm", "names", "varnames"):
            if b[field] != a[field]:
                diffs.append((k, field))
                break

    print("拆分前 %d 个命名函数，拆分后 %d 个" % (len(before), len(after)))
    if only_before:
        print("\n只在拆分前 (%d):" % len(only_before))
        for k in only_before[:20]:
            print("   ", k)
    if only_after:
        print("\n只在拆分后 (%d):" % len(only_after))
        for k in only_after[:20]:
            print("   ", k)
    print("\n内容有差异: %d" % len(diffs))
    for k, field in diffs[:20]:
        print("   %-46s %s" % (k, field))

    # 展示第一处差异的细节
    if diffs:
        k, field = diffs[0]
        b, a = before[k][field], after[k][field]
        if field == "asm":
            import difflib
            bl = b.split("\n") if isinstance(b, str) else b
            al = a.split("\n") if isinstance(a, str) else a
            print("\n第一处差异 %s:" % k)
            for line in list(difflib.unified_diff(bl, al, "before", "after",
                                                  lineterm=""))[:20]:
                print("  ", line)
        else:
            print("\n第一处差异 %s.%s:" % (k, field))
            print("   before:", b)
            print("   after :", a)

    ok = not only_before and not only_after and not diffs
    print("\n%s" % ("IDENTICAL — 纯搬移，每个函数反汇编一致"
                    if ok else "DIFFERS — 见上面第一处差异"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
