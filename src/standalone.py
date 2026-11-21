# -*- coding: utf-8 -*-
"""standalone.py —— 不依赖机器人进程的独立感知。

为什么需要
----------
用户问："伤害计算器能脱离机器人也可以使用吗？会自动识别吗？"

原来的答案是**不能**：`/api/state` 只读 `logs/live_state.json`，
而那个文件只有 robot.py 会写。机器人不跑 → 悬浮窗永远是空态。

但技术上完全可行：`perceive.Perc.observe()` 是**纯函数** ——
给它一个 Screen 就能从屏幕读出战斗状态（血条/技能卡/倒计时），
不需要机器人进程、不需要 BLE 板子、不需要任何决策逻辑。

实测：独立跑一次 observe 约 **80ms**，和机器人在跑时一模一样。

边界（必须说清楚）
------------------
这个模块能给的（纯视觉可读）：
    my_pct / foe_pct / my_color / foe_color
    battle / bar / countdown / energy
    四张技能卡的伤害数字和 lit 状态

这个模块**给不了**的（需要决策层或数据库）：
    · 对面是谁（要立绘识别 + 头像库，代价高，暂不做）
    · 我方是谁（同上）
    · 技能名（机器人从队伍配置查，不是读画面）
    · 伤害计算（pvp_engine 需要双方身份和属性）

所以独立模式下前端显示的是"**画面读数**"，不是"完整战斗面板"。
要完整面板仍然需要机器人 —— 但至少现在**伤害数字能看**了，
这恰好是"伤害计算器"最核心的部分。

用法
----
    python src/standalone.py            # 自己跑一次，打印读数
    python src/standalone.py --loop     # 连续跑，看实时变化

由 ui_server 在"没有机器人"时自动调用（见 ui_server.standalone_state）。
"""
from __future__ import annotations

import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, r"D:\Tools")

import hud_ident                      # noqa: E402

_lock = threading.Lock()
_perc = None
_screen = None
_thread_state = {"tid": None}      # Screen/mss 绑线程，见 _get() 的注释
_last = {"t": 0.0, "st": None}
_cache = {"at": 0.0, "state": None}
MIN_INTERVAL = 0.10       # 最快 10Hz，和机器人推送同量级

# ---- 身份识别（可选，有代价）
#
# 立绘识别一次约 80~120ms，**不能每帧跑**（10Hz 会占满 CPU）。
# 用两级缓存：
#   1. 时间节流：最多每 1.5 秒识别一次
#   2. 结果保持：识别到的名字一直用，直到"对面明显换了"才重认
#
# "明显换了"的判据：血量百分比突然变高（换人上场通常是满血）。
# 这比像素指纹稳 —— 实测同一只精灵帧间指纹距离能到 27.6，
# 拿指纹判换人会每帧都触发。
ID_INTERVAL = 1.5
_ident = {
    "at": 0.0,
    "foe": None,        # 种族名
    "foe_conf": 0.0,
    "self": None,
    "self_conf": 0.0,
    "foe_hp_at_id": None,
}


