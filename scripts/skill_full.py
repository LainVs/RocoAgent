# -*- coding: utf-8 -*-
"""找到技能属性（element）到底存在 skill_map 的哪个字段。"""
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), *([".."] * 0), "src"))
import paths
import json
import os

BASE = str(paths.GAME_DATA)
OUT = os.path.join(os.environ["TEMP"], "oc_skill_full.txt")


def load(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    out = []
    p = lambda *a: out.append(" ".join(str(x) for x in a))
    sk = load("skill_map.json")
    for sid in ("7180110", "7040390", "7020590", "7021110"):
        s = sk.get(sid)
        p("==", sid, s.get("name"))
        for k, v in s.items():
            if k in ("desc", "res_id", "icon"):
                continue
            p("   %-18s %s" % (k, json.dumps(v, ensure_ascii=False)[:200]))
        p()

    # 统计：dam_para 与 energy_cost 的取值分布，判断哪个字段像元素
    from collections import Counter
    c_type = Counter()
    c_damt = Counter()
    for s in sk.values():
        c_type[s.get("type")] += 1
        c_damt[s.get("skill_dam_type")] += 1
    p("type field:", dict(c_type))
    p("skill_dam_type field:", dict(sorted(c_damt.items(), key=lambda x: (x[0] is None, x[0]))))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


if __name__ == "__main__":
    main()
    print("ok")
