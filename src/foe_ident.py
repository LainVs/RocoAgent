# -*- coding: utf-8 -*-
"""foe_ident.py —— 四通道融合识别「对面是谁」（属性作硬否决）。

为什么需要融合
--------------
单独任何一条通道都不可靠：

| 通道 | 问题 |
|---|---|
| 名字 OCR | **玩家能改昵称**，排位里大量「漆黑的冥古龙」「黑武士」 |
| HUD 头像模板 | 留一验证 Top-1 只有 42.1%（只擅长认见过的） |
| 立绘 ONNX | 整体 82.8%，但静止画面能掉到 1.3e-07 |
| 属性图标 | 只给属性，不给种族 |

但它们**互相约束**，所以关键不是「信谁」，而是**用属性做硬否决**：

    幽+恶 这个组合全库只有 2 只精灵（影集剪纸、冥古龙）
    幽+幻 只有 5 只

属性图标是玩家改不掉的，所以任何通道给出的候选，属性对不上就直接排除。
这把候选池从 1228 只压到个位数，剩下的用名字/头像/立绘投票。

决策顺序
--------
    1. 读属性图标 -> types（硬约束）
    2. 属性过滤候选池 -> cands（可能 2~93 只，取决于属性组合）
    3. 名字 OCR -> nickname
         a. nickname_map 命中 -> 若在 cands 里，直接确定
         b. 名字恰好是种族名且属性一致 -> 确定
    4. 头像模板在 cands 内打分 -> 最高分且领先足够 -> 确定
    5. 立绘 ONNX Top-5 与 cands 取交集 -> 唯一 -> 确定
    6. 只剩 1 只 -> 确定
    7. 否则返回候选列表，让上层决定（UI 显示「候选：A / B」）

**绝不猜。** 宁可返回 candidates 列表，也不要给一个错的名字 —— 错名字会让
伤害计算全盘错（属性错 -> 克制错 -> 伤害差 4 倍）。

对外接口
--------
    identify(screen, prev=None) -> dict
    candidates_by_types(types)  -> list[str]
    nickname_lookup(nick)       -> str|None
    bind(nickname, species)     -> None
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import paths                          # noqa: E402
import hud_ident                      # noqa: E402

# 阈值
PORTRAIT_MIN = 0.85     # 头像模板：定标留一验证 score>=0.85 时 94.6% 准确
PORTRAIT_MARGIN = 0.06  # 与第二名的分差（防止两个相似头像打架）
ONNX_MIN = 0.92         # 立绘通道：定标 conf>=0.90 时 93.2%，取 0.92 更保守
NAME_MIN = 0.75         # 名字与词表的相似度


# ------------------------------------------------------------------ 数据库
def _db():
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    return db


def candidates_by_types(types, subset=True):
    """属性组合 -> 候选种族名列表。

    types 为空时返回 []（表示"属性未知"，不是"无属性"）。

    subset=True（默认）
    -------------------
    只用**已读到的属性做包含匹配**，而不是精确相等。

    为什么必须这样（2026-11-21 实盘踩到）
    ------------------------------------
    对面「仪式巨像」在库里是 地+毒 双属性，但 HUD 上第二个属性图标
    被头像/血条挡住了，只读到「地」。如果要求"精确相等"，
    候选池里就只剩纯地系的 52 只，真正的答案反而不在里面。

    改成"候选的属性必须**包含**已读到的属性"之后：
        读到 ['地']      -> 地、地+毒、地+翼… 都在候选里
        读到 ['地','毒'] -> 只剩 地+毒
    读到的属性越多，池子越小；读漏了也不会把正确答案排除掉。
    """
    if not types:
        return []
    want = set(types)
    db = _db()
    out = []
    try:
        for r in db.execute(
                "SELECT name, type_names FROM species "
                "ORDER BY implemented DESC, total DESC"):
            try:
                ts = set(json.loads(r["type_names"] or "[]"))
            except Exception:
                continue
            if not ts:
                continue
            if (want <= ts) if subset else (ts == want):
                out.append(r["name"])
    except Exception:
        pass
    finally:
        db.close()
    # 去重保序
    seen = set()
    uniq = []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def types_of(name):
    """种族名 -> 属性列表。"""
    if not name:
        return []
    db = _db()
    try:
        r = db.execute(
            "SELECT type_names FROM species WHERE name=? "
            "ORDER BY implemented DESC, total DESC LIMIT 1", (name,)).fetchone()
        return json.loads(r["type_names"] or "[]") if r else []
    except Exception:
        return []
    finally:
        db.close()


# ------------------------------------------------------------------ 昵称
def nickname_lookup(nick):
    """昵称 -> 种族名。没记录返回 None。

    这是**玩家改名的唯一正解**：昵称本身不是身份，只是一个映射键。
    命中说明以前确认过（用户说的"特性反查"流程走通过一次）。
    """
    if not nick:
        return None
    db = _db()
    try:
        r = db.execute(
            "SELECT sp.name FROM nickname_map n JOIN species sp "
            "ON sp.id=n.species_id WHERE n.nickname=?", (nick,)).fetchone()
        return r["name"] if r else None
    except Exception:
        return None
    finally:
        db.close()


def bind(nickname, species_name):
    """把「昵称 -> 种族」写进 nickname_map。

    **只在确认过身份之后调用。** 误绑定比不绑定更糟：
    以后同一个昵称会一直返回错的种族。
    """
    if not nickname or not species_name:
        return False
    db = _db()
    try:
        r = db.execute("SELECT id FROM species WHERE name=? "
                       "ORDER BY implemented DESC, total DESC LIMIT 1",
                       (species_name,)).fetchone()
        if not r:
            return False
        sid = r["id"]
        row = db.execute("SELECT hits FROM nickname_map WHERE nickname=?",
                         (nickname,)).fetchone()
        if row:
            db.execute("UPDATE nickname_map SET hits=hits+1, "
                       "last_seen=datetime('now','localtime'), "
                       "species_id=? WHERE nickname=?", (sid, nickname))
        else:
            db.execute("INSERT INTO nickname_map VALUES(?,?,1,"
                       "datetime('now','localtime'),"
                       "datetime('now','localtime'))", (nickname, sid))
        db.commit()
        return True
    except Exception:
        return False
    finally:
        db.close()


# ------------------------------------------------------------------ 头像
_PORTRAIT_CACHE = {}


def _portrait_items(side="foe"):
    """加载头像模板库（{文件名: ndarray}）。"""
    if side in _PORTRAIT_CACHE:
        return _PORTRAIT_CACHE[side]
    try:
        import portraits
        lib = (portraits.FOE_LIB_DIR if side == "foe" else portraits.LIB_DIR)
        items = portraits.load_library(lib)
    except Exception:
        items = []
    _PORTRAIT_CACHE[side] = items
    return items


def _portrait_scores(screen, side="foe"):
    """HUD 头像与每个模板的相似度 -> [(种族名, 分数), ...]（降序）。

    注意：模板文件名不一定是种族名（wolf.png / 音速犬#4.png），
    所以要通过 portrait_db 解析。
    """
    try:
        import portraits
        import portrait_db as P
        m = portraits.SEARCH_MARGIN
        box = (portraits.foe_box_for(*screen.client_size, margin=m)
               if side == "foe"
               else portraits.box_for(*screen.client_size, margin=m))
        img = screen.grab_client(box)
        if img is None or getattr(img, "size", 0) == 0:
            return []
        items = _portrait_items(side)
        if not items:
            return []
        target = (box[2] - 2 * m, box[3] - 2 * m)
        scored = []
        import cv2
        ai = portraits._prepare(img)
        for fname, tpl in items:
            try:
                b = tpl
                if tuple(b.shape[:2][::-1]) != tuple(target):
                    b = cv2.resize(b, (int(target[0]), int(target[1])))
                if ai.shape[0] < b.shape[0] or ai.shape[1] < b.shape[1]:
                    b = cv2.resize(b, (ai.shape[1], ai.shape[0]))
                r = cv2.matchTemplate(ai, b, cv2.TM_CCOEFF_NORMED)
                scored.append((float(r.max()), fname))
            except Exception:
                continue
        scored.sort(reverse=True)

        # 文件名 -> 种族名
        out = []
        for sc, fname in scored:
            try:
                sid = P.species_of(fname, side)
                nm = P.species_name(sid) if sid else None
            except Exception:
                nm = None
            if nm:
                out.append((nm, sc))
        # 同一只精灵多个模板时取最高分
        best = {}
        for nm, sc in out:
            if nm not in best or sc > best[nm]:
                best[nm] = sc
        return sorted(((n, s) for n, s in best.items()),
                      key=lambda x: -x[1])
    except Exception:
        return []


# ------------------------------------------------------------------ 立绘
def _onnx_scores(screen, side="foe", topk=5):
    """立绘 ONNX 分类 -> [(种族名, 置信度), ...]。"""
    try:
        import portrait_onnx as PO
        if not PO.available():
            return []
        return PO.predict_region(screen, side, topk=topk) or []
    except Exception:
        return []


# ------------------------------------------------------------------ 融合
def identify(screen, prev=None, side="foe"):
    """识别对面是谁。

    返回：
        {
          "name":       str|None,      # 确定的种族名（None 表示没定下来）
          "types":      list[str],     # 属性（硬约束，可能是 []）
          "candidates": list[str],     # 属性过滤后的候选
          "nickname":   str|None,      # 屏幕原文（可能是玩家昵称）
          "source":     str,           # nickname|name|portrait|onnx|types|none
          "conf":       float,
          "conflict":   bool,          # 通道之间打架
          "detail":     dict,          # 各通道原始分数，便于排查
        }
    """
    out = {
        "name": None, "types": [], "candidates": [], "nickname": None,
        "source": "none", "conf": 0.0, "conflict": False, "detail": {},
    }

    # ---- 1. 属性图标（硬约束）
    try:
        types = hud_ident.read_types(screen, side)
    except Exception:
        types = []
    out["types"] = types
    cands = candidates_by_types(types)
    out["candidates"] = cands
    out["detail"]["n_candidates"] = len(cands)

    def _accept(name):
        """属性硬否决：候选的属性必须**包含** HUD 读到的属性。

        用包含而不是相等，是因为 HUD 上第二个图标经常被遮挡
        （实测对面「仪式巨像」是 地+毒，但只读到「地」）。
        要求相等会把正确答案排除掉；要求包含则只会放宽，不会误放。
        """
        if not name:
            return False
        if not types:
            return True          # 属性没读出来，无法否决
        got = set(types_of(name))
        return set(types) <= got

    # ---- 2. 名字（先当昵称查表）
    try:
        raw, nconf = hud_ident.read_name(screen, side, vocab=hud_ident._species_vocab())
    except Exception:
        raw, nconf = "", 0.0
    out["nickname"] = raw or None
    out["detail"]["name_conf"] = round(float(nconf), 3)

    if raw:
        hit = nickname_lookup(raw)
        if hit and _accept(hit):
            out.update(name=hit, source="nickname", conf=float(nconf))
            return out
        # 名字恰好是种族名
        if nconf >= NAME_MIN and _accept(raw):
            out.update(name=raw, source="name", conf=float(nconf))
            return out
        # 名字匹配到种族名但与属性冲突 -> 记下来（可能是昵称撞了种族名）
        if nconf >= NAME_MIN and types and not _accept(raw):
            out["conflict"] = True
            out["detail"]["name_rejected"] = raw

    # ---- 3. 只剩一个候选，直接定
    if len(cands) == 1:
        out.update(name=cands[0], source="types", conf=1.0)
        return out

    # ---- 4. 头像模板（只在候选集内挑）
    psc = _portrait_scores(screen, side)
    out["detail"]["portrait_top"] = [(n, round(s, 3)) for n, s in psc[:3]]
    if psc:
        top_name, top_sc = psc[0]
        second = psc[1][1] if len(psc) > 1 else 0.0
        in_cands = (not cands) or (top_name in cands)
        if top_sc >= PORTRAIT_MIN and (top_sc - second) >= PORTRAIT_MARGIN and in_cands:
            if _accept(top_name):
                out.update(name=top_name, source="portrait", conf=top_sc)
                return out
        elif cands and top_name not in cands:
            out["detail"]["portrait_conflict"] = top_name

    # ---- 5. 立绘 ONNX（与候选取交集）
    osc = _onnx_scores(screen, side)
    out["detail"]["onnx_top"] = [(n, round(float(s), 3)) for n, s in osc[:3]]
    if osc:
        oname, oconf = osc[0]
        if oconf >= ONNX_MIN and (not cands or oname in cands) and _accept(oname):
            out.update(name=oname, source="onnx", conf=float(oconf))
            return out
        # Top-5 里只有一个落在候选集内 -> 用那个
        if cands:
            inside = [n for n, _s in osc if n in cands]
            if len(inside) == 1 and _accept(inside[0]):
                out.update(name=inside[0], source="onnx", conf=float(oconf))
                return out

    # ---- 6. 定不下来：保持 prev（绝不抖），否则返回候选
    if prev and _accept(prev):
        out.update(name=prev, source="kept", conf=0.0)
        return out
    if cands:
        out["source"] = "types"
    return out


# ------------------------------------------------------------------ 自检
def _selftest():
    ok = True
    print("=== 属性 -> 候选数 ===")
    for t in (["幽", "恶"], ["幽", "幻"], ["火", "恶"], ["草", "冰"], ["火"]):
        c = candidates_by_types(t)
        print("  %-10s -> %2d 只  %s"
              % ("+".join(t), len(c), "、".join(c[:6]) + ("…" if len(c) > 6 else "")))

    if len(candidates_by_types(["幽", "恶"])) != 2:
        print("**FAIL** 幽+恶 应该是 2 只")
        ok = False

    print("\n=== 昵称反查 ===")
    for nick in ("黑武士", "漆黑的冥古龙", "不存在的昵称"):
        print("  %-12s -> %s" % (nick, nickname_lookup(nick)))

    print("\n=== 真实帧融合（logs/resultshots 前 6 张）===")
    import glob
    import numpy as np
    import cv2

    class _Shot:
        def __init__(self, img):
            self.img = img
            self.client_size = (2560, 1600)

        def grab_client(self, box):
            x, y, w, h = box
            return self.img[y:y + h, x:x + w][:, :, ::-1].copy()

    files = sorted(glob.glob(os.path.join(
        str(paths.ROOT), "logs", "resultshots", "*.jpg")))
    shown = 0
    for p in files:
        img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None or img.shape[1] < 2400:
            continue
        r = identify(_Shot(img), side="foe")
        print("  %-16s name=%-10s src=%-9s types=%-8s cands=%d"
              % (os.path.basename(p), r["name"] or "-", r["source"],
                 "+".join(r["types"]) or "-", len(r["candidates"])))
        shown += 1
        if shown >= 6:
            break

    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
