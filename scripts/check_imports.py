# -*- coding: utf-8 -*-
"""check_imports.py —— 找出新模块里用到但没导入的名字。

用 ast 收集所有 Name 节点，减去已导入/已定义/内建的名字。
不做完整类型推断，只求"漏了哪个 import"这类问题能自动暴露。
"""
from __future__ import annotations

import ast
import builtins
import io
import os
import sys

ROOT = r"D:\Projects\RocoAgent"
MODULES = ["winio.py", "perceive.py", "ui_probe.py", "policy_legacy.py",
           "robot.py"]

# 这些名字来自 robot.py 的模块级定义，拆分后需要跨模块 import
KNOWN_GLOBALS = set()

BUILTINS = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__package__", "__builtins__",
    "annotations", "self", "cls",
}


def collect_bound(tree):
    """模块里所有被绑定过的名字：import / def / class / 赋值 / 参数。"""
    bound = set()

    class V(ast.NodeVisitor):
        def visit_Import(self, node):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])

        def visit_ImportFrom(self, node):
            for a in node.names:
                if a.name != "*":
                    bound.add(a.asname or a.name)

        def visit_FunctionDef(self, node):
            bound.add(node.name)
            for a in node.args.args + node.args.kwonlyargs:
                bound.add(a.arg)
            if node.args.vararg:
                bound.add(node.args.vararg.arg)
            if node.args.kwarg:
                bound.add(node.args.kwarg.arg)
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            bound.add(node.name)
            self.generic_visit(node)

        def visit_Name(self, node):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            self.generic_visit(node)

        def visit_ExceptHandler(self, node):
            if node.name:
                bound.add(node.name)
            self.generic_visit(node)

        def visit_Lambda(self, node):
            for a in node.args.args:
                bound.add(a.arg)
            self.generic_visit(node)

        def visit_comprehension(self, node):
            self.generic_visit(node)

    V().visit(tree)
    return bound


def collect_used(tree):
    used = set()

    class V(ast.NodeVisitor):
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                used.add(node.id)
            self.generic_visit(node)

    V().visit(tree)
    return used


def main():
    # 先把 robot.py 里的顶层定义收进来（新模块可能要 import 它们）
    all_reports = {}
    for mod in MODULES:
        path = os.path.join(ROOT, "src", mod)
        if not os.path.exists(path):
            continue
        src = io.open(path, encoding="utf-8").read()
        tree = ast.parse(src)
        bound = collect_bound(tree)
        used = collect_used(tree)
        missing = sorted(used - bound - BUILTINS - KNOWN_GLOBALS)
        all_reports[mod] = missing

    for mod, missing in all_reports.items():
        if missing:
            print("\n%s 缺少 %d 个名字:" % (mod, len(missing)))
            for n in missing:
                print("   ", n)
        else:
            print("\n%s OK" % mod)
    return 0


if __name__ == "__main__":
    sys.exit(main())
