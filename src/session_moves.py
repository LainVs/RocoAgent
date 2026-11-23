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
# BAND 名字带专用门槛（2026-11-23）
#
# 为什么与 BAND_MIN_CONF 分开：BAND 的候选词表是"配置 ∪ 可学池"
# （几十个），而 Ocr.match 的分数是逐候选算分、**与词表规模无关**。
# 旧代码按 `len(vocab) < 60` 切换 0.80/0.90，会在词表跨 60 的瞬间
# 让同一个匹配从采纳变拒绝 —— 这是个只会静默出错的边界陷阱。
#
# 0.80 的依据：实测精确匹配 1.00；1 字偏差 0.50~0.87；
# Ocr.match 对"包含关系"（t in c）会提到 0.85。
# 所以 0.80 能放过包含关系，又挡住单字误配。
BAND_MIN_CONF_BAND = 0.80
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
        # ★ 2026-11-23 候选词表 = my_team 配置 ∪ species_skills 可学池
        #
        # 并集而非二选一，因为两个来源漏的地方相反：
        #   「扬沙」配置有/池内无 -> 需要配置补
        #   「吞噬」配置无(过期)/池内有 -> 需要池补
        # 详见 _band_vocab 的 docstring。
        vocab, tier = _band_vocab(db, pet)
    finally:
        db.close()
    name, sc = ocr_cn.Ocr.match(raw, vocab)
    # ★ 门槛不依赖词表规模（2026-11-23 修正）
    #
    # 旧写法 `0.80 if len(vocab) < 60 else 0.90` 有个陷阱：词表规模
    # 跨过 60 时门槛会**突然**从 0.80 跳到 0.90，而 Ocr.match 的分数
    # 是逐候选算分、**与词表规模无关** —— 于是同一个匹配会在词表变大
    # 的瞬间从"采纳"变成"拒绝"。现在并集最大 59（卡瓦重），暂时安全，
    # 但只要队伍换个技能就可能跨线。
    #
    # 统一用 0.80：实测精确匹配都给 1.00，1 字偏差给 0.50~0.87。
    # 0.80 能放过"包含关系"（Ocr.match 对 t in c 会提到 0.85），
    # 又挡住单字误配。唯一性兜底有自己的安全保证，不受这个门槛约束。
    thr = BAND_MIN_CONF_BAND
    how = "match"
    # ★ 唯一性近似兜底（2026-11-23）
    # 标准匹配没过线时，若"等长 + 编辑距离<=1"的候选**唯一**，采纳它。
    # 实测：raw='诉毁' 唯一指向「诋毁」；raw='香噬' 唯一指向「吞噬」；
    # 而在多候选时（诋毁/底毁/抵毁）一律拒绝 —— 模糊即拒，安全。
    if not (name and sc >= thr):
        near = _unique_near(raw, vocab)
        if near:
            name, sc, how = near, max(float(sc), thr), "near-unique"
    # 诊断：把"读到了什么"和"为什么拒"一起缓存，供日志与前端查看。
    # 只记 raw 与词表规模，不记整条词表（太大）。
    _BAND_RAW[key] = (raw, len(vocab), thr, tier, now, how)
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


def _band_vocab(db, pet):
    """BAND 的候选词表：`my_team` 配置 ∪ `species_skills` 可学池。

    ## 为什么是并集，不是二选一（2026-11-23 两次实机教训）

    两个数据源各自都有**确定的漏**，而且漏的地方相反：

    | 场景 | 配置有? | 可学池有? | 只有配置能救 | 只有池能救 |
    |------|---------|-----------|--------------|------------|
    | 「扬沙」卡瓦重 | ✅ | ❌ | ✅ | |
    | 「吞噬」音速犬 | ❌（表过期） | ✅ | | ✅ |

    - 「扬沙」：`species_skills` 漏了（`species` 里「卡瓦重」有 4 个
      同名 id 3042/3294/3295/3296，跨行取并集仍缺）。需要配置补。
    - 「吞噬」：配置过期（用户实机把槽1 从「借用」换掉了），4 个词
      里没有它。需要可学池补。

    先写成"配置优先独占"时，第二种场景被卡死 —— 实测 `raw='香噬'`
    （「吞噬」的形近误读）连拒三次，因为配置层根本没有「吞噬」。

    ## 为什么并集是安全的

    - `ocr_cn.Ocr.match` 逐候选算分取最高，**与词表规模无关**，
      并入更多词不会稀释已有词的分数
    - 实测 5 个精灵并集规模 44~59，**都没跨 60**，门槛稳定
    - 唯一性兜底在更大词表下更容易"多候选 -> 拒绝"，只会更保守

    ## 返回

    (vocab, tier)。tier 用于日志诊断：
      "config+species" 两级都有
      "config"         只有配置（该精灵不在 species 表里）
      "species"        只有可学池（不在 my_team 里）
      "all"            全库兜底
    """
    own = _owned_vocab(db, pet)
    learn = _learnable_vocab(db, pet)
    if own or learn:
        vocab = list(dict.fromkeys(list(own) + list(learn)))
        if own and learn:
            tier = "config+species"
        elif own:
            tier = "config"
        else:
            tier = "species"
        return vocab, tier
    return _all_vocab(db), "all"