def identify(now=None, foe_hp_pct=None):
    """识别双方身份。带节流和换人检测。返回 dict（永不抛异常）。

    三条通道（2026-11-21 重构）
    --------------------------
    原来只有立绘 ONNX 一条，实测静止画面 conf 1.3e-07（等于瞎猜），
    所以计算器一打开就显示"未知"。现在改成：

        1. `foe_ident.identify()`  —— 四通道融合，**属性图标做硬否决**
           属性是玩家改名也改不掉的，龙+幽只有 4 只，幽+恶只有 2 只。
        2. 名字只当**昵称**用（排位有大量改名精灵），先查 nickname_map。
        3. 立绘 ONNX 作为补充，且必须落在属性过滤后的候选集内。

    我方身份不需要识别 —— 队伍是 `my_team` 表里已知的 5 只，
    头像模板只有 4/5 只覆盖。所以优先按头像模板匹配，命中就是确定答案。

    为什么不能每帧跑：立绘 ONNX 一次 80~120ms + OCR 一次 1~2 秒，
    10Hz 会占满 CPU。而且身份在**一回合内不会变**，没必要重复认。
    """
    now = now or time.time()
    if now - _ident["at"] < ID_INTERVAL:
        return _ident
    _ident["at"] = now

    # 换人检测：血量突然变高说明对面换了只满血的上场
    prev_hp = _ident.get("foe_hp_at_id")
    changed = (foe_hp_pct is not None and prev_hp is not None
               and foe_hp_pct > prev_hp + 25)

    try:
        _scr, _p = _get()
    except Exception:
        return _ident

    # ---- 敌方：四通道融合
    try:
        import foe_ident
        prev = None if changed else _ident.get("foe")
        res = foe_ident.identify(_scr, prev=prev, side="foe")
        if changed:
            _ident["foe"] = None
            _ident["foe_conf"] = 0.0
            _ident["foe_types"] = []
            _ident["foe_candidates"] = []
        if res.get("name"):
            if res["name"] != _ident.get("foe"):
                _log("FOE -> %s (%s, %.2f)"
                     % (res["name"], res.get("source"), res.get("conf") or 0))
            _ident["foe"] = res["name"]
            _ident["foe_conf"] = float(res.get("conf") or 0.0)
        _ident["foe_types"] = res.get("types") or []
        _ident["foe_candidates"] = res.get("candidates") or []
        _ident["foe_nickname"] = res.get("nickname")
        _ident["foe_source"] = res.get("source")
        _ident["foe_detail"] = res.get("detail") or {}
    except Exception as e:
        _log("FOE identify failed: %s: %s" % (type(e).__name__, e))

    # ---- 我方：名字直接查 my_team（队伍已知，5 只名字都不同）
    #
    # 为什么这条路最可靠（2026-11-21 实盘验证）
    # ------------------------------------------
    # 我方队伍是 `my_team` 表里已知的 5 只：音速犬 / 冰钻布鲁斯 / 卡瓦重 /
    # 银月狼王 / 恶魔狼。HUD 名字条 OCR 实测读到 "冰钻布鲁斯" conf=1.0。
    #
    # 比头像模板强：模板只有 4/5 只覆盖，实测匹配分 0.515，还把恶魔狼
    # 认成了"首领-恶魔狼"（13000152）。而名字只要命中 my_team 就绝不会错
    # —— 因为**我方不可能出现队伍外的精灵**。
    #
    # 注意：这里用名字是安全的，和敌方不同。敌方名字是玩家昵称（不可信），
    # 但我方是**我们自己的队伍**，名字就那 5 个，且 OCR 能读到。
    if _ident.get("self") is None:
        try:
            name = _self_by_name(_scr)
            if name:
                _ident["self"] = name
                _ident["self_conf"] = 1.0
                _log("SELF -> %s (名字)" % name)
        except Exception:
            pass

    # ---- 我方兜底 1：血条绝对值（"162/345" 的分母）
    if _ident.get("self") is None:
        try:
            name = _self_by_hp_bar(_scr)
            if name:
                _ident["self"] = name
                _ident["self_conf"] = 1.0
                _log("SELF -> %s (血条绝对值)" % name)
        except Exception:
            pass

    # ---- 我方兜底 2：头像模板（只有 4/5 有模板）
    if _ident.get("self") is None:
        try:
            import portraits
            import portrait_db as P
            m = portraits.SEARCH_MARGIN
            box = portraits.box_for(*_scr.client_size, margin=m)
            img = _scr.grab_client(box)
            items = portraits.load_library(portraits.LIB_DIR)
            if img is not None and items:
                stem, sc = portraits.identify(
                    img, items, target=(box[2] - 2 * m, box[3] - 2 * m))
                if stem and sc >= 0.75:
                    sid = P.species_of(stem + ".png", "self")
                    nm = P.species_name(sid) if sid else None
                    if nm and _in_my_team(nm):
                        _ident["self"] = nm
                        _ident["self_conf"] = float(sc)
                        _log("SELF -> %s (头像 %.2f)" % (nm, sc))
        except Exception:
            pass

    # 我方兜底 3：立绘 ONNX（必须和 my_team 里某只对得上）
    if _ident.get("self") is None:
        try:
            import portrait_onnx as PO
            if PO.available():
                res = PO.predict_region(_scr, "self", topk=3)
                if res:
                    name, conf = res[0]
                    if conf >= 0.90 and _in_my_team(name):
                        _ident["self"] = name
                        _ident["self_conf"] = float(conf)
                        _log("SELF -> %s (onnx %.2f)" % (name, conf))
        except Exception:
            pass

    _ident["foe_hp_at_id"] = foe_hp_pct
    return _ident


