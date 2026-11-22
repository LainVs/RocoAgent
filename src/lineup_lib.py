# -*- coding: utf-8 -*-
"""真实玩家阵容库：把外部项目的阵容数据解析成模拟器配置。

数据来源（都是 `data/external/` 里的**固定资产**，入库、换机器可用）
--------------------------------------------------------------------
* `rocom-data/data/lineups.json`  —— 169 套 PvP 阵容，1014 只精灵
    每只：pokemon / bloodline / nature / talents(三项) / skills(四个名字)
* `rocoarena/teams.json`          —— 15 套天梯快照，75 只精灵
    每只：name / nature / ivStats(三项 name) / skills[{id,name}]

★ 三方交叉验证「三项 60 + 性格」这个建模
--------------------------------------------
    my_team 实测面板反推（5 只）        -> 三项 60 + 性格，6/6 面板命中
    rocom-data 1014 只                  -> talents 恰好 3 项，**0 例外**
    rocoarena    75 只                  -> ivStats 恰好 3 项，**0 例外**

所以模拟器的 `invest` 不是"随便勾几项"，而是固定三项 —— 和设计一致。

解析结果（喂 `simulator.run_match` 的 cfg）
-------------------------------------------
    {"title","author","blood_magic","source",
     "team": [精灵名...],
     "cfg":  [{"name","nature","invest":[...],"moves":[名字...]}, ...]}

精灵名归一化：`音速犬（看守麦田的样子）` -> `音速犬`（去括号形态后缀），
实测 996 精确 + 12 去括号成功 + 6 失败（源数据里被截断的 `权杖-?`）。
技能名：交给 `Dex.resolve_skill()` 解析，覆盖率 99.7%。
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paths  # noqa: E402

# 三项天赋：中文字段 -> simulator.Pet 的 invest 键
TALENT = {
    "生命": "hp", "精力": "hp", "体力": "hp",
    "物攻": "atk", "攻击": "atk",
    "魔攻": "spa", "特攻": "spa",
    "物防": "def", "防御": "def",
    "魔防": "spd", "特防": "spd",
    "速度": "spe",
}

# rocoarena 用的是英文键
ARENA_STAT = {"hp": "hp", "attack": "atk", "magicAttack": "spa",
              "defense": "def", "magicDefense": "spd", "speed": "spe"}

PAREN_RE = re.compile(r"[（(].*?[)）]")


def _norm_species(name, known):
    """把阵容里的精灵名映射到图鉴名。返回 (名字, 方式) 或 (None, "fail")。"""
    nm = (name or "").strip()
    if not nm:
        return None, "empty"
    if nm in known:
        return nm, "exact"
    stripped = PAREN_RE.sub("", nm).strip()
    if stripped in known:
        return stripped, "strip-paren"
    if "-" in nm:
        head = nm.split("-")[0].strip()
        if head in known:
            return head, "strip-hyphen"
    return None, "fail"


def _species_set(dex):
    return {r[0] for r in dex.db.execute("SELECT name FROM species")}


def _from_rocom(dex, known):
    p = str(paths.ROOT / "data" / "external" / "rocom-data" / "data"
            / "lineups.json")
    if not os.path.exists(p):
        return [], {"missing_file": p}
    raw = json.load(io.open(p, encoding="utf-8"))
    good, skipped = [], 0
    stat = {"species": {}, "talent_unknown": 0, "teams": 0}
    for t in raw:
        team, cfg = [], []
        for m in t.get("members") or []:
            nm, how = _norm_species(m.get("pokemon"), known)
            stat["species"][how] = stat["species"].get(how, 0) + 1
            if nm is None:
                skipped += 1
                continue
            invest = []
            for x in m.get("talents") or []:
                k = TALENT.get(x)
                if k is None:
                    stat["talent_unknown"] += 1
                elif k not in invest:
                    invest.append(k)
            if not invest:
                invest = ["hp", "atk", "spe"]
            team.append(nm)
            cfg.append({"name": nm, "nature": m.get("nature"),
                        "invest": invest,
                        "moves": list(m.get("skills") or [])})
        if not team:
            continue
        stat["teams"] += 1
        good.append({"title": t.get("title") or "",
                     "author": t.get("author") or "",
                     "blood_magic": t.get("blood_magic") or "",
                     "source": "rocom-data",
                     "team": team, "cfg": cfg})
    if skipped:
        stat["members_skipped"] = skipped
    return good, stat


def _from_arena(dex, known):
    p = str(paths.ROOT / "data" / "external" / "rocoarena" / "teams.json")
    if not os.path.exists(p):
        return [], {"missing_file": p}
    raw = json.load(io.open(p, encoding="utf-8"))
    good, skipped = [], 0
    stat = {"species": {}, "teams": 0}
    for t in raw.get("teams") or []:
        team, cfg = [], []
        for m in t.get("members") or []:
            nm, how = _norm_species(m.get("name"), known)
            stat["species"][how] = stat["species"].get(how, 0) + 1
            if nm is None:
                skipped += 1
                continue
            invest = [ARENA_STAT[x] for x in (m.get("ivStats") or [])
                      if x in ARENA_STAT]
            if len(invest) != 3:
                invest = ["hp", "atk", "spe"]
            moves = [s.get("name") for s in (m.get("skills") or [])
                     if s.get("name")]
            team.append(nm)
            cfg.append({"name": nm, "nature": m.get("nature"),
                        "invest": invest, "moves": moves})
        if not team:
            continue
        stat["teams"] += 1
        good.append({"title": t.get("name") or "",
                     "author": t.get("author") or "",
                     "blood_magic": t.get("gameMagic") or "",
                     "source": "rocoarena",
                     "team": team, "cfg": cfg})
    if skipped:
        stat["members_skipped"] = skipped
    return good, stat


def load(dex, with_arena=True):
    """返回 (阵容列表, 统计)。阵容列表按「成员数降序、标题」排序。

    会按**内容**去重（不看标题）：外部数据里存在同一套阵容被不同作者
    重复上传的情况（实测 169 套里有 2 套内容完全一致）。
    """
    known = _species_set(dex)
    out, st1 = _from_rocom(dex, known)
    stats = {"rocom-data": st1}
    if with_arena:
        more, st2 = _from_arena(dex, known)
        out += more
        stats["rocoarena"] = st2
    seen = set()
    uniq = []
    for lu in out:
        key = (tuple(lu["team"]),
               tuple((c.get("nature"), tuple(c["invest"]), tuple(c["moves"]))
                     for c in lu["cfg"]))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(lu)
    stats["duplicates_removed"] = len(out) - len(uniq)
    out = uniq
    out.sort(key=lambda x: (-len(x["team"]), x["title"]))
    stats["teams"] = len(out)
    stats["members"] = sum(len(x["team"]) for x in out)
    return out, stats


if __name__ == "__main__":
    import simulator as SIM

    lineups, stats = load(SIM.Dex())
    print("阵容 %d 套，成员 %d 只" % (stats["teams"], stats["members"]))
    for src, st in stats.items():
        if isinstance(st, dict):
            print("  %-12s %s" % (src, st))
    print()
    for lu in lineups[:3]:
        print("[%s] %s / %s / %s"
              % (lu["source"], lu["title"], lu["author"], lu["blood_magic"]))
        for c in lu["cfg"]:
            print("    %-14s %-4s %-22s %s"
                  % (c["name"], c["nature"] or "-", "/".join(c["invest"]),
                     "、".join(c["moves"])))
