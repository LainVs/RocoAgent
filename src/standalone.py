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

    # 组装成前端契约（字段名和 live_state.build 一致）
    skills = []
    for i, s in enumerate(raw.get("skills") or [], 1):
        if not s:
            skills.append(None)
            continue
        skills.append({
            "i": i,
            "name": None,          # 读不出名字（见模块文档）
            "element": None,
            "cost": None,
            "dmg": s.get("value"),
            "pct": None,           # 没有对面血量算不出百分比
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
            "name": None,
            "types": [],
            "hp_pct": raw.get("my_pct"),
            "hp_abs": None,
            "hp_max": None,
            "energy": raw.get("energy"),
            "energy_max": 10,
            "buffs": [],
            "trait": None,
        },
        "foe": {
            "name": None,
            "types": [],
            "hp_pct": raw.get("foe_pct"),
            "hp_range": None,
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