def _in_my_team(name):
    """这个名字是否在我们自己的队伍里（用于否决立绘误判）。"""
    try:
        import team_db
        return name in {t.get("name") for t in team_db.dump_team()}
    except Exception:
        return False


def _self_by_name(screen):
    """读我方 HUD 名字，命中 my_team 才返回。

    为什么我方可以用名字而敌方不行
    ------------------------------
    敌方名字是**玩家自己改的昵称**（排位里全是"漆黑的冥古龙""黑武士"），
    所以我方绝不能用名字当身份。
    但我方是**我们自己的队伍**，就那 5 只，且 HUD 上印的是种族名。
    实测 2026-11-21 实盘：读到 "冰钻布鲁斯" conf=1.0，正好在队伍里。

    唯一的风险是 OCR 把名字读成队伍外的另一只 —— 所以这里要求
    **必须命中 my_team**，否则返回 None（宁可认不出，也不认错）。
    """
    try:
        team = _my_team_hp()
        if not team:
            return None
        raw, conf = hud_ident.read_name(screen, "my",
                                       vocab=hud_ident._species_vocab())
        if not raw:
            return None
        # 名字条可能带 "60级" 之类后缀，先做包含匹配
        for name in team:
            if name and name in raw:
                return name
        # 再做词表最近邻
        if conf >= 0.85:
            for name in team:
                if raw == name:
                    return name
        return None
    except Exception:
        return None


def _my_team_hp():
    """我方队伍 {种族名: max_hp}。"""
    try:
        import sqlite3
        import paths
        db = sqlite3.connect(str(paths.DB))
        db.row_factory = sqlite3.Row
        try:
            out = {}
            for r in db.execute(
                    "SELECT sp.name, t.max_hp FROM my_team t "
                    "JOIN species sp ON sp.id=t.species_id ORDER BY t.slot"):
                if r["name"] and r["max_hp"]:
                    out[r["name"]] = int(r["max_hp"])
            return out
        finally:
            db.close()
    except Exception:
        return {}


def _self_by_hp_bar(screen):
    """读我方血条上的 "162/345"，用分母反查是哪只精灵。

    为什么这条路最可靠
    ------------------
    我方队伍是已知的 5 只，max_hp 分别是 366/345/352/417/417。
    HUD 血条直接印着绝对值，读到分母就能唯一确定（417 那两只有歧义，
    交给头像模板区分）。

    比头像模板强的地方：模板只有 4/5 只覆盖，实测匹配分 0.515，
    还把恶魔狼认成了"首领-恶魔狼"。而血条数字是游戏自己画的，改不了。

    返回种族名或 None。
    """
    try:
        import cv2
        import numpy as np
        import digitmat
        img = screen.grab_client((0, 0, 2560, 1600))
        if img is None or getattr(img, "size", 0) == 0:
            return None
        bgr = np.ascontiguousarray(img[:, :, ::-1])
        team = _my_team_hp()
        if not team:
            return None

        # 血条数字在血条中段，box 比血条本身宽一点容错
        for box in ((150, 150, 400, 46), (140, 145, 420, 56),
                    (160, 155, 380, 40)):
            try:
                r, text, conf = digitmat.read_bar_digits(bgr, box, "my")
            except Exception:
                continue
            if not text or "/" not in text:
                continue
            try:
                cur, mx = text.split("/", 1)
                mx_v = int(mx)
            except (ValueError, TypeError):
                continue
            if mx_v <= 0:
                continue
            # 精确命中
            hits = [n for n, hp in team.items() if hp == mx_v]
            if len(hits) == 1:
                return hits[0]
            # 容差 ±2（OCR 偶有 1~2 的偏差）
            hits = [n for n, hp in team.items() if abs(hp - mx_v) <= 2]
            if len(hits) == 1:
                return hits[0]
        return None
    except Exception:
        return None


