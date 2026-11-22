# -*- coding: utf-8 -*-
"""导出官方「技能 -> 效果 -> buff」完整链到 data/external/rkpp-official/。

链路（已探测验证）：
    skill.skill_result[].effect_id
      -> buff 实体（= buff_group，含 editor_name / buff_base_ids）
        -> buff_base_ids[] -> buffbase 实体（editor_name 是语义化中文，
           如「物攻等级提升10」「击杀获得6能量」）

1037 个技能有完整链。产物 skill_effects.json 给两个模块用：
  - opponent_model: 把对面用的技能分类成 a1攻击 / a2防御 / a3状态
  - policy_v2:      评估"这招会给自己加什么 buff"（现在只算伤害）
"""
import io
import json
import os
import sqlite3
import sys
import time

import msgpack

SRC = r"C:/Users/Lenovo/Downloads/洛克PVP助手/洛克PVP助手/app/rkpp/rkpp_data.sqlite"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "external", "rkpp-official")


def dec(p):
    return msgpack.unpackb(p, raw=False) if p else None


def main():
    os.makedirs(OUT, exist_ok=True)
    src = sqlite3.connect(SRC)
    t0 = time.time()

    # 预载三张映射表（避免 1655×N 次查询）
    buff_group = {}
    for eid, payload in src.execute(
            "SELECT id,payload_msgpack FROM entity WHERE kind='buff'"):
        d = dec(payload)
        if d:
            buff_group[eid] = d
    buff_base = {}
    for eid, payload in src.execute(
            "SELECT id,payload_msgpack FROM entity WHERE kind='buffbase'"):
        d = dec(payload)
        if d:
            buff_base[eid] = d
    print("buff_group", len(buff_group), "buff_base", len(buff_base))

    out = {}
    n_chain = 0
    for sid, payload in src.execute(
            "SELECT id,payload_msgpack FROM entity WHERE kind='skill'"):
        d = dec(payload)
        if not d:
            continue
        effects = []
        for e in (d.get("skill_result") or []):
            eid = e.get("effect_id")
            g = buff_group.get(eid)
            entry = {
                "effect_id": eid,
                "cast_moment": e.get("cast_moment"),
                "rate": e.get("success_rate"),
                "group_name": g.get("editor_name") if g else None,
            }
            names = []
            for bid in ((g or {}).get("buff_base_ids") or []):
                bb = buff_base.get(bid)
                if bb:
                    names.append(bb.get("editor_name"))
            if names:
                entry["buffs"] = names
            effects.append(entry)
        if effects:
            out[sid] = {
                "name": d.get("name"),
                "effects": effects,
            }
        if any(e.get("buffs") for e in effects):
            n_chain += 1

    with io.open(os.path.join(OUT, "skill_effects.json"), "w",
                 encoding="utf-8") as fh:
        json.dump({"count": len(out), "with_buffs": n_chain,
                   "skills": out}, fh, ensure_ascii=False)
    print("skill_effects:", len(out), "含 buff 链:", n_chain)

    # 顺带导 buffbase 全表（2164 条语义名）
    bb = {}
    for eid, payload in src.execute(
            "SELECT id,payload_msgpack FROM entity WHERE kind='buffbase'"):
        d = dec(payload)
        if d:
            bb[eid] = {"name": d.get("editor_name"),
                       "trigger": d.get("client_trigger_type")}
    with io.open(os.path.join(OUT, "buffbase_names.json"), "w",
                 encoding="utf-8") as fh:
        json.dump({"count": len(bb), "items": bb}, fh, ensure_ascii=False)
    print("buffbase:", len(bb))
    print("elapsed %.1fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
