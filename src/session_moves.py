# -*- coding: utf-8 -*-
"""session_moves.py —— 我方技能的会话级动态解析（不依赖 my_team 表）。

为什么需要
----------
用户经常临时换队伍/换精灵，`my_team` 表经常过期。过期后
`my_card_costs` 查耗能、`_skill_meta` 查技能名会全部错位，表现为
"策略选了低伤害技能"，很难排查。

实测发现（2026-11-22，见 docs/PLAN_DYNAMIC_SKILLS.md §一）：
**每张技能卡的正下方印着技能名**（矿物置换/碎银/突袭，三帧一致，
封闭词表 1.00）。用它做第二层证据，my_team 降级为第三层（缓存）。

三层优先级
----------
1. session  会话缓存：(pet, slot) -> 技能名，跨局复用，落盘
2. band     卡面直读：换精灵后一次性 OCR 名字带，conf>=0.90 才采纳
3. team_db  旧表：命中且与观测不矛盾才用

用法
----
    import session_moves as SM
    SM.get(my_name, slot)              -> 技能名 | None
    SM.confirm(my_name, slot, name)    -> None（外部证据，如播报）
    SM.note_team_stale(my_name, slot)  -> None（表与观测矛盾）
    SM.snapshot(my_name)               -> {slot: {"name","source"}}
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "roco.sqlite3")
STORE = os.path.join(ROOT, "data", "session_moves.json")

# 采纳门槛：OCR + 封闭词表匹配分数
BAND_MIN_CONF = 0.90
# 校验环：连续 N 次观测矛盾 -> 撤销确认
REVOTE_LIMIT = 2

_LOCK = threading.Lock()
_DATA = None          # {"pets": {name: {str(slot): {...}}}, "stale": {...}}
_DIRTY = False


# ---------------------------------------------------------------- 存储
def _load():
    global _DATA
    if _DATA is not None:
        return _DATA
    try:
        with open(STORE, encoding="utf-8") as fh:
            _DATA = json.load(fh)
        if not isinstance(_DATA, dict) or "pets" not in _DATA:
            _DATA = {"pets": {}, "stale": {}}
    except Exception:
        _DATA = {"pets": {}, "stale": {}}
    return _DATA


def _save():
    global _DIRTY
    if not _DIRTY:
        return
    try:
        tmp = STORE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_DATA, fh, ensure_ascii=False)
        os.replace(tmp, STORE)
        _DIRTY = False
    except Exception:
        pass


def clear(pet=None):
    """清空缓存（测试/换库后）。pet=None 清全部。"""
    global _DIRTY
    with _LOCK:
        d = _load()
        if pet is None:
            d["pets"] = {}
        else:
            d["pets"].pop(pet, None)
        _DIRTY = True
        _save()


# ---------------------------------------------------------------- 查询
def get(pet, slot):
    """当前 (pet, slot) 的技能名；没有确认答案返回 None。"""
    if not pet or not slot:
        return None
    with _LOCK:
        d = _load()
        e = d["pets"].get(pet, {}).get(str(int(slot)))
        if not e:
            return None
        return e.get("name") or None


def source(pet, slot):
    """答案来源：session / band / team_db / None。"""
    if not pet or not slot:
        return None
    with _LOCK:
        d = _load()
        e = d["pets"].get(pet, {}).get(str(int(slot)))
        return e.get("source") if e else None


def snapshot(pet):
    """{slot:int -> {"name","source"}}，给日志/前端用。"""
    with _LOCK:
        d = _load()
        out = {}
        for k, e in (d["pets"].get(pet) or {}).items():
            try:
                out[int(k)] = {"name": e.get("name"),
                               "source": e.get("source")}
            except (TypeError, ValueError):
                continue
        return out


def is_stale(pet):
    """my_team 表对这只精灵是否被观测否定过（只提示，不阻断）。"""
    with _LOCK:
        d = _load()
        return bool(d.get("stale", {}).get(pet))


# ---------------------------------------------------------------- 写入
def _set(pet, slot, name, src):
    global _DIRTY
    d = _load()
    d["pets"].setdefault(pet, {})[str(int(slot))] = {
        "name": name, "source": src,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _DIRTY = True


def confirm(pet, slot, name, src="session"):
    """写入一个**已确认**的技能名（播报 OCR / 卡面直读 / 校验通过）。

    src: "session"（播报/能量差推断）、"band"（卡面名字带）、"team_db"。
    """
    if not pet or not slot or not name:
        return
    with _LOCK:
        cur = _load()["pets"].get(pet, {}).get(str(int(slot)))
        if cur and cur.get("name") == name:
            return
        _set(pet, slot, name, src)
        _save()


def note_team_stale(pet):
    """my_team 表与实机观测矛盾时记录一次（只提示，不删表）。"""
    if not pet:
        return
    with _LOCK:
        d = _load()
        n = int(d.get("stale", {}).get(pet, 0) or 0) + 1
        d.setdefault("stale", {})[pet] = n
        _DIRTY = True
        _save()


def veto(pet, slot):
    """校验环判定矛盾：撤销确认，回退 unknown。"""
    if not pet or not slot:
        return
    with _LOCK:
        d = _load()
        d["pets"].get(pet, {}).pop(str(int(slot)), None)
        _DIRTY = True
        _save()


# ---------------------------------------------------------------- 名字带读法
_BAND_LAST = {}       # (pet, slot) -> (name, conf, at)
_BAND_TTL = 600.0     # 同一槽位 10 分钟内不重复 OCR（名字不会变）


def read_band(pet, slot, screen, pill_box):
    """读一张卡下方的技能名带（OCR，慢，只在缓存 miss 时调）。

    pill_box: (x0, y0, x1, y1) 技能栏坐标系里主胶囊的位置。
    返回 (name, conf)；读不出返回 (None, 0.0)。

    ROI 实测（docs/PLAN_DYNAMIC_SKILLS.md §一）：胶囊下缘 +0~40px、
    左右各扩 70px，印着技能名（矿物置换 / 碎银 / 突袭，三帧一致）。
    """
    if screen is None or not pill_box:
        return None, 0.0
    key = (pet, int(slot))
    now = time.time()
    hit = _BAND_LAST.get(key)
    if hit and now - hit[2] < _BAND_TTL:
        return hit[0], hit[1]

    x0, y0, x1, y1 = (int(v) for v in pill_box)
    try:
        img = screen.grab_client((0, 0, 2560, 1600))
    except Exception:
        return None, 0.0
    if img is None or getattr(img, "size", 0) == 0:
        return None, 0.0
    # SKILL_BOX 在 2560 基准客户区坐标 (250,480,780,1520)；
    # pill_box 是技能栏裁图内的坐标，平移回整帧。
    sx, sy = 250, 480
    roi = img[sy + y1:sy + y1 + 40, sx + max(0, x0 - 70):sx + x1 + 70]
    if roi.size == 0 or roi.shape[0] < 14:
        return None, 0.0

    import cv2
    import ability_read as AR
    import ocr_cn
    big = cv2.resize(roi, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
    try:
        res, _ = AR._engine()(cv2.cvtColor(big, cv2.COLOR_RGB2BGR))
    except Exception:
        return None, 0.0
    raw = " ".join(r[1] for r in (res or [])).strip()
    if not raw:
        # 空区域也缓存，避免同一空带反复 OCR（_band_stats 可见）
        _BAND_LAST[key] = (None, 0.0, now)
        return None, 0.0
    db = sqlite3.connect(DB)
    try:
        vocab = [r[0] for r in db.execute(
            "SELECT DISTINCT name FROM skills WHERE name IS NOT NULL")]
    finally:
        db.close()
    name, sc = ocr_cn.Ocr.match(raw, vocab)
    if name and sc >= BAND_MIN_CONF:
        with _LOCK:
            confirm(pet, slot, name, src="band")
        _BAND_LAST[key] = (name, sc, now)
        return name, sc
    _BAND_LAST[key] = (None, sc, now)
    return None, float(sc)


def band_stats():
    """诊断：名字带读法的缓存状态。"""
    now = time.time()
    return {"cached": len(_BAND_LAST),
            "fresh": sum(1 for v in _BAND_LAST.values()
                         if now - v[2] < _BAND_TTL)}


# ---------------------------------------------------------------- 自检
def _selftest():
    ok = True
    import tempfile
    global STORE, _DATA, _DIRTY
    tmp = tempfile.mktemp(suffix=".json")
    STORE, _DATA, _DIRTY = tmp, None, False

    confirm("音速犬", 1, "火云车", src="band")
    ok &= get("音速犬", 1) == "火云车"
    ok &= source("音速犬", 1) == "band"
    ok &= get("音速犬", 2) is None
    ok &= get(None, 1) is None and get("音速犬", None) is None

    # 播报确认覆盖 band 来源
    confirm("音速犬", 1, "闪光冲击", src="session")
    ok &= get("音速犬", 1) == "闪光冲击"
    ok &= source("音速犬", 1) == "session"

    # 校验环撤销
    veto("音速犬", 1)
    ok &= get("音速犬", 1) is None

    # snapshot / stale
    confirm("卡瓦重", 3, "撞鬼", src="team_db")
    snap = snapshot("卡瓦重")
    ok &= snap.get(3, {}).get("name") == "撞鬼"
    note_team_stale("卡瓦重")
    ok &= is_stale("卡瓦重")
    ok &= not is_stale("音速犬")

    # 重载后仍在（落盘生效）
    _DATA = None
    ok &= get("卡瓦重", 3) == "撞鬼"

    print("SELFTEST", "PASS" if ok else "FAIL")
    try:
        os.remove(tmp)
    except OSError:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
