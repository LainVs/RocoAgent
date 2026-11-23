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
_BAND_RAW = {}        # (pet, slot) -> (raw_text, vocab_n, threshold, at)
_BAND_TTL = 600.0     # 同一槽位 10 分钟内不重复 OCR（名字不会变）

# 调试落盘目录（robot --band-dump 会设置）。空字符串 = 关闭。
# 用途：槽位错位时，必须能目视看到"这个 box 指向哪张卡"，光看名字不够。
_DUMP_DIR = ""


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
    # ★ 2026-11-22 实机标定，2026-11-23 修正起点
    #
    # 名字带在**胶囊下缘** +3..+65、左右各扩 90（docs/LIVE_FINDINGS）。
    #
    # ⚠ 旧代码写成 `img[fy0 + 5 : fy1 + 95]` —— 从胶囊**上缘**+5 开始，
    # 把整颗胶囊连同里面的伤害数字一起圈进 ROI。实测后果（线上日志）：
    #
    #     raw='160'   raw='5z'       <- 胶囊里的伤害数字
    #     raw='OSE 恶魔..'
    #
    # OCR 先读出数字，词表匹配自然失败（conf 0.00），这就是"BAND 一直
    # 未识别"的最后一层原因。同一张真帧（logs/det2/now2_full.png）对比：
    #
    #     上缘起点(upper): '泡沫 | 14 影袭 | 能量 | 快速移'   <- 混入 14
    #     下缘起点(below): '泡沫 | 影袭 | 能量 | 快速移'      <- 4/4 全对
    #
    # 坐标系自适应：skill_pill_boxes 返回**整帧坐标**（已含 SKILL_BOX
    # 偏移）；测试假数据可能传裁图内坐标。判定标准：x0/y0 已经大于
    # SKILL_BOX 偏移 → 是整帧坐标，不再二次平移。
    sx, sy = 250, 480
    if x0 >= sx and y0 >= sy:
        fx0, fy0, fx1, fy1 = x0, y0, x1, y1
    else:
        fx0, fy0, fx1, fy1 = sx + x0, sy + y0, sx + x1, sy + y1
    roi = img[fy1 + 3:fy1 + 65,
              max(0, fx0 - 90):min(2560, fx1 + 90)]
    if roi.size == 0 or roi.shape[0] < 20:
        return None, 0.0

    # 调试落盘（--band-dump）：把**名字带 ROI** 和它上方的胶囊一起存图，
    # 用来目视核对"这个槽位读的到底是哪张卡"。槽位错位是当前唯一的
    # 残留问题，光靠日志里的名字无法判断对应关系。
    if _DUMP_DIR:
        try:
            import cv2 as _cv
            stem = os.path.join(_DUMP_DIR, "band_%s_%d_%d"
                                % (pet, int(slot), int(now)))
            # 胶囊 + 名字带（上下各留白），便于一眼看清对应关系
            ctx = img[max(0, fy0 - 10):min(1600, fy1 + 70),
                      max(0, fx0 - 100):min(2560, fx1 + 100)]
            _cv.imencode(".png", ctx[:, :, ::-1])[1].tofile(stem + "_ctx.png")
            _cv.imencode(".png", roi[:, :, ::-1])[1].tofile(stem + "_roi.png")
        except Exception:
            pass

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
        # ★ 2026-11-23 两级词表（优先级：用户配置 > 可学池 > 全库）
        #
        # 为什么把 `my_team` 配置放第一位 —— 实机铁证：
        #   卡瓦重槽2 卡面写着「扬沙」（--band-dump 落盘图目视确认），
        #   配置也是「扬沙」，但 BAND 三次拒掉（conf 0.00），因为
        #   species_skills 认为卡瓦重学不了扬沙（该表不全）。
        #
        # 为什么不能简单放宽门槛：门槛降到 0.30 会引入误配，
        # 「扬沙」在全库 1045 个技能里有大量近邻。安全性不能换。
        #
        # 为什么配置优先既安全又有效：
        #   - 只有 4 个候选词 -> 分母极小，1 字偏差的分数很高
        #   - 不可能配到别的精灵的技能（硬约束）
        #   - 它是用户亲手配的，权威性高于任何推导出来的池子
        own = _owned_vocab(db, pet)
        if own:
            vocab, tier = own, "config"
        else:
            own = _learnable_vocab(db, pet)
            if own:
                vocab, tier = own, "species"
            else:
                vocab, tier = _all_vocab(db), "all"
    finally:
        db.close()
    name, sc = ocr_cn.Ocr.match(raw, vocab)
    # 词表小时门槛可以放宽（分母小，1 字偏差对应的分数更高也更可信）
    thr = 0.80 if len(vocab) < 60 else BAND_MIN_CONF
    # 诊断：把"读到了什么"和"为什么拒"一起缓存，供日志与前端查看。
    # 只记 raw 与词表规模，不记整条词表（太大）。
    _BAND_RAW[key] = (raw, len(vocab), thr, tier, now)
    if name and sc >= thr:
        # ⚠ 不要在这里包 `with _LOCK:` —— confirm() 自己会加锁，
        # 而 _LOCK 是 threading.Lock()（**不可重入**），嵌套获取会自锁。
        # 2026-11-23 实测：这个写法让 read_band 永久挂死（faulthandler
        # 抓到 session_moves.py:288 卡在 confirm 的 with _LOCK:），
        # 表现为 BAND 一次都没成功采纳过（session_moves.json 至今不存在），
        # 且可能拖死感知线程。
        confirm(pet, slot, name, src="band")
        _BAND_LAST[key] = (name, sc, now)
        return name, sc
    _BAND_LAST[key] = (None, sc, now)
    return None, float(sc)


