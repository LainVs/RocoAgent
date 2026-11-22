# -*- coding: utf-8 -*-
"""skill_icons.py —— 技能名 -> 图标 URL（/skill_icons/<技能id>.png）。

为什么单独一个模块
------------------
图标是 **技能 id** 命名的（`assets/skill_icons/<id>.png`，由
`scripts/import_skill_icons.py` 从第三方官方技能表建立 id 映射后导入）。
机器人手里只有技能名（team_db / session_moves 给的是名字），
所以这里做名字 -> id -> 文件存在性 的三步解析，并缓存。

覆盖（2026-11-23）
-----------------
    954 / 1269 个 DB 技能有图标（75.2%）
    my_team 20 个槽位里 18 个有（月蚀 / 撞鬼 缺）

缺图标时返回 None —— UI 显示序号占位，绝不编造。
"""
from __future__ import annotations

import os
import sqlite3
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_DIR = os.path.join(ROOT, "assets", "skill_icons")
DB = os.path.join(ROOT, "data", "roco.sqlite3")

_LOCK = threading.Lock()
_NAME2ID = None
_CACHE = {}


def _index():
    global _NAME2ID
    if _NAME2ID is not None:
        return _NAME2ID
    with _LOCK:
        if _NAME2ID is not None:
            return _NAME2ID
        out = {}
        try:
            db = sqlite3.connect(DB)
            try:
                for sid, name in db.execute(
                        "SELECT id,name FROM skills WHERE name IS NOT NULL"):
                    out.setdefault(name, int(sid))
            finally:
                db.close()
        except Exception:
            out = {}
        _NAME2ID = out
        return out


def icon_url(skill):
    """技能名（或 id）-> '/skill_icons/<id>.png'；没有图标返回 None。"""
    if not skill:
        return None
    if skill in _CACHE:
        return _CACHE[skill]
    sid = None
    if isinstance(skill, int) or (isinstance(skill, str) and skill.isdigit()):
        try:
            sid = int(skill)
        except (TypeError, ValueError):
            sid = None
    if sid is None:
        sid = _index().get(skill)
    url = None
    if sid:
        p = os.path.join(ICON_DIR, "%d.png" % sid)
        if os.path.exists(p):
            url = "/skill_icons/%d.png" % sid
    _CACHE[skill] = url
    return url


def available():
    try:
        return len(os.listdir(ICON_DIR)) > 1
    except OSError:
        return False
