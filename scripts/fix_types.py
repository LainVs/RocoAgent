# -*- coding: utf-8 -*-
"""fix_types.py —— 补齐库里 type_names 为空的物种

两个来源，按优先级：
  1. 游戏原始数据 `pet_species.json`（有就用，最可靠）
  2. 人工表（来自 docs/PROJECT_MANAGEMENT.md 的 S5 名单 + 实机面板核对）

S5 那批 990xxxxxx 连原始数据都没有，只能走人工表；冬虫夏瓜的「虫/草」
在 20:38 的实机面板上已核对过。

用法
    python scripts\\fix_types.py            # 只看
    python scripts\\fix_types.py --apply    # 写入
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import paths  # noqa: E402

# 人工表：名字 -> 属性名列表（S5 名单，见 docs/PROJECT_MANAGEMENT.md）
MANUAL = {
    "冥古龙": ["幽", "龙"], "小冥龙": ["幽", "龙"],
    "地绡银龙": ["地"], "龙嵴金脉": ["电"],
    "阿布": ["普通"], "超能阿布": ["龙"],
    "乐乐鲨": ["水"], "鸡麻麻": ["水", "萌"],
    "蛋包蛋": ["普通"], "爆弹包": ["普通"],
    "蟹蟹椰": ["水", "草"], "沙咔蟹": ["水", "草"],
    "鲸羽": ["冰", "幻"], "圆环鲸羽": ["冰", "幻"],
    "豆粒鹅": ["冰"], "刨雪鹅": ["冰"],
    "西瓜虫": ["虫", "草"], "冬虫夏瓜": ["虫", "草"],
    "滴溜豚": ["水"], "旋流豚": ["水"],
    "吸泥鸥": ["地", "翼"], "泥沼飞鸥": ["地", "翼"],
    "花怨鳗": ["幽", "水"], "怨潮鳗": ["幽", "水"],
    "小翼龙": ["龙"], "迅翼龙": ["龙"],
    "小剪影": ["幽"], "影子剪纸": ["幽"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    tid = {r["name"]: r["id"] for r in db.execute("SELECT id, name FROM types")}

    raw = {}
    try:
        ps = json.load(open(str(paths.GAME_DATA) + r"\pet_species.json",
                            encoding="utf-8"))
        raw = ps
    except OSError:
        print("WARN 读不到 pet_species.json")

    empty = [r for r in db.execute(
        "SELECT id, name, types, type_names, provisional FROM species "
        "WHERE type_names IS NULL OR type_names='' OR type_names='[]'")]
    print("type_names 为空的物种: %d" % len(empty))

    fixed_raw = fixed_manual = skipped = 0
    for r in empty:
        sid, name = r["id"], r["name"]
        names = None
        src = ""
        j = raw.get(str(sid))
        if j and j.get("type_names"):
            names = list(j["type_names"])
            src = "原始数据"
        elif name in MANUAL:
            names = MANUAL[name]
            src = "人工表"
        if not names:
            skipped += 1
            print("   跳过 %-12s id=%-10d（无来源）" % (name, sid))
            continue
        ids = [tid[n] for n in names if n in tid]
        if len(ids) != len(names):
            print("   跳过 %-12s（属性名对不上 types 表: %s）" % (name, names))
            skipped += 1
            continue
        print("   %-12s id=%-10d -> %-12s [%s]"
              % (name, sid, "/".join(names), src))
        if a.apply:
            db.execute("UPDATE species SET types=?, type_names=? WHERE id=?",
                       (json.dumps(ids), json.dumps(names, ensure_ascii=False),
                        sid))
            if src == "原始数据":
                fixed_raw += 1
            else:
                fixed_manual += 1
    if a.apply:
        db.commit()
        print("\n写入完成：原始数据 %d 只、人工表 %d 只、跳过 %d 只"
              % (fixed_raw, fixed_manual, skipped))
    else:
        print("\n（未写入，加 --apply 生效）")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
