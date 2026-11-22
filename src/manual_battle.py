# -*- coding: utf-8 -*-
"""manual_battle.py —— 手操对战会话（UI 里「自己上场打」的那个窗口）。

为什么单独一层
--------------
战斗循环必须和批量模拟**完全一致**，否则同一个局面手操和批量给出不同
结果，手操就白看了。所以这里只做三件事，不重写任何战斗规则：

    · 持有 `simulator.Battle`（单步状态机，run_match 用的也是它）
    · 把状态翻译成前端能渲染的 JSON
    · 把打完的每局追加到 logs/sim_manual.jsonl（可回溯分析）

一致性由 `src/test_sim_engine.py` 的黄金基线保证（15 个用例逐字段比对）。

用法（ui_server 调用）：
    s = Session();  s.new(team_a=[...], cfg_a=[...], opponent_mode="lineups")
    s.act({"kind": "skill", "i": 0})     -> {"state": ..., "events": [...]}
    s.state()
"""
from __future__ import annotations

import io
import json
import os
import threading
import time

import lineup_lib
import simulator as SIM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORD = os.path.join(ROOT, "logs", "sim_manual.jsonl")

POLICIES = {"smart": SIM.SmartPolicy, "greedy": SIM.GreedyPolicy,
            "buff": SIM.BuffPolicy}

MAX_LOG = 400          # 只保留最近 N 条事件给前端


def _pet_json(p, with_moves=False, foe=None):
    if p is None:
        return None
    out = {
        "name": p.name,
        "hp": int(p.hp),
        "max_hp": int(p.max_hp),
        "pct": round(100.0 * p.hp / p.max_hp, 1) if p.max_hp else 0,
        "energy": int(p.energy),
        "types": list(p.types or []),
        "trait": p.trait,
        "nature": getattr(p, "nature", None),
        "invest": list(getattr(p, "invest", []) or []),
        "alive": bool(p.alive),
        "atk_buff": round(getattr(p, "atk_buff", 0.0), 3),
        "def_buff": round(getattr(p, "def_buff", 0.0), 3),
        "tmp_atk": round(getattr(p, "trait_tmp_atk", 0.0), 3),
        "tmp_def": round(getattr(p, "trait_tmp_def", 0.0), 3),
        "taken_mult": round(getattr(p, "taken_mult", 1.0), 3),
    }
    if with_moves:
        out["moves"] = []
        for m in p.moves:
            # dmg = **字面伤害**：游戏技能卡上显示的那个数字。
            # 公式和结算完全同源（calc_damage），所以卡面值 = 真打出去的伤害
            # （本作 PVP 无伤害浮动、卡面不含暴击）。
            dmg = 0
            if foe is not None and int(m.get("power") or 0) > 0:
                try:
                    dmg = int(SIM.calc_damage(p, foe, m))
                except Exception:
                    dmg = 0
            out["moves"].append({
                "name": m["name"],
                "power": int(m["power"]),
                "cost": int(m["cost"]),
                "type": m.get("type"),
                "usable": int(m["cost"]) <= p.energy,
                "dmg": dmg,
            })
    return out


