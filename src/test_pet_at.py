# -*- coding: utf-8 -*-
"""test_pet_at.py —— `pet_at` 的语义回归。

## 为什么要单独锁这条

`pet_at` 表示"场上这只精灵**上场**的时刻"，下游有两处 1.2 秒稳定窗口
依赖它（换人动画期间 UI 还没画完）：

    robot.py:2412  BAND 卡面名字带扫描
    robot.py:3465  首领化（恶魔狼变身）

2026-11-23 实测：`_check_active_pet()` 每 0.5 秒跑一次，而 ONNX 主通道和
avatar 次通道**无条件** `self.pet_at = now`。于是 `now - pet_at` 最大只有
0.5s，两处 1.2s 门槛**永远不成立**：

- BAND 整场 0 条日志（连 scan failed 都没有）
- 首领化从未触发过（变身不占回合，白白错过）

这个错误编译器查不出来，日志也只会表现为"功能不生效"，所以用 AST 锁住：
`pet_at = now` 必须处在"身份发生变化"的条件分支内。

## 检查项

1. 所有 `self.pet_at = now` 赋值点，都必须位于 same-or-parent 的
   `if ... != self.active_pet` / `if ... != <局部变量>` 条件内
2. `_check_active_pet` 里必须有身份比较后再赋值
3. 两处依赖 1.2s 窗口的调用点仍然存在（防止有人删了下游却留着注释）
"""
import ast
import io
import os
import sys

ROOT = r"D:\Projects\RocoAgent"
SRC = io.open(os.path.join(ROOT, "src", "robot.py"), encoding="utf-8").read()
TREE = ast.parse(SRC)

FAIL = []


def check(name, cond, detail=""):
    print("  %s %s %s" % ("ok  " if cond else "FAIL", name, detail))
    if not cond:
        FAIL.append(name)


def _is_pet_at_assign(node):
    """self.pet_at = <expr>，且 expr 是 now（初始化赋 0.0 不算）。"""
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return False
    t = node.targets[0]
    if not (isinstance(t, ast.Attribute) and t.attr == "pet_at"
            and isinstance(t.value, ast.Name) and t.value.id == "self"):
        return False
    v = node.value
    return isinstance(v, ast.Name) and v.id == "now"


def _guarded_by_identity_change(stmt, parents):
    """这条语句是否被"身份变化"的 if 包着。

    接受两种写法（实测代码里都出现过）：
        if _nm != self.active_pet:      # ONNX 主通道
        if a0["name"] != was:           # avatar 次通道
        if name != self.active_pet:     # 旧模板通道
    判据：所在 if 的 test 是 `Compare`，且一侧含 `active_pet` 属性或以
    `_pet`/`was` 结尾的局部变量。
    """
    for p in parents:
        if not isinstance(p, ast.If):
            continue
        t = p.test
        if not isinstance(t, ast.Compare):
            continue
        seg = ast.dump(t)
        if ("active_pet" in seg or "was" in seg
                or "_pet" in seg):
            return True
    return False


class Finder(ast.NodeVisitor):
    def __init__(self):
        self.stack = []
        self.assigns = []      # (lineno, guarded)

    def visit(self, node):
        is_stmt = isinstance(node, ast.stmt)
        if is_stmt:
            self.stack.append(node)
        super().visit(node)
        if is_stmt:
            self.stack.pop()

    def visit_Assign(self, node):
        if _is_pet_at_assign(node):
            parents = self.stack[:-1]        # 不含自己
            self.assigns.append(
                (node.lineno, _guarded_by_identity_change(node, parents)))
        self.generic_visit(node)


f = Finder()
f.visit(TREE)

print("== pet_at 赋值点 ==")
check("找到 pet_at 赋值点", len(f.assigns) >= 3, str(len(f.assigns)))
unguarded = [(ln, g) for ln, g in f.assigns if not g]
check("全部赋值都受'身份变化'保护", not unguarded,
      "无保护: %s" % [ln for ln, _ in unguarded])
for ln, g in f.assigns:
    print("     line %-5d guarded=%s" % (ln, g))

# ---- _check_active_pet 内部必须有身份比较 -------------------------
print("\n== _check_active_pet ==")
fn = None
for node in ast.walk(TREE):
    if isinstance(node, ast.FunctionDef) and node.name == "_check_active_pet":
        fn = node
        break
check("函数存在", fn is not None)
if fn is not None:
    body = ast.dump(fn)
    check("函数内读 active_pet", "active_pet" in body)
    check("函数内重置 _band_tries", "_band_tries" in body,
          "换精灵时要一并重开 BAND 重试窗口")
    check("函数内重置 _band_scan_at", "_band_scan_at" in body)

# ---- 下游两处 1.2s 窗口仍然存在 -----------------------------------
print("\n== 下游依赖 pet_at 的 1.2s 窗口 ==")
all_src = SRC
check("BAND 扫描有 1.2s 稳定窗口", "pet_at" in all_src
      and all_src.count("< 1.2") >= 2, str(all_src.count("< 1.2")))

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")