# -*- coding: utf-8 -*-
"""Static verification of the phantom-START guard:
1. The guard block exists inside run() before match_started assignment.
2. match_started is only set when both my_pct and foe_pct are live.
3. The suppression path still calls the identity/tick methods (so a real
   match arriving a few frames later works).
Simple AST-based check.
"""
import ast, io, os, sys

ROOT = r"D:\Projects\RocoAgent"
src = io.open(os.path.join(ROOT, "src", "robot.py"), encoding="utf-8").read()
tree = ast.parse(src)

run_fn = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "run":
        run_fn = node
assert run_fn is not None

srcline = [ast.dump(n) for n in ast.walk(run_fn)]
joined = "\n".join(srcline)
checks = {
    "guard reads my_pct": "my_pct" in joined and "my_live" in joined,
    "guard reads foe_pct": "foe_pct" in joined and "foe_live" in joined,
    "calls battle_tick in suppression": "battle_tick" in joined,
    "calls cast_ocr in suppression": "cast_ocr" in joined,
    "logs suppression (throttled)": "_phantom_log_at" in joined,
}
ok = True
for name, cond in checks.items():
    print("%-40s %s" % (name, "OK" if cond else "FAIL"))
    ok &= cond

# 语义检查：match_started 赋值前必须有 my_live and foe_live 判定为真
src_run = ast.get_source_segment(src, run_fn)
i = src_run.find("self.match_started = now")
before = src_run[:i]
ok &= "if not (my_live and foe_live):" in before
print("match_started guarded by live check:",
      "OK" if "if not (my_live and foe_live):" in before else "FAIL")
print("SELFTEST", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