def _owned_vocab(db, pet):
    """第一级词表：这只精灵在 `my_team` 里的 4 个技能。

    为什么把配置放第一位（2026-11-23 实机铁证）
    -------------------------------------------
    卡瓦重槽2 的卡面清楚写着「扬沙」（`--band-dump` 落盘图已目视确认），
    `my_team` 配置也是「扬沙」，但 BAND 三次都拒了它（conf 0.00）：

        BAND -> 卡瓦重 槽2 未识别 (conf 0.00 < 0.80) raw='扬沙'

    因为 `species_skills` 认为卡瓦重学不了扬沙 —— 而该表**数据不全**：
    `species` 表里「卡瓦重」有 4 个同名 id（3042/3294/3295/3296），
    按名字查会跨这 4 行取并集，即便如此仍缺「扬沙」（游戏里确实带着）。

    `my_team.moves` 是**用户亲手配的**，比任何推导出来的池子都可信，
    而且只有 4 个词 —— 词表小到不可能误配。
    """
    try:
        row = db.execute(
            "SELECT moves FROM my_team WHERE species_id IN "
            "(SELECT id FROM species WHERE name=?) ORDER BY slot LIMIT 1",
            (pet,)).fetchone()
    except Exception:
        return []
    if not row or not row[0]:
        return []
    try:
        ids = json.loads(row[0])
    except Exception:
        return []
    out = []
    for i in ids:
        r = db.execute("SELECT name FROM skills WHERE id=?", (int(i),)).fetchone()
        if r and r[0]:
            out.append(r[0])
    return out


def _learnable_vocab(db, pet):
    """第二级词表：这只精灵可学技能池（species_skills，几十个）。

    ⚠ 已知不全（见 `_owned_vocab` 的注释）。它比全库好得多（小分母 +
    不会配到别的精灵的技能），但不能当作完备集合。
    同名多 id 会取并集 —— 这会混入别的变种的技能，是可接受的代价：
    这些变体通常是同一只精灵的不同形态，技能高度重叠。
    """
    rows = db.execute(
        """SELECT DISTINCT s.name FROM species_skills ss
           JOIN species sp ON sp.id = ss.species_id
           JOIN skills s ON s.id = ss.skill_id
           WHERE sp.name = ? AND s.name IS NOT NULL""", (pet,))
    return [r[0] for r in rows]


def _all_vocab(db):
    """第三级词表：全库（约 1045 个）。只在上面两级都拿不到时用。"""
    return [r[0] for r in db.execute(
        "SELECT DISTINCT name FROM skills WHERE name IS NOT NULL")]
    """诊断：名字带读法的缓存状态。"""
    now = time.time()
    return {"cached": len(_BAND_LAST),
            "fresh": sum(1 for v in _BAND_LAST.values()
                         if now - v[2] < _BAND_TTL)}


def band_stats():
    """诊断：名字带读法的缓存状态。"""
    now = time.time()
    return {"cached": len(_BAND_LAST),
            "fresh": sum(1 for v in _BAND_LAST.values()
                         if now - v[2] < _BAND_TTL)}


def raw_of(pet, slot):
    """诊断：上一次 OCR 读到的原文 + 词表规模 + 门槛 + 词表层级。

    返回 (raw, vocab_n, threshold, tier)。没读过返回 ("", 0, 0.0, "")。
    用途：BAND 被门槛拒绝时，日志要能显示"到底读成了什么字"以及
    "用的是哪一级词表" —— tier=config 时被拒说明配置里真没这个词，
    tier=species/all 时被拒可能只是数据不全（见 _owned_vocab 注释）。
    """
    e = _BAND_RAW.get((pet, int(slot)))
    if not e:
        return "", 0, 0.0, ""
    if len(e) >= 5:
        return e[0], e[1], e[2], e[3]
    return e[0], e[1], e[2], ""


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
