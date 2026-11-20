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
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# 面板截图/调试图落在这里。之前这个常量根本没定义 —— ability_read 的 dump 参数
# 每帧抛 NameError 被 except 吞掉，于是"快路径"从未生效，只能退回整块 OCR（+3~5s）。
LOG_DIR = os.path.join(ROOT, "logs")


def _log(msg):
    """写进 bot_stdout.log（不依赖 robot，避免循环 import）。"""
    try:
        with open(os.path.join(LOG_DIR, "bot_stdout.log"), "a",
                  encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except OSError:
        pass

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
    """面板是否打开：在 PANEL_BOX 里找"一整块奶白卡片"。

    两个历史坑都要躲开：

    1. 最早查的是固定小窗 (960,350,300,40)。实测（20:38 那局）面板整体下移了
       ~60px，探针落在变暗的背景上 → 明明开着却判 False → open_panel 又点一下
       把面板关掉，于是永远打不开。
    2. 后来改成"整屏中间找大白块"，阈值 0.06。实测 21:26:19 误判：对面训练家
       的白衬衫占满了中列，被判成面板已经开着 → 不点开面板，直接对着场景 OCR，
       白花 6.7 秒，最后还点了一次"关闭"位置。

    现在只看 PANEL_BOX 这一块（面板永远在这里），并且要求**最大的那块白连通域
    占整块 >= 15%**：

        真面板  panel_210501 / 211304 / 211401 : 白 0.195~0.201，大块 0.185~0.194
        误判帧  panel_212619（训练家白衬衫）   : 白 0.115，    大块 0.108
    """
    import cv2
    import numpy as np
    img = screen.grab_client(PANEL_BOX)
    if img is None or img.size == 0:
        return False
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    v = hsv[:, :, 2].astype(np.int16)
    s = hsv[:, :, 1].astype(np.int16)
    m = ((v > 195) & (s < 50)).astype(np.uint8)
    if m.mean() < 0.17:
        return False
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, _lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    total = float(img.shape[0] * img.shape[1])
    for i in range(1, n):
        if st[i, 4] / total > 0.15:
            return True
    return False


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
    for n, (tx, ty) in enumerate(FOE_BUST_CLICKS):
        px, py = screen.phys_client(tx, ty)
        if click is not None:
            click(px, py)
        elif session is not None:
            if asyncio.iscoroutinefunction(session.click_at):
                asyncio.get_event_loop().run_until_complete(
                    session.click_at(px, py))
            else:
                session.click_at(px, py)
        # 轮询而不是"睡死"：面板动画约 0.3~0.5s，一旦检测到就立刻返回。
        # 主候选点 0.9s（实机 20:40/20:45 两次都是第一个点就开），
        # 备用点各 0.6s —— 最坏 2.1s，常见 0.4~0.8s。
        budget = 0.9 if n == 0 else 0.6
        t0 = time.time()
        while time.time() - t0 < budget:
            time.sleep(0.12)
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
        px, py = screen.phys_client(*PANEL_CLOSE_CLICK)
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
    import ability_read

    big = cv2.resize(rgb, None, fx=upscale, fy=upscale,
                     interpolation=cv2.INTER_CUBIC)
    # 复用 ability_read 的引擎单例：每次 new RapidOCR() 要重新加载模型，
    # 一次就 1~2 秒，而这条路径的预算只有 1.5 秒。
    res, _ = ability_read._engine()(cv2.cvtColor(big, cv2.COLOR_RGB2BGR))
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

    t_panel = time.time()
    if not open_panel(screen, click=click, session=session):
        _log("FOE PANEL open FAILED")
        return None
    _log("FOE PANEL open in %.2fs" % (time.time() - t_panel))
    try:
        t0 = time.time()
        bust = screen.grab_client(FOE_PORTRAIT_BOX)
        # ---- 先读特性：黄标签定位 + 标签那一行横带 OCR（一次，~3 秒）
        ability, badge = None, None
        got = None
        try:
            import ability_read
            # 只调一次：read_ability 内部就是一次 OCR（badge 行横带，
            # 实测两种面板布局都能读全）。绝不在这里重试。
            got = ability_read.read_ability(
                screen, ability_read.feature_vocab(),
                dump=os.path.join(LOG_DIR, "panel_%s.png"
                                  % time.strftime("%H%M%S")))
            if got:
                ability = got.get("ability")
                badge = got.get("badge")
        except Exception as e:
            _log("FOE PANEL ability_read failed: %s" % e)
        t_ab = time.time()

        # ---- 特性够用就不再跑整块 OCR（那一步要 2~3 秒）
        enough = False
        unknown_trait = None
        if ability:
            try:
                import trait_db
                res = trait_db.resolve(ability)
                enough = bool(res["types"]) or len(res["candidates"]) == 1
            except Exception:
                enough = False
        elif got and got.get("raw"):
            # 字读出来了、词表里没有 -> 新特性（实测 '上锁'）。落盘留证据，
            # 以后补 features 表用；不算读失败。
            unknown_trait = " ".join(t for t, _c in got["raw"])[:80]
            _log("FOE PANEL unknown trait text: %s" % unknown_trait)
            try:
                import json
                with open(os.path.join(LOG_DIR, "unknown_traits.jsonl"),
                          "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"),
                                         "raw": unknown_trait},
                                        ensure_ascii=False) + "\n")
            except OSError:
                pass

        if fast:
            data = read_name(screen, ocr=ocr)
        elif enough:
            data = {"name": None, "level": None, "ability": ability,
                    "skill": None, "raw": [], "from": "trait_only"}
        else:
            # 不跑整块 OCR：那一步 6~10 秒，而且实测在"特性读不出/没入库"
            # 的面板上它同样拿不到名字。只读顶部名字条（~1.5 秒）。
            data = read_name(screen, ocr=ocr)
        if data is None:
            data = {"name": None, "level": None, "ability": None,
                    "skill": None, "raw": []}
        if not data.get("ability") and ability:
            data["ability"] = ability
            data["ability_badge"] = badge
        if data.get("ability"):
            data["ability_badge"] = badge
        try:
            _log("FOE PANEL timing: ability %.1fs, total %.1fs (trait_only=%s)"
                 % (t_ab - t0, time.time() - t0, enough))
        except Exception:
            pass
    finally:
        close_panel(screen=screen, click=click, esc=esc, session=session)
    # 对面可以全员改名（昵称），名字读不到是常态 ——
    # 只要读到特性就继续：特性才是身份键。
    if not data or not (data.get("name") or data.get("ability")):
        return data

    sid = None
    if data.get("name"):
        db = P.connect()
        try:
            row = db.execute("SELECT id FROM species WHERE name=? "
                             "ORDER BY implemented DESC, total DESC LIMIT 1",
                             (data["name"],)).fetchone()
            sid = row["id"] if row else None
        finally:
            db.close()

    # ---- 特性反查（名字不可靠时用特性；名字可靠时用来交叉校验）
    # 实测：1024 只物种里 583 只可以只靠特性确定属性，
    # 194 只的特性有歧义（同特性多属性），63 只特性未识别。
    # 歧义时 types=None —— 绝不猜，否则伤害计算会被污染。
    try:
        import trait_db
        res = trait_db.resolve(data.get("ability"), species_id=sid)
        data["types"] = res["types"]
        data["types_how"] = res["how"]
        data["types_conf"] = "none" if res["ambiguous"] else "high"
        data["trait_candidates"] = res["candidates"]
        # 宽松结果：拿不准时也给一个默认值，供决策层"宁可按默认算"。
        # 严格结果 types 保持原样（None = 不猜），两者并存。
        data["best_types"] = res.get("best_types")
        data["best_species"] = res.get("best_species")
        data["best_how"] = res.get("best_how")
        data["best_conf"] = res.get("best_conf")
        if sid is None and len(res["candidates"]) == 1:
            sid = res["candidates"][0]        # 特性唯一 -> 直接定物种
        elif sid is not None and res["candidates"] and sid not in res["candidates"]:
            # 名字反查的物种不在特性候选里 -> 两者冲突，以特性为准更安全
            data["name_conflict"] = True
            if len(res["candidates"]) == 1:
                sid = res["candidates"][0]
    except Exception as e:
        data["types_error"] = "%s: %s" % (type(e).__name__, e)

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
