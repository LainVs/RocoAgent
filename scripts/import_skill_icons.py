# -*- coding: utf-8 -*-
"""import_skill_icons.py —— 把技能图标导入 assets/skill_icons/<技能id>.png

来源
----
`洛克PVP助手/app/wwwroot/`：
    native-data/authority/skill.json   官方技能表（含 icon 字段）
    roco-skill-icons/<图标id>.png      1872 张技能图标

为什么要做 id 映射
------------------
图标文件名是**图标 id**（如 704026），不是技能 id；技能 id 是
`skill.json[<技能id>].icon` 里 Texture2D 路径的最后一段数字。
本脚本建立 `技能id -> 图标id`，按**技能 id** 落盘，
这样 UI 只要知道技能 id 就能直接取图。

覆盖（2026-11-23 实测）
----------------------
    skill.json 条目      2032
    可用图标映射         1226
    我们 DB 技能覆盖     954 / 1269 = 75.2%
    my_team 20 个槽位    18 有图标（月蚀/撞鬼缺）

用法
----
    & $py scripts\\import_skill_icons.py
    & $py scripts\\import_skill_icons.py --dry
    & $py scripts\\import_skill_icons.py --src "D:/别的路径/wwwroot"
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402,F401

DEFAULT_SRC = (r"C:\Users\Lenovo\Downloads\洛克PVP助手\洛克PVP助手"
               r"\app\wwwroot")
OUT_DIR = os.path.join(ROOT, "assets", "skill_icons")


def build_map(src):
    """技能id -> 图标id（仅保留图标文件真实存在的）。"""
    icon_dir = os.path.join(src, "roco-skill-icons")
    skill_json = os.path.join(src, "native-data", "authority", "skill.json")
    if not os.path.isdir(icon_dir) or not os.path.exists(skill_json):
        return None, 0, 0
    available = {f[:-4] for f in os.listdir(icon_dir) if f.endswith(".png")}
    with io.open(skill_json, encoding="utf-8") as fh:
        data = json.load(fh)
    out = {}
    for k, v in data.items():
        icon = (v or {}).get("icon") or ""
        m = re.findall(r"/(\d+)\.\d+'", icon) or re.findall(r"/(\d+)\.", icon)
        if not m:
            continue
        iid = m[-1]
        if iid in available:
            try:
                out[int(k)] = iid
            except (TypeError, ValueError):
                continue
    return out, len(data), len(available)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    mapping, n_entries, n_icons = build_map(args.src)
    if mapping is None:
        print("跳过：源目录不存在或不完整（%s）" % args.src)
        return 0
    print("skill.json %d 条 / 图标 %d 张 / 可用映射 %d"
          % (n_entries, n_icons, len(mapping)))

    db = sqlite3.connect(str(paths.DB))
    try:
        ids = [int(r[0]) for r in db.execute(
            "SELECT id FROM skills WHERE id>0")]
    finally:
        db.close()
    wanted = [i for i in ids if i in mapping]
    print("我们 DB 技能 %d 个，有图标 %d 个（%.1f%%）"
          % (len(ids), len(wanted), 100.0 * len(wanted) / max(1, len(ids))))
    if args.dry:
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    copied = 0
    bytes_total = 0
    for sid in wanted:
        iid = mapping[sid]
        src = os.path.join(args.src, "roco-skill-icons", iid + ".png")
        dst = os.path.join(OUT_DIR, "%d.png" % sid)
        if os.path.exists(dst):
            bytes_total += os.path.getsize(dst)
            copied += 1
            continue
        try:
            shutil.copyfile(src, dst)
            bytes_total += os.path.getsize(dst)
            copied += 1
        except OSError as e:
            print("  失败 %s: %s" % (sid, e))
    print("写入 %d 张（%.1f MB）-> %s"
          % (copied, bytes_total / 1048576.0, OUT_DIR))

    note = os.path.join(OUT_DIR, "VERSION.md")
    with io.open(note, "w", encoding="utf-8") as fh:
        fh.write(
            "# 技能图标（第三方）\n\n"
            "- 来源：洛克PVP助手 `app/wwwroot/native-data/authority/skill.json`"
            "（官方技能表 icon 字段）+ `roco-skill-icons/`\n"
            "- 映射：技能 id -> 图标 id，按**技能 id** 落盘\n"
            "- 导入时间：%s\n"
            "- 覆盖：%d / %d 个 DB 技能\n"
            "- 只有图片取自第三方；本项目代码仍全部自己实现。\n"
            "- 该资源属于《洛克王国》官方美术，仅本地识别/展示用。\n"
            % (__import__("time").strftime("%Y-%m-%d %H:%M"),
               len(wanted), len(ids)))
    print("说明写入", note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
