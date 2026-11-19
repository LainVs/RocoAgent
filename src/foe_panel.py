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

# 点这里打开面板（对面头像中心）。实测位置随 HUD 状态浮动，给三个候选点。
FOE_BUST_CLICK = (2192, 95)
FOE_BUST_CLICKS = ((2192, 95), (2194, 114), (2185, 80))
# 关闭面板：点面板下方的空白处（ESC 关不干净，下次点头像会点不动）
PANEL_CLOSE_CLICK = (1250, 1440)
# 对面 HUD 头像框，用来建模板库
FOE_PORTRAIT_BOX = (2148, 47, 84, 76)

# 面板打开时这一块是白底（名字行），关掉就变回场景
PANEL_PROBE = (960, 350, 300, 40)
# 整个面板（两个布局变体都覆盖）
PANEL_BOX = (930, 300, 730, 1060)
# 只要名字时用这个（快路径）
NAME_BOX = (935, 330, 340, 100)
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
    mx = img.max(axis=2)
    mn = img.min(axis=2)
    bright = float((mn > 200).mean())
    # 白底 + 深色文字：只有纯白（过曝的场景）不算，必须有字
    dark = int((mx < 120).sum())
    return bright > 0.45 and dark > 40


def open_panel(screen, click=None, session=None):
    """点对面头像，等面板出来。返回是否成功。

    头像的位置随 HUD 状态浮动（"等待对方思考"和轮到自己时差 30 多像素，
    实测 影子纸翦在 y≈78、龙息帕尔在 y≈114），所以按候选点逐个试，
    面板一出来就停。
    """
    import time
    import asyncio
    # 面板可能已经开着（上一轮扫描留下的）：这时候再点一下会把它关掉，
    # 于是"点了却没反应"——实测就是被这个坑掉的。
    if is_open(screen):
        return True
    cx, cy = screen.client_rect_on_screen()[:2]
    for tx, ty in FOE_BUST_CLICKS:
        px, py = screen.phys(tx, ty)
        if click is not None:
            click(px, py)
        elif session is not None:
            if asyncio.iscoroutinefunction(session.click_at):
                asyncio.get_event_loop().run_until_complete(
                    session.click_at(px, py))
            else:
                session.click_at(px, py)
        # 面板有入场动画，0.5 秒就判"没开"会误判：
        # 于是点下一个候选点，把刚开的面板又关掉（实测就是这样 4 连点都不成功）
        for _ in range(8):
            time.sleep(0.25)
            if is_open(screen):
                return True
    return False


def close_panel(screen=None, click=None, esc=None, session=None):
    """关掉面板：点面板下方的空白处。

    实测 ESC 有时关不干净，面板还挂着的时候再点下一只精灵的头像会"点不动"，
    所以优先用一次空白处点击；点完还开着才退回 ESC。
    """
    import asyncio
    import time

    def _click(x, y):
        if click is not None:
            click(x, y)
        elif session is not None:
            if asyncio.iscoroutinefunction(session.click_at):
                asyncio.get_event_loop().run_until_complete(
                    session.click_at(x, y))
            else:
                session.click_at(x, y)

    def _esc():
        if esc is not None:
            esc()
        elif session is not None:
            if asyncio.iscoroutinefunction(session.key):
                asyncio.get_event_loop().run_until_complete(session.key("esc"))
            else:
                session.key("esc")

    if screen is not None:
        if not is_open(screen):
            return
        px, py = screen.phys(*PANEL_CLOSE_CLICK)
        _click(px, py)
        time.sleep(0.35)
        if not is_open(screen):
            return
    _esc()


def read_name(screen, ocr=None, vocab=None):
    """只读名字+等级（约 1.5-2 秒），足够认出是哪只精灵。

    整块面板 OCR 要 4-6 秒，占了回合窗口的三分之一；而识别一只精灵只需要
    顶部那行名字。特性/技能属于"有空再补"，不放在这条快路径上。
    """
    import re
    import ocr_cn

    img = screen.grab_client(NAME_BOX)
    if img is None or img.size == 0:
        return None
    if ocr is None:
        ocr = ocr_cn.Ocr()
    res = _raw_ocr(img, upscale=2.0)
    if not res:
        return None
    vocab = vocab or default_vocab()
    best, best_s, level = None, 0.0, None
    for item in res:
        text = item["text"].strip()
        if not text:
            continue
        m = re.search(r"(\d{1,3})\s*级", text)
        if m:
            level = int(m.group(1))
        n, s = ocr_cn.Ocr.match(text, vocab["species"])
        if n and s > best_s:
            best, best_s = n, s
    if best is None or best_s < 0.90:
        return None
    return {"name": best, "level": level, "raw": res}


