# -*- coding: utf-8 -*-
"""验证 data/external 下三个新拉的项目是否可用。"""
import io
import json
import os
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(ROOT, "data", "external")


def main():
    print("=== 1. nrc-ai / nrc.db ===")
    p = os.path.join(EXT, "nrc-ai", "data", "nrc.db")
    if not os.path.exists(p):
        print("  !! 缺失")
    else:
        db = sqlite3.connect(p)
        for t in ("pokemon", "skill", "pokemon_skill"):
            try:
                n = db.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                print("  %-16s %d 行" % (t, n))
            except Exception as e:
                print("  %-16s 失败: %s" % (t, e))
        try:
            rows = db.execute("SELECT name FROM pokemon LIMIT 3").fetchall()
            print("  样例:", [r[0] for r in rows])
        except Exception:
            pass
        db.close()

    print()
    print("=== 2. rocom-helper / buff_map ===")
    p = os.path.join(EXT, "rocom-helper", "data", "game", "buff_map.json")
    if not os.path.exists(p):
        print("  !! 缺失")
    else:
        d = json.load(io.open(p, encoding="utf-8"))
        print("  条数: %d (%s)" % (len(d), type(d).__name__))
        keys = list(d)[:3] if isinstance(d, dict) else d[:3]
        print("  样例:", json.dumps(keys, ensure_ascii=False)[:160])

    print()
    print("=== 3. rocom-data / lineups ===")
    p = os.path.join(EXT, "rocom-data", "data", "lineups.json")
    if not os.path.exists(p):
        print("  !! 缺失")
    else:
        d = json.load(io.open(p, encoding="utf-8"))
        print("  阵容数: %d" % len(d))
        first = d[0]
        print("  首条: %s | 成员 %d 只" % (
            first.get("title"), len(first.get("members") or [])))

    print()
    print("=== 4. 战术 YAML ===")
    base = os.path.join(EXT, "rocom-data", "data", "strategies")
    for fn in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        if not fn.endswith(".yaml"):
            continue
        s = io.open(os.path.join(base, fn), encoding="utf-8",
                    errors="ignore").read()
        print("  %-22s %d 字符 | 含专注力=%s 含音速犬=%s" % (
            fn, len(s), "专注力" in s, "音速犬" in s))

    print()
    print("=== 5. nrc-ai 效果配置 ===")
    p = os.path.join(EXT, "nrc-ai", "src", "effect_data.py")
    if os.path.exists(p):
        s = io.open(p, encoding="utf-8", errors="ignore").read()
        print("  effect_data.py   %d 行 | 含蝙蝠=%s 含吞噬=%s" % (
            len(s.splitlines()), '"蝙蝠"' in s, '"吞噬"' in s))
    p = os.path.join(EXT, "nrc-ai", "src", "effect_models.py")
    if os.path.exists(p):
        s = io.open(p, encoding="utf-8", errors="ignore").read()
        print("  effect_models.py %d 行 | E 枚举项 %d 个" % (
            len(s.splitlines()), s.count("auto()")))
    p = os.path.join(EXT, "nrc-ai", "src", "battle.py")
    if os.path.exists(p):
        s = io.open(p, encoding="utf-8", errors="ignore").read()
        print("  battle.py        %d 行" % len(s.splitlines()))

    print()
    print("=== 6. 各项目 VERSION.md ===")
    for d in ("roco-cal", "nrc-ai", "rocom-helper", "rocom-data"):
        p = os.path.join(EXT, d, "VERSION.md")
        print("  %-14s %s" % (d, "有" if os.path.exists(p) else "缺"))


if __name__ == "__main__":
    main()
