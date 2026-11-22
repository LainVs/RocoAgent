# -*- coding: utf-8 -*-
"""从 roco-cal（S5 权威源）生成「每只精灵的完整技能表」-> data/s5_skills.json

为什么需要这个（2026-11-22，用户指出两件事）
-------------------------------------------
1. "我们的精灵可选的技能组似乎不太全"
   编辑器的技能勾选框原来只列 `Dex.moves()` = 「species_skills ∩ power>0
   ORDER BY power DESC LIMIT 8」—— 冰钻布鲁斯库里其实有 45 个技能，
   界面只给 8 个，还全是伤害技能，力量增效 / 防御 / 有效预防这些
   辅助技能根本点不到。

2. "冰钻布鲁斯在 S5 赛季已经没有了跺地技能 取而代之的是扬沙"
   我们的 `species_skills` 表是旧赛季的：冰钻布鲁斯有「跺地」没有「扬沙」，
   而 roco-cal 的 S5 数据正好相反。已核对：
       data/external/roco-cal/data/pets_w_skill_json/254冰钻布鲁斯.json
       含 扬沙=True 含 跺地=False

数据形态（665 个文件，659 只唯一精灵，29186 条技能）
----------------------------------------------------
    {"name": "冰钻布鲁斯", "id": "254", "elements": ["冰"],
     "skills": [{"skill_name": "冰爪", "unlock_condition": "（默认）Lv.1"},
                ...,
                {"skill_name": "冰雹", "unlock_condition": "（血脉）冰系血脉"}]}

解锁条件有两类：`（默认）Lv.N` 和 `（血脉）X系血脉`。同一只精灵的
血脉技能会按血脉类型重复很多遍，**必须按技能名去重**，默认档优先。

输出：
    data/s5_skills.json
    {"source": ..., "built_at": ..., "pets": {"冰钻布鲁斯": {"id","skills":[{"name","unlock"}...]}}}

用法：
    & $py scripts/build_s5_skills.py
"""
import glob
import io
import json
import os
import sys
import time

BASE = os.path.join("data", "external", "roco-cal", "data",
                    "pets_w_skill_json")
OUT = os.path.join("data", "s5_skills.json")

sys.path.insert(0, "src")


def main():
    files = sorted(glob.glob(os.path.join(BASE, "*.json")))
    if not files:
        print("ABORT: 找不到 %s" % BASE)
        return 1

    pets = {}
    dup_names = 0
    for p in files:
        d = json.load(io.open(p, encoding="utf-8"))
        name = (d.get("name") or "").strip()
        if not name:
            continue
        if name in pets:
            dup_names += 1
        seen = {}
        # 默认档优先，其次血脉档
        for s in d.get("skills") or []:
            sn = (s.get("skill_name") or "").strip()
            if not sn:
                continue
            cond = (s.get("unlock_condition") or "").strip()
            rank = 0 if cond.startswith("（默认）") else 1
            old = seen.get(sn)
            if old is None or rank < old[0]:
                seen[sn] = (rank, cond)
        if name in pets and len(pets[name]["skills"]) >= len(seen):
            continue
        pets[name] = {
            "id": str(d.get("id") or ""),
            "elements": d.get("elements") or [],
            "trait": d.get("特性") or "",
            "skills": [{"name": k, "unlock": v[1]}
                       for k, v in sorted(seen.items(),
                                          key=lambda kv: (kv[1][0], kv[0]))],
        }

    out = {
        "source": "data/external/roco-cal/data/pets_w_skill_json (S5)",
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pets": pets,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))

    n_sk = sum(len(v["skills"]) for v in pets.values())
    print("写出 %s：%d 只精灵 / %d 条技能（重名文件 %d）"
          % (OUT, len(pets), n_sk, dup_names))

    # 覆盖率：S5 技能名能否在 skills 表里查到 power/cost/type
    import sqlite3
    try:
        import paths
        db = sqlite3.connect(paths.DB)
        known = {r[0] for r in db.execute("SELECT name FROM skills")}
    except Exception as e:
        print("跳过覆盖率检查：%s" % e)
        return 0
    miss = {}
    total = 0
    for v in pets.values():
        for s in v["skills"]:
            total += 1
            if s["name"] not in known:
                miss[s["name"]] = miss.get(s["name"], 0) + 1
    hit = total - sum(miss.values())
    print("技能名在 skills 表里的覆盖率: %d/%d = %.1f%%"
          % (hit, total, 100.0 * hit / max(1, total)))
    if miss:
        top = sorted(miss.items(), key=lambda kv: -kv[1])[:10]
        print("  缺失 TOP:", top)

    # 用户点名的两个技能
    for nm in ("冰钻布鲁斯",):
        v = pets.get(nm)
        if v:
            names = [s["name"] for s in v["skills"]]
            print("  %s: %d 个技能，含扬沙=%s 含跺地=%s"
                  % (nm, len(names), "扬沙" in names, "跺地" in names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