class Session:
    """一个手操对局。ui_server 里只用一个全局实例 + 锁。"""

    def __init__(self):
        self.lock = threading.RLock()
        self.bt = None
        self.log = []
        self.meta = {}
        self.actions = []
        self.seed = 0

    # ---------------------------------------------------------- 开局
    def new(self, team_a, cfg_a=None, opponent_mode="lineups",
            team_b=None, cfg_b=None, policy_b="smart", seed=0,
            weather="none", marks=None, marks_b=None, dex=None):
        with self.lock:
            dex = dex or SIM.Dex()
            ta = [str(x) for x in (team_a or [])][:6]
            if not ta:
                return {"error": "我方队伍不能为空"}
            title = ""
            if opponent_mode == "lineups":
                # 从 158 套真实阵容里按 seed 抽一套（同 seed 可复现）
                pool, _st = lineup_lib.load(dex)
                if not pool:
                    return {"error": "真实阵容库为空"}
                import random
                idx = random.Random(seed).randrange(len(pool))
                lu = pool[idx]
                tb, cb = lu["team"], lu["cfg"]
                title = lu["title"]
                self.meta = {"mode": "lineups", "opponent": title,
                             "author": lu.get("author") or "",
                             "source": lu.get("source") or ""}
            else:
                tb = [str(x) for x in (team_b or [])][:6]
                cb = cfg_b
                if not tb:
                    return {"error": "固定对手模式需要对方队伍"}
                self.meta = {"mode": "fixed", "opponent": "/".join(tb),
                             "author": "", "source": ""}

            self.seed = int(seed or 0)
            self.bt = SIM.Battle(dex, ta, tb, cfg_a=cfg_a, cfg_b=cb,
                                 seed=self.seed)
            self.pol_b = POLICIES.get(policy_b, SIM.SmartPolicy)()
            # 天气 / 印记（2026-11-22）：开局时设定，整局有效
            self.bt.set_weather(str(weather or "none"))
            for field, stacks in (marks or {}).items():
                self.bt.set_marks("a", field, stacks)
            for field, stacks in (marks_b or {}).items():
                self.bt.set_marks("b", field, stacks)
            self.log = []
            self.actions = []
            self._say("对局开始：我方 %s vs %s（%s，对手策略 %s%s）"
                      % ("/".join(ta), "/".join(tb),
                         self.meta.get("opponent") or "固定", policy_b,
                         ("，天气 " + str(weather))
                         if weather and weather != "none" else ""))
            if not self.bt.begin_turn():
                self._record()
            return self.state()

    # ---------------------------------------------------------- 操作
    def act(self, action):
        """执行我方一个动作，敌方由策略自动出招。"""
        with self.lock:
            if self.bt is None:
                return {"error": "还没有开始对局"}
            if self.bt.done:
                return {"error": "对局已经结束，开新局", "state": self.state()}
            me, foe = self.bt.me, self.bt.foe
            if me is None or foe is None:
                return {"error": "状态异常（没有出战精灵）"}

            kind = (action or {}).get("kind") or "skill"
            payload = action.get("i")
            if kind == "skill":
                try:
                    payload = int(payload)
                except (TypeError, ValueError):
                    return {"error": "技能下标非法"}
                if not (0 <= payload < len(me.moves)):
                    return {"error": "技能下标越界"}
                mv = me.moves[payload]
                if mv["cost"] > me.energy:
                    return {"error": "能量不够放 %s（需要 %d，当前 %d）"
                                     % (mv["name"], mv["cost"], me.energy)}
                self.actions.append({"turn": self.bt.turn, "kind": "skill",
                                     "skill": mv["name"]})
            elif kind == "switch":
                bench = self.bt.bench("a")
                try:
                    payload = int(payload)
                except (TypeError, ValueError):
                    return {"error": "换人下标非法"}
                if not (0 <= payload < len(bench)):
                    return {"error": "换人下标越界"}
                self.actions.append({"turn": self.bt.turn, "kind": "switch",
                                     "to": self.bt.a[bench[payload]].name})
            elif kind == "energy":
                payload = None
                self.actions.append({"turn": self.bt.turn, "kind": "energy"})
            elif kind == "buff":
                payload = None
                self.actions.append({"turn": self.bt.turn, "kind": "buff"})
            else:
                return {"error": "未知动作 %s" % kind}

            # 敌方出招（和批量模拟同一套策略接口）
            try:
                self.pol_b._team = self.bt.b
            except Exception:
                pass
            kb, pb = self.pol_b.choose(foe, me, self.bt.bench("b"),
                                       self.bt.turn)

            # 日志按回合分组：先打一条回合头，再逐条挂事件。
            # 用户要求"显示当前回合、对面放什么技能、我方放什么技能"，
            # 平铺一长串很难看出回合边界。
            self._say("第 %d 回合" % self.bt.turn)
            ev = self.bt.step(kind, payload, kb, pb)
            for e in ev:
                self._say("  " + e)
            if not self.bt.done:
                self.bt.begin_turn()
            if self.bt.done:
                self._finish_log()
            return self.state(events=ev)

    # ---------------------------------------------------------- 查询
    def state(self, events=None):
        with self.lock:
            if self.bt is None:
                return {"error": "还没有开始对局"}
            bt = self.bt
            return {
                "turn": bt.turn,
                "max_turn": SIM.MAX_TURNS,
                "energy_gain": SIM.ENERGY_GAIN,
                "done": bt.done,
                "result": ("win" if bt.done == "a"
                           else "lose" if bt.done == "b" else None),
                "me": _pet_json(bt.me, with_moves=True, foe=bt.foe),
                "foe": _pet_json(bt.foe),
                # 可换的只有「活着且不是当前出战」的精灵，顺序必须和
                # Battle.bench("a") 完全一致 —— act 里的 switch 下标
                # 就是往这个列表里取的。之前把阵亡的也算进来，
                # 导致「换人下标越界」。
                "bench": [dict(_pet_json(bt.a[i]), slot=i + 1)
                          for i in bt.bench("a")],
                "my_team": [{"slot": i + 1, "name": p.name,
                             "hp": int(p.hp), "max_hp": int(p.max_hp),
                             "alive": bool(p.alive)}
                            for i, p in enumerate(bt.a)],
                "foe_team": [{"slot": i + 1, "name": p.name,
                              "hp": int(p.hp), "max_hp": int(p.max_hp),
                              "alive": bool(p.alive)}
                             for i, p in enumerate(bt.b)],
                "meta": self.meta,
                "events": events or [],
                "log": self.log[-MAX_LOG:],
                "fx": (self.bt.mark_state() if self.bt else {}),
            }

    # ---------------------------------------------------------- 记录
    def _say(self, text):
        self.log.append(text)

    def _finish_log(self):
        r = self.bt.result()
        self._say("对局结束：%s（%d 回合）"
                  % ("我方胜利" if r[0] == "a" else "我方失败", r[1]))
        self._record()

    def _record(self):
        """追加到 logs/sim_manual.jsonl，供 /api/sim/manual/history 分析。"""
        if self.bt is None or self.bt.done is None:
            return
        w, t, ha, hb = self.bt.result()
        row = {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "seed": self.seed,
            "result": "win" if w == "a" else "lose",
            "turns": t,
            "hp_a": ha,
            "hp_b": hb,
            "meta": self.meta,
            "my_team": [p.name for p in self.bt.a],
            "foe_team": [p.name for p in self.bt.b],
            "actions": self.actions,
            "log": self.log[-80:],
        }
        try:
            os.makedirs(os.path.dirname(RECORD), exist_ok=True)
            with io.open(RECORD, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass


# -------------------------------------------------------------- 分析
def history(limit=40):
    """手操对局的汇总：总场次/胜率/常见对手/常用技能。"""
    if not os.path.exists(RECORD):
        return {"games": 0, "rows": [], "summary": _summary([])}
    rows = []
    with io.open(RECORD, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return {"games": len(rows), "rows": rows[-limit:],
            "summary": _summary(rows)}


def _summary(rows):
    from collections import Counter
    wins = sum(1 for r in rows if r.get("result") == "win")
    opp = Counter()
    skills = Counter()
    turns = []
    for r in rows:
        opp[(r.get("meta") or {}).get("opponent") or "?"] += 1
        turns.append(r.get("turns") or 0)
        for a in r.get("actions") or []:
            if a.get("kind") == "skill" and a.get("skill"):
                skills[a["skill"]] += 1
    return {
        "games": len(rows),
        "wins": wins,
        "losses": len(rows) - wins,
        "win_rate": round(100.0 * wins / len(rows), 1) if rows else 0,
        "avg_turns": round(sum(turns) / len(turns), 1) if turns else 0,
        "opponents": opp.most_common(10),
        "skills": skills.most_common(12),
    }
