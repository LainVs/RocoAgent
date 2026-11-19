# -*- coding: utf-8 -*-
"""搞清楚 skill_map 的字段语义 + battle_config 里的公式常数。"""
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), *([".."] * 0), "src"))
import paths
import json
import os

BASE = str(paths.GAME_DATA)
OUT = os.path.join(os.environ["TEMP"], "oc_fields.txt")


def load(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    out = []
    p = lambda *a: out.append(" ".join(str(x) for x in a))
    cfg = load("battle_config.json")
    p("== battle_config ==")
    for k, v in cfg.items():
        p(" ", k, "=", json.dumps(v, ensure_ascii=False)[:220])

    chart = load("type_chart.json")
    p("\n== types ==")
    for t in chart["types"]:
        p(" ", t["id"], t["name"], t["name_en"], "immunity:", t.get("type_immunity"))

    sk = load("skill_map.json")
    ps = load("pet_skill_map.json")
    p("\n== 恶魔狼 3011 skills ==")
    ent = ps.get("3011") or {}
    ids = [x["skill_id"] for x in ent.get("level_skills", [])]
    for sid in ids:
        s = sk.get(str(sid))
        if not s:
            continue
        p("  %-8s %-10s type=%-3s dam_type=%-3s dam_para=%-8s energy=%-6s prio=%s hit=%s" % (
            sid, s.get("name"), s.get("type"), s.get("skill_dam_type"),
            s.get("dam_para"), s.get("energy_cost"), s.get("skill_priority"),
            s.get("hit_para")))

    p("\n== 对照：几个常见技能 ==")
    for nm in ("水枪", "火焰切割", "撕咬", "电击", "撞击", "藤鞭"):
        hits = [(k, v) for k, v in sk.items() if v.get("name") == nm]
        for sid, s in hits[:2]:
            p("  %-8s %-8s type=%-3s dam_type=%-3s dam_para=%-8s energy=%-6s" % (
                sid, nm, s.get("type"), s.get("skill_dam_type"),
                s.get("dam_para"), s.get("energy_cost")))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


if __name__ == "__main__":
    main()
    print("ok")
