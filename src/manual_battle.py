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
        # S8 血脉 / S13 槽位锁 / S16 条件规则（UI 展示）
        "bloodline": getattr(p, "bloodline", None),
        "allowed_slots": sorted(getattr(p, "_allowed_slots", None) or []),
        "cond_mods": getattr(p, "_cond_mods", None) or None,
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
        self.forced_switch = False   # 我方力竭，等待玩家手动选换人
        self.pick_starter = False    # 开局等待玩家选首发

    # ---------------------------------------------------------- 开局
    def new(self, team_a, cfg_a=None, opponent_mode="lineups",
            team_b=None, cfg_b=None, policy_b="smart", seed=0,
            weather="none", marks=None, marks_b=None, dex=None,
            skip_starter=False, team_magic_a="愿力强化"):
        """skip_starter=True 时不开局选首发（批量测试用）。

        team_magic_a：我方共鸣魔法（S9）——"愿力强化"/"光合治愈"/
        "闪焰爆发"/"节流术"，None = 不带。
        """
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
            # S8/S9：血脉缺省回退主系（秘药改血脉前最常见状态）；
            # 共鸣魔法 team_magic_a 缺省愿力强化
            ca = list(cfg_a or [])
            while len(ca) < len(ta):
                ca.append(None)
            for i, n in enumerate(ta):
                c = ca[i] if isinstance(ca[i], dict) else {}
                if not c.get("bloodline"):
                    sp = dex.species(n)
                    if sp and sp.get("types"):
                        c["bloodline"] = sp["types"][0]
                ca[i] = c
            self.bt = SIM.Battle(dex, ta, tb, cfg_a=ca, cfg_b=cb,
                                 seed=self.seed,
                                 magic_kind_a=team_magic_a)
            self.pol_b = POLICIES.get(policy_b, SIM.SmartPolicy)()
            # 天气 / 印记（2026-11-22）：开局时设定，整局有效
            self.bt.set_weather(str(weather or "none"))
            for field, stacks in (marks or {}).items():
                self.bt.set_marks("a", field, stacks)
            for field, stacks in (marks_b or {}).items():
                self.bt.set_marks("b", field, stacks)
            self.log = []
            self.actions = []
            # S7.5：开局选首发——pick_starter=True 时不自动 begin_turn，
            # 等玩家 act({"kind":"starter","i":N}) 后才进入第一回合
            self.pick_starter = not skip_starter
            self._say("对局开始：我方 %s vs %s（%s，对手策略 %s%s）"
                      % ("/".join(ta), "/".join(tb),
                         self.meta.get("opponent") or "固定", policy_b,
                         ("，天气 " + str(weather))
                         if weather and weather != "none" else ""))
            if not skip_starter:
                if not self.bt.begin_turn():
                    self._record()
            return self.state()

    # ---------------------------------------------------------- 操作
    def act(self, action):
        """执行我方一个动作，敌方由策略自动出招。

        ★ 2026-11-22 用户纠正：我方精灵倒下时**不要自动换人**，要让玩家
        手动选换谁上场（包括开局选首发）。实现：
          · step 后若我方出战精灵倒下 → 本回合正常结束（敌方照常行动），
            下回合 begin_turn 前置一个 forced_switch 状态，等玩家选。
          · 玩家没选之前，任何 skill/energy 动作被拒绝。
        """
        with self.lock:
            if self.bt is None:
                return {"error": "还没有开始对局"}
            if self.bt.done:
                return {"error": "对局已经结束，开新局", "state": self.state()}
            me, foe = self.bt.me, self.bt.foe
            if me is None or foe is None:
                return {"error": "状态异常（没有出战精灵）"}

            kind = (action or {}).get("kind") or "skill"

            # 开局选首发（S7.5）
            if getattr(self, "pick_starter", False):
                if kind != "starter":
                    return {"error": "请先选择首发精灵",
                            "state": self.state()}
                payload = action.get("i")
                try:
                    payload = int(payload)
                except (TypeError, ValueError):
                    return {"error": "首发下标非法"}
                if not (0 <= payload < len(self.bt.a)):
                    return {"error": "首发下标越界"}
                self.pick_starter = False
                self.bt.ia = payload
                self.bt.a[self.bt.ia].energy = SIM.ENERGY_CAP
                self._say("T%d 我方首发 %s" % (self.bt.turn, self.bt.me.name))
                if not self.bt.begin_turn():
                    self._record()
                return self.state(events=["我方首发 " + self.bt.me.name])
                if kind != "switch":
                    return {"error": "我方精灵已力竭，请先选择上场的精灵",
                            "state": self.state(forced_switch=True)}
                payload = action.get("i")
                try:
                    payload = int(payload)
                except (TypeError, ValueError):
                    return {"error": "换人下标非法"}
                bench = self.bt.bench("a")
                if not (0 <= payload < len(bench)):
                    return {"error": "换人下标越界"}
                self.forced_switch = False
                # 走 step("switch")：clear_buffs（重置入场标记）、
                # switch_in 印记（棘刺/降灵/暗涌）等全部走同一代码路径
                evs = self.bt.step("switch", payload, "energy", None)
                for e in evs:
                    self._say("T%d %s" % (self.bt.turn, e))
                self.actions.append({"turn": self.bt.turn, "kind": "switch",
                                     "to": self.bt.me.name})
                return self.state(events=evs)

            if kind == "skill":
                try:
                    payload = int(action.get("i"))
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
            elif kind == "magic":
                # S8/S9 共鸣魔法：payload = 本回合技能下标
                # （愿力强化固定第一格，见 Battle.step；增益魔法用 payload 技能出手）
                spell = self.bt.team_magic.get("a")
                if not spell:
                    return {"error": "我方没有携带共鸣魔法"}
                st = self.bt.magic_state["a"]
                if st["used"] >= 2:
                    return {"error": "%s每场只能用 2 次" % spell}
                if st["cd"] > 0:
                    return {"error": "%s冷却中（还剩 %d 回合）"
                                     % (spell, st["cd"])}
                if spell == "愿力强化":
                    if not getattr(self.bt.me, "bloodline", None):
                        return {"error": "我方精灵没有配置血脉（bloodline）"}
                    payload = 0
                else:
                    # 增益魔法：payload = 本回合照常出的技能
                    try:
                        payload = int(action.get("i"))
                    except (TypeError, ValueError):
                        return {"error": "技能下标非法"}
                    if not (0 <= payload < len(me.moves)):
                        return {"error": "技能下标越界"}
                self.actions.append({"turn": self.bt.turn, "kind": "magic",
                                     "spell": spell})
                self.actions.append({"turn": self.bt.turn, "kind": "magic",
                                     "spell": spell})
            elif kind == "evolve":
                # S10 进化之力：首领血脉精灵战斗中进化，每场 1 次。
                # payload = 本回合照常出的技能下标（不占行动）。
                if self.bt.evolution_used.get("a"):
                    return {"error": "进化之力每场只能使用 1 次"}
                _lf_path = os.path.join(os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__))),
                    "data", "leader_forms.json")
                try:
                    _lf = json.load(io.open(_lf_path, encoding="utf-8"))
                except Exception:
                    _lf = {}
                _forms = (_lf.get(me.name) or {}).get("forms") or {}
                _target = None
                bl = getattr(me, "bloodline", None)
                for fname, f in _forms.items():
                    if f.get("bloodline") == "首领":
                        if bl == "首领":
                            _target = fname
                    else:
                        _elem = fname[1] if fname.startswith("圣") else None
                        if _elem and (bl == _elem or
                                      _elem in (me.types or [])):
                            _target = fname
                if not _target:
                    return {"error": "%s 不满足首领化条件（血脉 %s）"
                                     % (me.name, bl or "未配置")}
                try:
                    payload = int(action.get("i"))
                except (TypeError, ValueError):
                    payload = 0
                self.actions.append({"turn": self.bt.turn, "kind": "evolve",
                                     "target": _target})
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
                # 我方出战精灵若已倒下 → 等玩家手动选换人（不自动换）
                if not self.bt.me.alive:
                    self.forced_switch = True
                    self._say("T%d 我方 %s 力竭，请选择上场的精灵"
                              % (self.bt.turn, self.bt.me.name))
                else:
                    self.bt.begin_turn()
            if self.bt.done:
                self._finish_log()
            return self.state(events=ev)

    # ---------------------------------------------------------- 查询
    def state(self, events=None, forced_switch=None):
        with self.lock:
            if self.bt is None:
                return {"error": "还没有开始对局"}
            bt = self.bt
            if forced_switch is None:
                forced_switch = getattr(self, "forced_switch", False)
            return {
                "turn": bt.turn,
                "max_turn": SIM.MAX_TURNS,
                "energy_gain": SIM.ENERGY_GAIN,
                "forced_switch": bool(forced_switch),
                "pick_starter": getattr(self, "pick_starter", False),
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
                # S8/S9 共鸣魔法状态：UI 按钮用
                "magic": {
                    "name": bt.team_magic.get("a") or "",
                    "used": bt.magic_state["a"]["used"],
                    "max_uses": 2,
                    "cd": bt.magic_state["a"]["cd"],
                    "usable": (bool(bt.team_magic.get("a"))
                               and bt.magic_state["a"]["used"] < 2
                               and bt.magic_state["a"]["cd"] == 0
                               and getattr(bt.me, "bloodline", None)
                               != "首领"),
                },
                # S10 进化之力状态：UI 按钮用（每场 1 次）
                "evolution": {
                    "used": bool(bt.evolution_used.get("a")),
                    "usable": not bt.evolution_used.get("a")
                    and getattr(bt.me, "bloodline", None) == "首领",
                },
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
