# -*- coding: utf-8 -*-
"""把 nrc-ai 手写的技能/特性效果表导成 JSON -> data/nrc_effects.json

为什么用它（AGENTS.md 里就写了）
--------------------------------
「做技能/特性效果系统前先看 `data/external/nrc-ai/src/effect_models.py`
和 `effect_data.py`，它已经把 197 种效果原语和 12 种印记设计好了，别从零写。」

实测覆盖（2026-11-22）：
    SKILL_EFFECTS   139 个技能（我们库里 1269 个技能名，命中 128 = 10.1%）
    ABILITY_EFFECTS 169 个特性（我们库里 343 个特性名，命中 168 = **49%**）

特性这边是质变：roco-cal 只有 261 条机器生成的结构化特性，其中能应用的
只有 18 条；nrc-ai 这 168 条是**人手写的语义**（`T(E.SELF_BUFF, spatk=0.7)`
这种），覆盖面大得多。

注意：这个脚本**单独进程**跑，因为它要 `sys.path.insert` 到 nrc-ai 让
`import src.effect_data` 生效 —— 那个 `src` 包名和我们自己的 `src/` 会撞，
所以绝不能在本项目运行时导入它。导成 JSON 之后，运行时只读 JSON。

用法：
    & $py scripts/build_nrc_effects.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NRC = os.path.join(ROOT, "data", "external", "nrc-ai")
OUT = os.path.join(ROOT, "data", "nrc_effects.json")


def ser_tag(t):
    return {"type": getattr(t.type, "name", str(t.type)),
            "params": dict(t.params or {}),
            "condition": dict(t.condition or {}),
            "sub": [ser_tag(x) for x in (t.sub_effects or [])]}


def main():
    if NRC not in sys.path:
        sys.path.insert(0, NRC)
    try:
        from src import effect_data as ED
    except Exception as e:
        print("跳过：导入 nrc-ai 失败（%s: %s）" % (type(e).__name__, e))
        return 0

    skills = {}
    for name, grp in ED.SKILL_EFFECTS.items():
        rows = []
        for g in grp:
            rows.append({
                "timing": getattr(g.timing, "name", str(g.timing)),
                "filters": dict(getattr(g, "filters", None) or
                                getattr(g, "filter", None) or {}),
                "effects": [ser_tag(t) for t in (g.effects or [])],
            })
        skills[name] = rows

    traits = {}
    for name, grp in ED.ABILITY_EFFECTS.items():
        rows = []
        for g in grp:
            rows.append({
                "timing": getattr(g.timing, "name", str(g.timing)),
                "filters": dict(getattr(g, "filter", None) or {}),
                "effects": [ser_tag(t) for t in (g.effects or [])],
            })
        traits[name] = rows

    payload = {
        "source": "data/external/nrc-ai/src/effect_data.py (MIT)",
        "note": "手写的技能/特性效果表。运行时只读这个 JSON，不导入 nrc-ai "
                "代码（它的包名 src 会和本项目冲突）。",
        "skill_count": len(skills),
        "trait_count": len(traits),
        "skills": skills,
        "traits": traits,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    # 覆盖率
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import sqlite3
    import paths
    db = sqlite3.connect(paths.DB)
    sk_names = {r[0] for r in db.execute(
        "SELECT DISTINCT name FROM skills WHERE name IS NOT NULL")}
    tr_names = {r[0] for r in db.execute(
        "SELECT DISTINCT name FROM features WHERE name IS NOT NULL")}
    hit_s = len(sk_names & set(skills))
    hit_t = len(tr_names & set(traits))
    print("写出 %s（%d KB）" % (OUT, os.path.getsize(OUT) // 1024))
    print("  技能 %d 条，命中我们库 %d/%d = %.1f%%"
          % (len(skills), hit_s, len(sk_names),
             100.0 * hit_s / max(1, len(sk_names))))
    print("  特性 %d 条，命中我们库 %d/%d = %.1f%%"
          % (len(traits), hit_t, len(tr_names),
             100.0 * hit_t / max(1, len(tr_names))))
    # 原语种类
    from collections import Counter
    kinds = Counter()
    for rows in list(skills.values()) + list(traits.values()):
        for r in rows:
            for e in r["effects"]:
                kinds[e["type"]] += 1
    print("  用到的效果原语 %d 种，最常见 15 种:" % len(kinds))
    for k, n in kinds.most_common(15):
        print("     %-28s %d" % (k, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
