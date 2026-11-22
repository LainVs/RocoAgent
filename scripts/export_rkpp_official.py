# -*- coding: utf-8 -*-
"""从 洛克PVP助手 的 rkpp_data.sqlite 导出官方数据到 data/external/rkpp-official/。

只读源库；产物三件：
  skills_official.json     全部技能（含官方 skill_priority / damage_type / effects）
  skillbanks.json          精灵 -> 等级 -> 可学技能池（16828 条）
  buff_map_official.json   buff/effect 名称映射（给 opponent_model 细分 a2/a3 用）
另写 VERSION.md 记录来源与许可。

源：C:/Users/Lenovo/Downloads/洛克PVP助手/洛克PVP助手/app/rkpp/rkpp_data.sqlite
    （data 来自 roco-pak-unpack，即游戏客户端 PAK 的官方数据；
     项目本体 MIT，游戏数据版权归 洛克王国世界 IP 方，
     见其 THIRD-PARTY-NOTICES.txt）
"""
import io
import json
import os
import sqlite3
import sys
import time

SRC = r"C:/Users/Lenovo/Downloads/洛克PVP助手/洛克PVP助手/app/rkpp/rkpp_data.sqlite"
# 项目实际位置（脚本可能从任意 cwd 运行，用 __file__ 锚定最稳）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "external", "rkpp-official")

try:
    import msgpack
except ImportError:
    print("need msgpack: pip install msgpack")
    raise SystemExit(1)


def rows(db, sql, args=()):
    return db.execute(sql, args).fetchall()


def main():
    os.makedirs(OUT, exist_ok=True)
    db = sqlite3.connect(SRC)
    t0 = time.time()

    # ---- 1. skills：官方技能表（带 skill_priority / damage_type）
    skills = {}
    for kind_id, name, payload in rows(db,
            "SELECT id,name,payload_msgpack FROM entity WHERE kind='skill'"):
        try:
            d = msgpack.unpackb(payload, raw=False)
        except Exception:
            continue
        skills[int(kind_id)] = {
            "name": name or d.get("name"),
            "power": d.get("dam_para", [None])[0] if d.get("dam_para") else 0,
            "priority": d.get("skill_priority"),
            "damage_type": d.get("damage_type"),        # 1=物理 2=魔法（待验证）
            "energy_cost": (d.get("energy_cost") or [None])[0],
            "hit_para": d.get("hit_para"),
            "effects": [
                {"effect_id": e.get("effect_id"),
                 "rate": e.get("success_rate")}
                for e in (d.get("skill_result") or [])[:4]
            ],
        }
    with io.open(os.path.join(OUT, "skills_official.json"), "w",
                 encoding="utf-8") as fh:
        json.dump({"count": len(skills), "skills": skills}, fh,
                  ensure_ascii=False)
    print("skills:", len(skills))

    # ---- 2. skillbanks：精灵 -> 技能池
    banks = {}
    for eid, payload in rows(db,
            "SELECT id,payload_msgpack FROM entity WHERE kind='monster_skillbank'"):
        try:
            d = msgpack.unpackb(payload, raw=False)
        except Exception:
            continue
        sids = [s.get("skill_id") for s in (d.get("skills") or [])]
        banks[int(eid)] = sids
    with io.open(os.path.join(OUT, "skillbanks.json"), "w",
                 encoding="utf-8") as fh:
        json.dump({"count": len(banks), "banks": banks}, fh,
                  ensure_ascii=False)
    print("skillbanks:", len(banks))

    # ---- 3. buff 名称映射
    buffs = {}
    for kind_id, name, payload in rows(db,
            "SELECT id,name,payload_msgpack FROM entity WHERE kind='buff'"):
        try:
            d = msgpack.unpackb(payload, raw=False)
        except Exception:
            continue
        buffs[int(kind_id)] = {
            "name": name or d.get("editor_name") or d.get("name"),
            "desc": (d.get("desc") or "")[:120],
        }
    with io.open(os.path.join(OUT, "buff_map_official.json"), "w",
                 encoding="utf-8") as fh:
        json.dump({"count": len(buffs), "buffs": buffs}, fh,
                  ensure_ascii=False)
    print("buffs:", len(buffs))

    # ---- 4. VERSION.md
    ver = """# rkpp-official 官方数据导出

来源：洛克PVP助手（GitHub HZ-KMNO/roco-sandbox 衍生）的 rkpp_data.sqlite，
其数据由游戏客户端 PAK 解包（`roco-pak-unpack`），版权属《洛克王国：世界》IP 方。
项目本体 MIT 许可；本目录仅引用**数据**，不复制其代码。

生成时间：{ts}
源库：{src}

| 文件 | 内容 | 数量 |
|---|---|---|
| skills_official.json | 官方技能表（skill_priority 真值 / 伤害类型 / 效果） | {n1} |
| skillbanks.json | 精灵 -> 等级 -> 可学技能池 | {n2} |
| buff_map_official.json | buff / effect 名称映射 | {n3} |

更新方式：重跑 `scripts/export_rkpp_official.py`。
""".format(ts=time.strftime("%Y-%m-%d %H:%M:%S"), src=SRC,
           n1=len(skills), n2=len(banks), n3=len(buffs))
    with io.open(os.path.join(OUT, "VERSION.md"), "w", encoding="utf-8") as fh:
        fh.write(ver)
    print("VERSION.md written")
    print("elapsed %.1fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
