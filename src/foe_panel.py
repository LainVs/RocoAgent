# -*- coding: utf-8 -*-
"""对手信息面板：点对面头像 -> 读特性/名字/等级/技能 -> 反查种族 -> 入库。

这是"信息录入"的主入口。游戏数据导出里没有的新精灵（实测 197 只没有特性，
其中 22 只是已实现的），在战斗里点开对面头像就能看到：名字、等级、属性图标、
弱点/抗性图标、特性名 + 描述、一个技能名 + 耗能。

实测（2026-11-19 01:20，人机对战 影子剪纸）：
    - 点击 (2185, 85) 客户区坐标，面板在屏幕正中弹出
    - 面板期间战斗暂停，可以慢慢 OCR，不会被回合超时打断
    - ESC 关闭面板，战斗继续
    - 整块面板 OCR 6.5 s / 15 行；只读一条竖带约 2-3 s
    - 面板显示的"受伤提升 3 个 / 受伤降低 4 个"和 type_chart 算出来的
      幽属性弱点(幽/恶/光) + 抗性(普通/武/毒/虫) 完全一致 -> 克制表可信

坐标都是 CLIENT 坐标（2560x1600 基准），配合 Screen.grab_client 用。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# 点这里打开面板（对面头像中心）
FOE_BUST_CLICK = (2185, 85)
# 对面 HUD 头像框，用来建模板库
FOE_PORTRAIT_BOX = (2148, 47, 84, 76)

# 面板打开时这一块是白底（名字行），关掉就变回场景
PANEL_PROBE = (960, 350, 300, 40)
# 一次 OCR 的竖带：名字 / 等级 / 特性 / 技能都在左半边
STRIP_BOX = (945, 345, 400, 815)

# 竖带内的 y 分段（相对 STRIP_BOX）
SEGMENTS = {
    "name": (0, 62),
    "level": (62, 112),
    "ability": (578, 640),
    "skill": (740, 800),
}


def is_open(screen):
    img = screen.grab_client(PANEL_PROBE)
    if img is None or img.size == 0:
        return False
    # 面板白底 + 深色文字：亮像素占比很高；战斗场景几乎不会有
    return int((img.min(axis=2) > 200).sum()) > 0.35 * img.shape[0] * img.shape[1]


def open_panel(screen, session):
    """点对面头像，等面板出来。返回是否成功。"""
    import time
    import asyncio
    cx, cy = screen.client_rect_on_screen()[:2]
    tx, ty = FOE_BUST_CLICK
    if asyncio.iscoroutinefunction(session.click_at):
        asyncio.get_event_loop().run_until_complete(
            session.click_at(cx + tx, cy + ty))
    else:
        session.click_at(cx + tx, cy + ty)
    for _ in range(10):
        time.sleep(0.25)
        if is_open(screen):
            return True
    return False


def close_panel(session):
    import asyncio
    if asyncio.iscoroutinefunction(session.key):
        asyncio.get_event_loop().run_until_complete(session.key("esc"))
    else:
        session.key("esc")


def read(screen, ocr=None, vocab=None):
    """读面板。返回 {'name','level','ability','skill','raw'}。

    一次 OCR 一条竖带，再按 y 分段归位；每一段都在封闭词表里做最近邻，
    这样"力量吞噬"被识别成"量吞噬"也能纠正回来。
    """
    import cv2
    import numpy as np
    import ocr_cn

    img = screen.grab_client(STRIP_BOX)
    if img is None or img.size == 0:
        return None
    if ocr is None:
        ocr = ocr_cn.Ocr()
    res = _raw_ocr(img)
    out = {"name": None, "level": None, "ability": None, "skill": None,
           "raw": res}
    labels = {"特性", "技能", "名字", "等级"}
    buckets = {k: [] for k in SEGMENTS}
    for item in res:
        y = item["y"]
        text = item["text"].strip()
        if not text or text in labels:
            continue
        for key, (a, b) in SEGMENTS.items():
            if a <= y < b:
                buckets[key].append(text)
    vocab = vocab or default_vocab()
    if buckets["name"]:
        out["name"] = ocr_cn.Ocr.match(" ".join(buckets["name"]),
                                       vocab["species"])[0]
    if buckets["level"]:
        digits = "".join(ch for ch in " ".join(buckets["level"]) if ch.isdigit())
        out["level"] = int(digits) if digits else None
    if buckets["ability"]:
        out["ability"] = ocr_cn.Ocr.match(" ".join(buckets["ability"]),
                                           vocab["features"])[0]
    if buckets["skill"]:
        out["skill"] = ocr_cn.Ocr.match(" ".join(buckets["skill"]),
                                        vocab["skills"])[0]
    return out


def _raw_ocr(rgb, upscale=2):
    import cv2
    from rapidocr_onnxruntime import RapidOCR

    big = cv2.resize(rgb, None, fx=upscale, fy=upscale,
                     interpolation=cv2.INTER_CUBIC)
    eng = RapidOCR()
    res, _ = eng(cv2.cvtColor(big, cv2.COLOR_RGB2BGR))
    out = []
    for r in (res or []):
        pts, txt = r[0], r[1]
        out.append({"x": int(pts[0][0] / upscale), "y": int(pts[0][1] / upscale),
                    "text": txt})
    return out


def learn_from_panel(screen, session, ocr=None):
    """完整流程：点头像 -> 读面板 -> 关面板 -> 存头像/特性/技能。

    返回 {'name','level','ability','skill','species_id','new_*'}，失败返回 None。
    战斗会在这期间暂停（实测面板打开时回合不推进），所以可以慢慢读。
    """
    import cv2
    import numpy as np
    import portrait_db as P

    if not open_panel(screen, session):
        return None
    try:
        bust = screen.grab_client(FOE_PORTRAIT_BOX)
        data = read(screen, ocr=ocr)
    finally:
        close_panel(session)
    if not data or not data.get("name"):
        return data

    db = P.connect()
    try:
        row = db.execute("SELECT id FROM species WHERE name=? "
                         "ORDER BY implemented DESC, total DESC LIMIT 1",
                         (data["name"],)).fetchone()
        sid = row["id"] if row else None
    finally:
        db.close()
    data["species_id"] = sid
    if sid is None:
        return data

    if bust is not None and bust.size:
        fname = "%s.png" % data["name"]
        path = os.path.join(P.dir_for("foe"), fname)
        buf = cv2.imencode(".png", cv2.cvtColor(bust, cv2.COLOR_RGB2BGR))[1]
        buf.tofile(path)
        P.add_portrait(fname, sid, "普通", "foe", "panel")

    if data.get("ability"):
        _, created = P.learn_feature(sid, data["ability"])
        data["new_feature"] = created
    if data.get("skill"):
        _, created = P.learn_skill(sid, data["skill"])
        data["new_skill"] = created
    return data


_vocab_cache = None


def default_vocab(db_path=None):
    """封闭词表：精灵名 / 特性名 / 技能名，全部来自库。"""
    global _vocab_cache
    if _vocab_cache is not None:
        return _vocab_cache
    import sqlite3
    from portrait_db import DB_PATH
    db = sqlite3.connect(db_path or DB_PATH)
    species = [r[0] for r in db.execute("SELECT DISTINCT name FROM species WHERE name IS NOT NULL")]
    features = [r[0] for r in db.execute("SELECT DISTINCT name FROM features WHERE name IS NOT NULL")]
    skills = [r[0] for r in db.execute("SELECT DISTINCT name FROM skills WHERE name IS NOT NULL")]
    db.close()
    _vocab_cache = {"species": species, "features": features, "skills": skills}
    return _vocab_cache


if __name__ == "__main__":
    import cv2
    import numpy as np
    import robot

    scr = robot.Screen()
    print("panel open:", is_open(scr))
    # 离线：从一张整屏图读
    if len(sys.argv) > 1:
        p = sys.argv[1]
        full = cv2.imdecode(np.fromfile(p, np.uint8), 1)[:, :, ::-1].copy()
        print(read(_FakeScreen(full)))
    else:
        print(read(scr))


class _FakeScreen:
    def __init__(self, rgb):
        self.rgb = rgb

    def grab_client(self, box):
        x, y, w, h = box
        return self.rgb[y:y + h, x:x + w]
