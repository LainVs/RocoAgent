# -*- coding: utf-8 -*-
"""Stage 3 (apply): rewrite the 12 damaged code literals with pyc ground truth.

Each edit is anchored on a distinctive ASCII/known substring of the damaged
line and replaces only the damaged literal.  The script prints a diff-like
before/after and refuses to touch a line whose anchor is missing.
"""
from __future__ import annotations

import io
import os
import sys

ROOT = r"D:\Projects\RocoAgent"
SRC = os.path.join(ROOT, "src", "robot.py")
BAD = "\ufffd"

# (line_no, must_contain, old_substring, new_substring)
EDITS = [
    (1835, "BLE FAILED", 'log("BLE FAILED - 180s 内没等到板子，退\ufffd?)',
     'log("BLE FAILED - 180s 内没等到板子，退出")'),
    (1837, "BLE waiting", 'log("BLE waiting for board \ufffd?)',
     'log("BLE waiting for board …")'),
    (2406, "FOE SCAN -> %s", 'log("FOE SCAN -> %s lv%s 特\ufffd?%s 属\ufffd?%s 技\ufffd?%s (%.1fs)"',
     'log("FOE SCAN -> %s lv%s 特性=%s 属性=%s 技能=%s (%.1fs)"'),
    (2445, 'in text:', 'if "使出\ufffd? in text:', 'if "使出了" in text:'),
    (2446, 'text.split', 'tail = text.split("使出\ufffd?, 1)[1]',
     'tail = text.split("使出了", 1)[1]'),
    (2447, 're.sub', 'tail = re.sub(r"[\ufffd?\\s]", "", tail)',
     'tail = re.sub(r"[！!\\s]", "", tail)'),
    (2457, 'in text:', 'if "召唤\ufffd? in text:', 'if "召唤了" in text:'),
    (2458, 'text.split', 'tail = text.split("召唤\ufffd?, 1)[1]',
     'tail = text.split("召唤了", 1)[1]'),
    (2459, 're.sub', 'tail = re.sub(r"[\ufffd?\\s]", "", tail)',
     'tail = re.sub(r"[！!\\s]", "", tail)'),
    (2596, "LEADER ->", 'log("LEADER -> 恶魔\ufffd?on field, transforming (Q/1/space)")',
     'log("LEADER -> 恶魔狼 on field, transforming (Q/1/space)")'),
    (2778, 'return bench', 'return bench, "会被秒（它最凶一\ufffd?>= 我当前血\ufffd?',
     'return bench, "会被秒（它最凶一击 >= 我当前血）"'),
    (2782, 'return bench', 'return bench, "打不动对面（%d 张被抵抗、无克制\ufffd? % n_red',
     'return bench, "打不动对面（%d 张被抵抗、无克制）" % n_red'),
    (2784, 'return bench', 'return bench, "本系被对面抵\ufffd?×%.1f)且伤害低(%.0f%%)" % (',
     'return bench, "本系被对面抵抗(×%.1f)且伤害低(%.0f%%)" % ('),
    (2829, 'bench', '("建议\ufffd? if (weak and lives >= 2 and bench) else "继续\ufffd?)))',
     '("建议换" if (weak and lives >= 2 and bench) else "继续打")))'),
    (2884, 'now_hi', '"会被\ufffd? if v["now_hi"] else ("下限即死" if v["now_lo"] else "扛得\ufffd?)))',
     '"会被秒" if v["now_hi"] else ("下限即死" if v["now_lo"] else "扛得住")))'),
    (2911, 'my_name', 'or getattr(self, "my_name", None) == "首领-恶魔\ufffd?):',
     'or getattr(self, "my_name", None) == "首领-恶魔狼"):'),
]


def main():
    apply = "--apply" in sys.argv
    text = io.open(SRC, encoding="utf-8", errors="replace").read()
    lines = text.split("\n")
    bad = 0
    for ln, must, old, new in EDITS:
        cur = lines[ln - 1]
        if must not in cur:
            print("!! %5d anchor %r missing; line is %r" % (ln, must, cur[:80]))
            bad += 1
            continue
        if old not in cur:
            print("!! %5d exact old not found; line is %r" % (ln, cur[:90]))
            bad += 1
            continue
        lines[ln - 1] = cur.replace(old, new)
        print("   %5d %s" % (ln, lines[ln - 1].strip()[:100]))

    print("\nanchored: %d / %d" % (len(EDITS) - bad, len(EDITS)))
    if bad:
        print("aborting: %d anchor(s) failed" % bad)
        return
    out = "\n".join(lines)
    print("remaining U+FFFD:", out.count(BAD))
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
