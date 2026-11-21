# -*- coding: utf-8 -*-
"""update_team.py —— 把实盘读到的队伍写入 my_team 表。

数据来源：2026-11-21 10:40 通过开发板点击 + MCP 截图，
逐个点开队伍编辑界面的 6 个槽位读到的详情。

实测队伍（5 只，第 6 槽空）：
    slot1 音速犬      火      借用/火云车/诋毁/闪光冲击
    slot2 冰钻布鲁斯  冰      丢冰块/暗突袭/主轴/扬沙
    slot3 卡瓦重      草·冰   筛管奔流/扬沙/虫击/冰雹
    slot4 银月狼王    幽·幻   岩脉崩毁/月蚀/撞鬼/荆棘爪
    slot5 恶魔狼      恶      吞噬/火焰切割/蝙蝠/惊吓盒子
    slot6 （空）

用法：
    python scripts\\update_team.py            # 写入
    python scripts\\update_team.py --dry      # 只打印，不写
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "src"))

import paths

# 实盘读数（游戏内实际数值，不是种族值）
TEAM = [
    # (slot, 名称, 技能名列表, 生命, 物攻, 魔攻, 物防, 魔防, 速度, 特性)
    (1, "音速犬", ["借用", "火云车", "诋毁", "闪光冲击"],
     366, 221, 97, 171, 150, 260, "专注力"),
    (2, "冰钻布鲁斯", ["丢冰块", "暗突袭", "主轴", "扬沙"],
     345, 220, 80, 185, 157, 192, "冰钻"),
    (3, "卡瓦重", ["筛管奔流", "扬沙", "虫击", "冰雹"],
     352, 168, 73, 147, 140, 280, "诈死"),
    (4, "银月狼王", ["岩脉崩毁", "月蚀", "撞鬼", "荆棘爪"],
     417, 234, 116, 186, 168, 273, "铭记于月亮"),
    (5, "恶魔狼", ["吞噬", "火焰切割", "蝙蝠", "惊吓盒子"],
     417, 218, 104, 160, 129, 260, "悲悯"),
]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    dry = "--dry" in sys.argv
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row

    print("=== 准备写入的队伍 ===")
    rows = []
    for slot, name, moves, hp, atk, spa, dfn, spd, spe, trait in TEAM:
        sp = db.execute(
            "SELECT id,name FROM species WHERE name=? "
            "ORDER BY implemented DESC, total DESC LIMIT 1", (name,)).fetchone()
        if not sp:
            print("  !! %s 在 species 表里找不到" % name)
            return 1
        ids = []
        desc = []
        for mv in moves:
            r = db.execute("SELECT id,name,power,cost FROM skills "
                           "WHERE name=? LIMIT 1", (mv,)).fetchone()
            if not r:
                print("  !! 技能 %s 找不到（%s）" % (mv, name))
                return 1
            ids.append(r["id"])
            desc.append("%s(p%s/c%s)" % (r["name"], r["power"], r["cost"]))
        print("  slot%d %-10s %s" % (slot, name, " ".join(desc)))
        print("         生命%d 物攻%d 魔攻%d 物防%d 魔防%d 速度%d 特性=%s" % (
            hp, atk, spa, dfn, spd, spe, trait))
        rows.append((slot, sp["id"], hp, atk, spa, dfn, spd, spe,
                     json.dumps(ids), trait, name))

    if dry:
        print()
        print("（--dry：不写入）")
        db.close()
        return 0

    print()
    print("=== 写入 my_team ===")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        # 先清空，再按 slot 插入（第 6 槽留空）
        db.execute("DELETE FROM my_team")
        for slot, sid, hp, atk, spa, dfn, spd, spe, moves, trait, name in rows:
            db.execute(
                "INSERT INTO my_team (slot, species_id, level, nature_id, "
                "hp, atk, spa, def, spd, spe, max_hp, moves, note, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (slot, sid, 60, None, hp, atk, spa, dfn, spd, spe, hp,
                 moves, "%s 特性%s" % (name, trait), now))
        db.commit()
        print("  已写入 %d 只" % len(rows))
    except Exception as e:
        db.rollback()
        print("  !! 写入失败: %s: %s" % (type(e).__name__, e))
        db.close()
        return 1

    print()
    print("=== 复查 ===")
    for r in db.execute("SELECT slot,species_id,moves,note FROM my_team "
                        "ORDER BY slot"):
        sp = db.execute("SELECT name FROM species WHERE id=?",
                        (r["species_id"],)).fetchone()
        ids = json.loads(r["moves"] or "[]")
        names = []
        for mid in ids:
            s = db.execute("SELECT name FROM skills WHERE id=?",
                           (int(mid),)).fetchone()
            names.append(s["name"] if s else "?")
        print("  slot%d %-10s %s" % (
            r["slot"], sp["name"] if sp else "?", "/".join(names)))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
