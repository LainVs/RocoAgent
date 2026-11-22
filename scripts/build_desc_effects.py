# -*- coding: utf-8 -*-
"""S4.5：技能描述正则抽取器 + 生成 data/skill_effects_desc.json。

为什么：1510 个技能里只有 1378 个有描述，描述是游戏内原文 ——
「吸血100%」「威力+25」「获得2层中毒」「先手+1」这些模式是**官方数值**，
不是猜测。正则抽出来后引擎就能吃，不用逐个手写。

生成物结构：
    {"source", "built_at",
     "skills": {技能名: {"desc": 原文,
                          "priority": int,          # 先手 +N
                          "lifesteal": float,       # 吸血比例
                          "power_bonus": int,       # 威力 +N
                          "status": {"poison": n, ...},  # 施加的异常状态
                          "mark": {"id": 层数},           # 施加的印记
                          "flags": ["打断","蓄力","脱离","迅捷","迸发"]}}}

usage:
    & $py scripts/build_desc_effects.py
"""
import io
import json
import os
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402

OUT = os.path.join(ROOT, "data", "skill_effects_desc.json")

# ---- 正则表（每个对应游戏内原文措辞，不猜） ----
RE_PRIORITY = re.compile(r"先手\s*([+-]?\d+)")
RE_LIFESTEAL = re.compile(r"吸血[:：]?\s*(\d+)\s*%")
RE_POWER_BONUS = re.compile(r"威力\s*\+\s*(\d+)")
RE_COMBO = re.compile(r"连击数\s*\+\s*(\d+)")
RE_ENERGY_SELF = re.compile(r"获得\s*(\d+)\s*点能量")
RE_ENERGY_DRAIN = re.compile(r"(?:敌方|对方)(?:失去|扣除)\s*(\d+)\s*点能量")
STATUS_RES = {
    "poison": re.compile(r"(\d+)\s*层中毒(?!印记)"),
    "burn": re.compile(r"(\d+)\s*层灼烧"),
    "freeze": re.compile(r"(\d+)\s*层冻结"),
    "parasite": re.compile(r"(\d+)\s*层寄生"),
}
MARK_RES = {
    "star_mark": re.compile(r"(\d+)\s*层星陨印记"),
    "poison_mark": re.compile(r"(\d+)\s*层中毒印记"),
    "wet_mark": re.compile(r"(\d+)\s*层湿润印记"),
}
FLAGS = ("打断", "蓄力", "脱离", "迅捷", "迸发", "禁足", "返场")

# 抽出来的字段 -> 引擎使用方式
USABLE = {"priority", "lifesteal", "power_bonus", "status", "mark",
          "energy_gain", "energy_drain"}


def parse_desc(desc):
    """从技能描述抽取效果。返回 dict（只含有内容的键）。"""
    if not desc:
        return {}
    out = {}
    m = RE_PRIORITY.search(desc)
    if m:
        try:
            v = int(m.group(1))
            if v:
                out["priority"] = v
        except ValueError:
            pass
    m = RE_LIFESTEAL.search(desc)
    if m:
        out["lifesteal"] = min(1.0, int(m.group(1)) / 100.0)
    m = RE_POWER_BONUS.search(desc)
    if m:
        out["power_bonus"] = int(m.group(1))
    status = {}
    for key, pat in STATUS_RES.items():
        m = pat.search(desc)
        if m:
            status[key] = int(m.group(1))
    if status:
        out["status"] = status
    mark = {}
    for key, pat in MARK_RES.items():
        m = pat.search(desc)
        if m:
            mark[key] = int(m.group(1))
    if mark:
        out["mark"] = mark
    m = RE_ENERGY_SELF.search(desc)
    if m:
        out["energy_gain"] = int(m.group(1))
    if "energy_gain" not in out:
        # 「获得10点能量」——中文里数字可能紧跟"点"，宽松匹配
        m2 = re.search(r"获得\s*(\d+)\s*点能量", desc)
        if m2:
            out["energy_gain"] = int(m2.group(1))
    m = RE_ENERGY_DRAIN.search(desc)
    if m:
        out["energy_drain"] = int(m.group(1))
    flags = [f for f in FLAGS if f in desc]
    if flags:
        out["flags"] = flags
    return out


def main():
    db = sqlite3.connect(paths.DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id,name,power,cost,type_name,desc FROM skills "
        "WHERE desc IS NOT NULL AND desc<>''").fetchall()
    try:
        db.close()
    except Exception:
        pass

    skills = {}
    n_parsed = 0
    for r in rows:
        fx = parse_desc(r["desc"])
        # 只保留有"可用效果"的（flags 单独存，供 UI 显示）
        usable = {k: v for k, v in fx.items() if k in USABLE}
        if usable:
            usable["desc"] = (r["desc"] or "").strip()
            skills[r["name"]] = usable
            n_parsed += 1

    payload = {
        "source": "skills.desc 正则抽取（游戏内原文措辞）",
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "priority/lifesteal/power_bonus/status/mark 已由引擎消费；"
                "flags（打断/蓄力/脱离/迅捷/迸发）是待实现机制的标记",
        "count": len(skills),
        "skills": skills,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    print("写出 %s（%d KB）" % (OUT, os.path.getsize(OUT) // 1024))
    print("  抽取出可用效果的技能 %d / %d" % (n_parsed, len(rows)))
    c = Counter()
    for v in skills.values():
        for k in ("priority", "lifesteal", "power_bonus", "status", "mark"):
            if k in v:
                c[k] += 1
    print("  分类:", dict(c))
    # 样例
    for k in ("先发制人", "汲取", "溶解扩散", "阻断"):
        if k in skills:
            print("  [%s] %s" % (k, {kk: vv for kk, vv in skills[k].items()
                                     if kk != "desc"}))


if __name__ == "__main__":
    from collections import Counter
    sys.exit(main())