def read(screen, ocr=None, vocab=None):
    """读面板。返回 {'name','level','ability','skill','raw'}。

    面板布局会变（有的版本多一栏"速度对比"，特性/技能的位置整体下移 100+ 像素），
    所以不按固定坐标切，而是把整块面板 OCR 一遍，再把每一行拿去和三个封闭
    词表做最近邻 —— 精灵名 / 特性名 / 技能名各有上千个候选，比"第几行是什么"
    稳得多。
    """
    import ocr_cn

    img = screen.grab_client(PANEL_BOX)
    if img is None or img.size == 0:
        return None
    if ocr is None:
        ocr = ocr_cn.Ocr()
    res = _raw_ocr(img, upscale=1.5)
    if not res:
        return None
    vocab = vocab or default_vocab()
    out = {"name": None, "level": None, "ability": None, "skill": None,
           "raw": res}
    import re

    best_name = (None, 0.0)
    best_feat = (None, 0.0)
    best_skill = (None, 0.0)
    feat_idx = None      # 记住特性是哪一行，技能匹配时排除它
    best_feat_i = -1
    for idx, item in enumerate(res):
        text = item["text"].strip()
        if not text:
            continue
        if out["level"] is None:
            m = re.search(r"(\d{1,3})\s*级", text)
            if m:
                out["level"] = int(m.group(1))
        n, s = ocr_cn.Ocr.match(text, vocab["species"])
        # 名字只可能在面板顶部，描述里出现的词不算
        if n and s > best_name[1] and item["y"] < 170:
            best_name = (n, s)
        f, fs = ocr_cn.Ocr.match(text, vocab["features"])
        if f and fs > best_feat[1]:
            best_feat = (f, fs)
            best_feat_i = idx
        if idx == best_feat_i:
            continue          # 同一行不能既是特性又是技能
        k, ks = ocr_cn.Ocr.match(text, vocab["skills"])
        if k and ks > best_skill[1]:
            best_skill = (k, ks)
    if best_name[1] >= 0.90:
        out["name"] = best_name[0]
    if best_feat[1] >= 0.90:
        out["ability"] = best_feat[0]
    if best_skill[1] >= 0.90:
        out["skill"] = best_skill[0]
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


def learn_from_panel(screen, session=None, ocr=None, click=None, esc=None,
                     fast=True):
    """完整流程：点头像 -> 读名字 -> 关面板 -> 存头像/特性/技能。

    fast=True 只读名字（1.5-2 秒）；fast=False 读整块面板（4-6 秒），
    能顺带拿到特性和技能，适合人工复盘时用。
    返回 {'name','level','ability','skill','species_id','new_*'}。
    """
    import cv2
    import numpy as np
    import portrait_db as P

    if not open_panel(screen, click=click, session=session):
        return None
    try:
        bust = screen.grab_client(FOE_PORTRAIT_BOX)
        data = read_name(screen, ocr=ocr) if fast else read(screen, ocr=ocr)
        # 整块 OCR 经常读不到特性（小字 + 布局变化）——
        # 用"黄标签定位"再读一次，布局无关。
        if data is not None and not data.get("ability"):
            try:
                import ability_read
                got = ability_read.read_ability(
                    screen, ability_read.feature_vocab(),
                    dump=os.path.join(LOG_DIR, "panel_%s.png"
                                      % time.strftime("%H%M%S")))
                if got and got.get("ability"):
                    data["ability"] = got["ability"]
                    data["ability_badge"] = got.get("badge")
            except Exception:
                pass
    finally:
        close_panel(screen=screen, click=click, esc=esc, session=session)
    # 对面可以全员改名（昵称），名字读不到是常态 ——
    # 只要读到特性就继续：特性才是身份键。
    if not data or not (data.get("name") or data.get("ability")):
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

    # 特性是用来"反查 id"的，不是用来"补数据"的。
    # 库里已经有 269 条特性；读到的名字应该去匹配，匹配不上说明 OCR 错了
    # 或者真的是新特性 —— 记下来人工确认，绝不自动写库
    # （之前就是这样把"60级水桶模板"写进 features 的）。
    if data.get("ability"):
        fid = P.find_feature_id(data["ability"])
        if fid:
            data["feature_id"] = fid
            data["candidates"] = P.species_by_feature(data["ability"])
        else:
            data["unknown_ability"] = data["ability"]
    if data.get("skill") and data.get("species_name"):
        # 面板上那只显示 1 个技能，但它是最真实的证据（玩家真的带了）
        try:
            import foe_moves
            foe_moves.note_observation(data["species_name"], data["skill"])
            data["skill_noted"] = True
        except Exception:
            pass
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
