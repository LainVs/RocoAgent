# -*- coding: utf-8 -*-
"""live_state.py —— 把机器人每帧的状态导出给前端。

为什么单独一个模块
------------------
机器人主循环已经很重（BLE + 抓屏 + OCR + 决策），不能在循环里加
业务计算。这里只做「把已有数据重新打包成前端契约」这一件事，
所有数值都来自调用方已经算好的结果，本模块不算伤害。

写入策略
--------
原子替换：先写 .tmp 再 os.replace。前端随时可能在读，
直接覆盖会出现半截 JSON。

    publish(state)   # 主循环每帧调一次
    clear()          # 退出时清掉，避免前端显示过期数据

契约见 docs/FRONTEND.md §三。
"""
from __future__ import annotations

import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "logs")
PATH = os.path.join(LOGS, "live_state.json")

_seq = 0
_last_write = 0.0
MIN_INTERVAL = 0.08      # 最快 12.5Hz，比感知快但不会刷爆磁盘


def _num(v):
    """None 保持 None —— 未知就是未知，绝不填 0。"""
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _pct(v):
    v = _num(v)
    if v is None:
        return None
    return max(0.0, min(100.0, v))


def build(*, turn=None, clock=None, mode=None,
          me=None, foe=None, skills=None,
          matchup=None, verdict=None, system=None, bench=None):
    """组装一份符合前端契约的状态字典。

    每个参数都是可选的；缺的字段前端会显示"—"。
    """
    global _seq
    _seq += 1
    return {
        "t": time.time(),
        "seq": _seq,
        "mode": mode or "idle",
        "turn": turn,
        "clock": clock,
        "me": me or {},
        "foe": foe or {},
        "skills": skills or [],
        "matchup": matchup,
        "verdict": verdict,
        "system": system or {},
        # 替补：我只知道"见过名字"的那几只，未知的槽位直接省略。
        # 打架时"还剩几只"和当前血量同等重要。
        "bench": bench or [],
    }


def bench_block(entries):
    """替补列表。

    entries: [{"name": str, "hp_pct": float|None, "alive": bool}, ...]
    未知槽位不要塞进来 —— 前端会按 len(bench) 判断还剩几只。
    """
    out = []
    for e in entries or []:
        if not e:
            continue
        name = e.get("name")
        if not name:
            continue
        out.append({
            "name": name,
            "hp_pct": _pct(e.get("hp_pct")),
            "alive": bool(e.get("alive", True)),
        })
    return out


def me_block(name=None, types=None, hp_pct=None, hp_abs=None, hp_max=None,
             energy=None, energy_max=10, buffs=None, trait=None):
    return {
        "name": name,
        "types": list(types or []),
        "hp_pct": _pct(hp_pct),
        "hp_abs": None if hp_abs is None else int(hp_abs),
        "hp_max": None if hp_max is None else int(hp_max),
        "energy": None if energy is None else int(energy),
        "energy_max": int(energy_max or 10),
        "buffs": list(buffs or []),
        "trait": trait,
    }


def foe_block(name=None, types=None, hp_pct=None,
              hp_range=None, def_range=None,
              nature=None, nature_conf=None, known_skills=None,
              hp_dist=None, def_dist=None, nature_dist=None,
              sample_n=None):
    return {
        "name": name,
        "types": list(types or []),
        "hp_pct": _pct(hp_pct),
        # 区间必须给 tuple/list；前端画成 "349~419"
        "hp_range": list(hp_range) if hp_range else None,
        "def_range": list(def_range) if def_range else None,
        "nature": nature,
        "nature_conf": _num(nature_conf),
        "known_skills": list(known_skills or []),
        # ---- PvP 大数据（pvp_meta）：概率分布而不是单点 ----
        # hp_dist/def_dist: [[值, 概率], ...] 按值升序
        # nature_dist:      [[性格, 概率], ...] 按概率降序
        # sample_n:         这个物种的天梯样本数（0 = 没数据，前端不要编造）
        "hp_dist": [[int(v), round(float(p), 4)] for v, p in (hp_dist or [])],
        "def_dist": [[int(v), round(float(p), 4)] for v, p in (def_dist or [])],
        "nature_dist": [[str(k), round(float(p), 4)] for k, p in (nature_dist or [])],
        "sample_n": None if sample_n is None else int(sample_n),
    }


def skill_block(i, name=None, element=None, cost=None,
                dmg=None, pct=None, usable=True, note=None,
                pct_lo=None, pct_mid=None, pct_hi=None, pct_kill=None):
    return {
        "i": i,
        "name": name,
        "element": element,
        "cost": None if cost is None else int(cost),
        "dmg": None if dmg is None else int(dmg),
        "pct": _pct(pct),
        "usable": bool(usable),
        "note": note,
        # 伤害占比的概率分布（对面 HP 不确定 -> 占比不确定）
        "pct_lo": _pct(pct_lo),
        "pct_mid": _pct(pct_mid),
        "pct_hi": _pct(pct_hi),
        # 这一下 >= 对面当前血量的概率（斩杀率）
        "pct_kill": _num(pct_kill),
    }


def system_block(fps_ms=None, detectors=None, conflicts=0, gate=None,
                 budget_ms=None, blind=None):
    """系统指标。

    fps_ms    最近一次 Perc.observe() 的耗时（含技能表纠正）
    budget_ms 主循环每轮的 sleep 间隔（config.POLL_S）。它不是"上限"，
              而是**下限** —— 一轮的实际周期 ≈ fps_ms + budget_ms。
              前端用这两个数算等效轮询频率，比一个孤零零的毫秒数有用。
    blind     本局「没看到技能栏就出招」的次数。这是真正该报警的指标：
              `bar=False` 在非我方回合是正常的，只有盲打才等于丢决策。
    """
    return {
        "fps_ms": _num(fps_ms),
        "budget_ms": _num(budget_ms),
        "blind": None if blind is None else int(blind),
        "detectors": dict(detectors or {}),
        "conflicts": int(conflicts or 0),
        "gate": gate,
    }


def publish(state, force=False):
    """原子写。返回是否真的写了。"""
    global _last_write
    now = time.time()
    if not force and (now - _last_write) < MIN_INTERVAL:
        return False
    tmp = PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, PATH)
        _last_write = now
        return True
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def clear():
    try:
        os.remove(PATH)
    except OSError:
        pass