def _log(msg):
    """独立模式也要有日志，否则出问题无从查起。"""
    try:
        import os
        import time as _t
        path = os.path.join(ROOT, "logs", "bot_stdout.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s [standalone] %s\n"
                     % (_t.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def _get():
    """惰性初始化 Screen + Perc（导入很重，不要放在模块级）。

    为什么必须按线程隔离（2026-11-21 实盘踩到）
    -----------------------------------------
    `winio.Screen` 内部持有 `mss.mss()` 实例，而 **mss 的对象绑定在创建它的
    线程上**。`ui_server` 是 HTTP 服务，每个请求都在不同线程里跑，
    于是第二次请求就报：

        ScreenShotError: Windows graphics function failed:
        BitBlt: 系统找不到指定的文件。

    同样的代码在独立脚本里（单线程）完全正常，所以一开始查了很久。

    修法：按线程 id 各存一份 Screen/Perc。
    """
    global _perc, _screen
    tid = threading.get_ident()
    if _perc is None or _thread_state.get("tid") != tid:
        import perceive as P
        import winio
        _screen = winio.Screen()
        _perc = P.Perc(_screen)
        _thread_state["tid"] = tid
    return _screen, _perc


def available():
    """游戏窗口在不在。不在就别硬跑（会一直返回全空）。

    ★ 环境变量 ROCO_NO_STANDALONE=1 时强制返回 False。
    ----------------------------------------------------------------
    test_ui 用独立端口起服务跑假数据，但 standalone 会去读**真实屏幕**
    （游戏开着时），把测试数据源盖掉 —— 实测 /api/state 超时。
    测试必须能明确关掉它。
    """
    if os.environ.get("ROCO_NO_STANDALONE"):
        return False
    try:
        import winio
        return winio.find_game() is not None
    except Exception:
        return False


def observe(force=False):
    """从屏幕读一帧状态，打包成和 live_state 同构的 dict。

    失败返回 None。**绝不抛异常** —— 调用方是 HTTP 请求，
    抛了会让前端整页白掉。
    """
    now = time.time()
    if not force and _cache["state"] is not None \
            and now - _cache["at"] < MIN_INTERVAL:
        return _cache["state"]

    with _lock:
        try:
            _scr, perc = _get()
            raw = perc.observe()
        except Exception as e:
            return {"error": "%s: %s" % (type(e).__name__, e),
                    "standalone": True}

    # ---- 身份识别（节流 1.5s，失败不影响主流程）
    ident = {}
    try:
        ident = identify(now=now, foe_hp_pct=raw.get("foe_pct"))
    except Exception:
        ident = {}

    my_name = ident.get("self")
    foe_name = ident.get("foe")
    foe_types = ident.get("foe_types") or []
    foe_cands = ident.get("foe_candidates") or []
    foe_src = ident.get("foe_source") or "none"
    foe_nick = ident.get("foe_nickname")

    # ---- 用身份查库，补齐血量绝对值 / 属性 / 伤害百分比
    my_types, my_max, my_abs = [], None, None
    foe_hp_range, foe_max = None, None
    try:
        import foe_hp as FH
        if my_name:
            my_types = FH.types_of(my_name) or []
            my_max = FH.max_hp(my_name)
            if my_max and raw.get("my_pct") is not None:
                my_abs = int(round(my_max * raw["my_pct"] / 100.0))
        if foe_name:
            # 属性优先用 HUD 直读的（改名改不掉，比查库更可信）
            if not foe_types:
                foe_types = FH.types_of(foe_name) or []
            foe_hp_range = FH.hp_band(foe_name)
            foe_max = FH.max_hp(foe_name)
    except Exception:
        pass

    # ---- 技能卡：有对面最大血量才能算百分比
    #
    # 分母用 `hp_band` 的**上限**（对手可能点满 + 生命性格），
    # 而不是 `max_hp`（iv=60 无性格的默认值）。实测仪式巨像：
    #     max_hp = 401        hp_band = (401, 461)
    # 拿 401 作分母会把 330 的伤害算成 82%，实际只有 72%。
    foe_hp_hi = foe_hp_range[1] if foe_hp_range else foe_max

    skills = []
    for i, s in enumerate(raw.get("skills") or [], 1):
        if not s:
            skills.append(None)
            continue
        dmg = s.get("value")

        # 合理性过滤（2026-11-21 实盘踩到）
        # ---------------------------------
        # UI 面板遮挡卡片时，`_fill_missing` 会给缺失的槽位猜一个位置，
        # 从被挡住区域的残留像素里读出一个荒唐的数字。
        # 实测：卡片 1 被左侧设置面板挡住，读成 752，而对面最大血量只有 461
        # （仪式巨像 iv60+生命性格的上限），算出 163% 的伤害。
        #
        # 判据：伤害超过对面血量上限的 1.2 倍就必然是误读。
        # 用 1.2 而不是 1.0，是给"双倍克制 + 攻buff"留余量。
        if dmg is not None and foe_hp_hi and dmg > foe_hp_hi * 1.2:
            skills.append({
                "i": i, "name": None, "element": None, "cost": None,
                "dmg": None, "pct": None, "usable": False,
                "note": "读数异常（%d > 血量上限 %d）" % (dmg, foe_hp_hi),
                "_text": s.get("text"), "_kind": s.get("kind"),
            })
            continue

        pct = None
        if dmg is not None and foe_hp_hi:
            pct = round(100.0 * dmg / foe_hp_hi, 1)
        skills.append({
            "i": i,
            "name": None,          # 读不出名字（见模块文档）
            "element": None,
            "cost": None,
            "dmg": dmg,
            "pct": pct,
            "usable": bool(s.get("lit", True)) and dmg is not None,
            "note": None,
            # 独立模式特有的原始读数，前端可以显示"卡 N: 175"
            "_text": s.get("text"),
            "_kind": s.get("kind"),
        })

    st = {
        "t": now,
        "seq": int(now * 10),
        "mode": "battle" if raw.get("battle") else "idle",
        "turn": None,
        "clock": None,
        "me": {
            "name": my_name,
            "types": my_types,
            "hp_pct": raw.get("my_pct"),
            "hp_abs": my_abs,
            "hp_max": my_max,
            "energy": raw.get("energy"),
            "energy_max": 10,
            "buffs": [],
            "trait": None,
        },
        "foe": {
            "name": foe_name,
            "types": foe_types,
            "hp_pct": raw.get("foe_pct"),
            "hp_range": foe_hp_range,
            "def_range": None,
            "nature": None,
            "nature_conf": None,
            "known_skills": [],
            # 属性过滤后的候选（识别没定下来时 UI 显示"候选：A / B"）
            "candidates": foe_cands,
            "ident_source": foe_src,
            "nickname": foe_nick,
        },
        "skills": skills,
        "matchup": None,
        "verdict": None,
        "bench": [],
        "system": {
            "fps_ms": None,
            "budget_ms": None,
            "blind": None,
            "detectors": {
                "bar": bool(raw.get("my_pct") is not None
                            and raw.get("foe_pct") is not None),
                "skillbar": bool(raw.get("bar")),
                "countdown": bool(raw.get("countdown")),
            },
            "conflicts": 0,
            "gate": None,
        },
        # ★ 前端靠这个字段区分"机器人推的"和"我自己看的"
        "standalone": True,
        # 身份识别的置信度（前端可用来显示"这个对手是猜的"）
        "ident_conf": {
            "self": round(ident.get("self_conf") or 0.0, 2),
            "foe": round(ident.get("foe_conf") or 0.0, 2),
        },
    }
    _cache["at"] = now
    _cache["state"] = st
    return st


def main(argv):
    loop = "--loop" in argv
    if not available():
        print("游戏窗口没找到（先开游戏）")
        return 2
    n = 0
    while True:
        st = observe(force=True)
        if st is None:
            print("读不到")
        else:
            sk = " ".join(
                "%d:%s%s" % (s["i"], s["_text"],
                             "" if s["usable"] else "×")
                for s in (st["skills"] or []) if s)
            print("%s battle=%s my=%s foe=%s | %s"
                  % (time.strftime("%H:%M:%S"), st["mode"] == "battle",
                     st["me"]["hp_pct"], st["foe"]["hp_pct"], sk))
        n += 1
        if not loop or (n > 0 and "--once" in argv):
            break
        time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