def _owned_vocab(db, pet):
    """这只精灵在 `my_team` 里的 4 个技能（`_band_vocab` 的一级来源）。

    为什么配置是**最强证据**（2026-11-23 实机铁证）
    ----------------------------------------------
    卡瓦重槽2 的卡面清楚写着「扬沙」（`--band-dump` 落盘图已目视确认），
    `my_team` 配置也是「扬沙」，但 BAND 三次都拒了它（conf 0.00）：

        BAND -> 卡瓦重 槽2 未识别 (conf 0.00 < 0.80) raw='扬沙'

    因为 `species_skills` 认为卡瓦重学不了扬沙 —— 而该表**数据不全**：
    `species` 表里「卡瓦重」有 4 个同名 id（3042/3294/3295/3296），
    按名字查会跨这 4 行取并集，即便如此仍缺「扬沙」（游戏里确实带着）。

    配置是用户亲手配的，权威性最高；但它**可能过期**（用户实机换技能
    后没跑 team_db），所以只能作为并集的一半，不能独占。

    注意「吞噬」正好相反：它在音速犬的可学池里、不在配置里。
    单靠配置会漏掉它——见 `_band_vocab` 的说明。
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


def _lev(a, b):
    """编辑距离（Levenshtein）。技能名都很短（2~6 字），无需优化。"""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if not la:
        return lb
    if not lb:
        return la
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _unique_near(raw, vocab):
    """唯一性近似匹配：只在**有且仅有一个**候选时返回它。

    ## 为什么需要它（2026-11-23 实测）

    配置词表只有 4 个词时，OCR 的形近字错读会让标准匹配分跌破门槛：

        raw='诉毁'  vs 配置['借用','火云车','诋毁','闪光冲击']
        -> ocr_cn.Ocr.match 只给 0.50，被 0.80 门槛拒

    「诋」被读成「诉」是形近字系统性误读（同一场连读 3 次都一样），
    放大倍数从 1.0 扫到 3.5 也解决不了（见 docs/PLAN_OCR_SHAPE.md）。

    ## 为什么这个约束是安全的

    判据：等长 + 编辑距离 <= 1，且**候选唯一**。
    实测（20 组真实配置 + 4 组危险场景 + 5 组全库查询）：

        配置层 20/20 正确反查，0 误配
        raw='诋毁' 对 3 个形近词 -> 拒绝（多候选）
        raw='火云车' 对 火云马/火云箭 -> 拒绝
        raw='扬沙' 全库（扬尘/扬沙/流沙）-> 拒绝
        raw='鼎管奔流' 全库 -> 唯一「筛管奔流」，正确采纳

    **它在模糊时一律拒绝，只在唯一时采纳。** 这正是我们要的安全性 ——
    宁可漏读，不可误配。词表越小（4 个配置词），唯一性越容易成立，
    所以配置层效果最好；退化到全库时绝大多数情况会被拒，不会乱猜。
    """
    if not raw or not vocab:
        return None
    cands = [c for c in vocab
             if c and len(c) == len(raw) and _lev(raw, c) <= 1]
    if len(cands) == 1:
        return cands[0]
    return None


def band_stats():
    """诊断：名字带读法的缓存状态。"""
    now = time.time()
    return {"cached": len(_BAND_LAST),
            "fresh": sum(1 for v in _BAND_LAST.values()
                         if now - v[2] < _BAND_TTL)}


def raw_of(pet, slot):
    """诊断：上一次 OCR 的原文 / 词表规模 / 门槛 / 层级 / 匹配方式。

    返回 (raw, vocab_n, threshold, tier, how)。没读过返回
    ("", 0, 0.0, "", "")。

    用途：BAND 被拒时能一眼分清三种完全不同的情况 ——
      tier=config  被拒 -> 配置里真没这个词（用户换队了）
      tier=species 被拒 -> 可能只是可学池数据不全，技能本身是对的
      how=near-unique   -> 标准匹配没过，靠"唯一近似"兜底采纳的
    """
    e = _BAND_RAW.get((pet, int(slot)))
    if not e:
        return "", 0, 0.0, "", ""
    raw, nv, thr = e[0], e[1], e[2]
    tier = e[3] if len(e) >= 4 else ""
    # 元组是 (raw, nv, thr, tier, now, how)：how 在**最后**一位，
    # 因为 now 是第 5 位（历史格式兼容）。
    how = e[5] if len(e) >= 6 else ""
    return raw, nv, thr, tier, how


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
