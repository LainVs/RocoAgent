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

_lock = threading.Lock()
_perc = None
_screen = None
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

    为什么不能每帧跑：立绘 ONNX 一次 80~120ms，10Hz 会占满 CPU。
    而且身份在**一回合内不会变**，没必要重复认。
    """
    now = now or time.time()
    if now - _ident["at"] < ID_INTERVAL:
        return _ident
    _ident["at"] = now

    try:
        import portrait_onnx as PO
        if not PO.available():
            return _ident
    except Exception:
        return _ident

    # 换人检测：血量突然变高说明对面换了只满血的上场
    prev_hp = _ident.get("foe_hp_at_id")
    changed = (foe_hp_pct is not None and prev_hp is not None
               and foe_hp_pct > prev_hp + 25)

    try:
        _scr, _p = _get()
        if changed:
            _ident["foe"] = None
            _ident["foe_conf"] = 0.0
        if _ident["foe"] is None:
            res = PO.predict_region(_scr, "foe", topk=1)
            if res:
                name, conf = res[0]
                # 置信度门槛：定标实测 conf>=0.90 时准确率 93.2%，
                # 0.70~0.90 只有 55.6%（等于抛硬币），所以取 0.90。
                if conf >= 0.90:
                    _ident["foe"] = name
                    _ident["foe_conf"] = float(conf)
                else:
                    _ident["foe_conf"] = float(conf)
        if _ident["self"] is None:
            res = PO.predict_region(_scr, "self", topk=1)
            if res:
                name, conf = res[0]
                if conf >= 0.90:
                    _ident["self"] = name
                    _ident["self_conf"] = float(conf)
        _ident["foe_hp_at_id"] = foe_hp_pct
    except Exception:
        pass
    return _ident


def _get():
    """惰性初始化 Screen + Perc（导入很重，不要放在模块级）。"""
    global _perc, _screen
    if _perc is None:
        import perceive as P
        import winio
        _screen = winio.Screen()
        _perc = P.Perc(_screen)
    return _screen, _perc


def available():
    """游戏窗口在不在。不在就别硬跑（会一直返回全空）。"""
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

    # ---- 用身份查库，补齐血量绝对值 / 属性 / 伤害百分比
    my_types, my_max, my_abs = [], None, None
    foe_types, foe_hp_range, foe_max = [], None, None
    try:
        import foe_hp as FH
        if my_name:
            my_types = FH.types_of(my_name) or []
            my_max = FH.max_hp(my_name)
            if my_max and raw.get("my_pct") is not None:
                my_abs = int(round(my_max * raw["my_pct"] / 100.0))
        if foe_name:
            foe_types = FH.types_of(foe_name) or []
            foe_hp_range = FH.hp_band(foe_name)
            foe_max = FH.max_hp(foe_name)
    except Exception:
        pass

    # ---- 技能卡：有对面最大血量才能算百分比
    skills = []
    for i, s in enumerate(raw.get("skills") or [], 1):
        if not s:
            skills.append(None)
            continue
        dmg = s.get("value")
        pct = None
        if dmg is not None and foe_max:
            pct = round(100.0 * dmg / foe_max, 1)
        skills.append({
            "i": i,
            "name": None,          # 读不出名字（见模块文档）
            "element": None,
            "cost": None,
            "dmg": dmg,
            "pct": pct,
            "usable": bool(s.get("lit", True)),
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
