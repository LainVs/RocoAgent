# -*- coding: utf-8 -*-
"""旧决策 decide()：治疗 / 聚能 / 最大伤害

从 robot.py 拆出（第 1308-1525 行），纯搬移，逻辑未改。
"""
from __future__ import annotations

import random
import time

# 这两个常量原本在 robot.py 的 1299-1305 行（decide 段之前），
# 拆分时被切到了 config 段，但只有本模块用，所以放回这里。
HEAL_HINTS = ("休息回复", "回复", "治疗")

# 中文技能名 OCR 不可靠，但每只精灵的"非伤害槽位"（dash）模式是唯一的。
# 队伍表：奶龙 1 泥浆铠甲(-) 2 过充(num) 3 电磁偏转(-) 4 休息回复(-) -> 治疗在 4 号槽
HEAL_BY_DASH = {(1, 3, 4): 4}


def _heal_slot(st):
    dash = tuple((i + 1) for i, s in enumerate(st["skills"])
                 if s and s["kind"] == "dash")
    return HEAL_BY_DASH.get(dash)


HEAL_HP = 40          # only heal below this
HEAL_MAX_STREAK = 3   # never heal more than this many turns in a row
RETREAT_HP = 25       # below this, and behind on HP, consider switching out
SWITCH_DMG = 40        # below this, a fresh pet is usually better


_MY_COSTS = {"at": 0.0, "name": None, "costs": {}}


def my_card_costs(my_name, force=False):
    """我方这只精灵四个技能槽的耗能 {slot: cost}，缓存 60 秒。

    卡片顺序 = 装备的 4 个技能顺序，所以耗能不需要读 HUD 上的 ★N 徽标，
    直接从 team_db.moves + skills.cost 查 —— 零额外识别成本。
    """
    import time as _t
    if (not force and _MY_COSTS["name"] == my_name
            and _t.time() - _MY_COSTS["at"] < 60.0):
        return _MY_COSTS["costs"]
    out = {}
    try:
        import sqlite3
        from portrait_db import DB_PATH
        import team_db
        team = {t.get("name"): t for t in team_db.dump_team() if t.get("name")}
        ent = team.get(my_name) or {}
        moves = ent.get("moves") or []
        if moves:
            db = sqlite3.connect(DB_PATH)
            for i, sid in enumerate(moves[:4]):
                r = db.execute("SELECT cost FROM skills WHERE id=?", (int(sid),)).fetchone()
                if r and r[0] is not None:
                    out[i + 1] = int(r[0])
            db.close()
    except Exception:
        pass
    _MY_COSTS.update({"at": _t.time(), "name": my_name, "costs": out})
    return out


def _est_value(s):
    """Best guess at the number on a card, even when one glyph failed to match.

    digitread reports "1?5" for a three-digit number whose middle glyph did not
    clear its correlation threshold. `value` is None in that case and the old
    code dropped the skill entirely, so a real "1?5" lost to a neighbouring
    "55" and the bot spent three turns on the weaker skill while the opponent
    walked through it.

    The digit count is still reliable, so use it as a floor: a three-glyph read
    is at least 100, a two-glyph read at least 10.

    "1?5" is a 3-glyph read, but the digits we *did* read say much more than
    "at least 100": the number is 105-195. Use the known digits and assume the
    unknown ones sit at the end, which keeps the estimate in the right decade.
    The old floor of 100 let a "23?" tie with a real 100 and win the sort, so
    the bot pressed a card it could not read over one it could.

    位数上限（2026-11-21）
    ---------------------
    卡面伤害在本作里最多 3 位（实测最大 ~700，精灵血量都在 500 上下）。
    4 位以上一定是**切分错误**：多切进了一个字形。实测 turns.jsonl 里
    25 条 4 位以上的读数（"1062"/"1294"/"??????"）**全部** >999，
    对应的真实值都是 3 位数（106 / 129）。
    不拦住的话后果很严重：
        "1062"  -> value=1062 -> 引擎认为这一下必定秒杀（+0.30 KO 权重）
        "??????" -> 按位数推算 10**5 = 100000 -> 同上
    """
    txt = s.get("text") or ""
    if len(txt) > 3:
        return None
    if s.get("value") is not None:
        return float(s["value"])
    digits = "".join(ch for ch in txt if ch.isdigit())
    n = len(txt) or int(s.get("glyphs") or 0)
    if s.get("kind") == "num" and 2 <= n <= 3:
        if digits:
            return float(int(digits) * 10 ** max(0, n - len(digits)))
        return float(10 ** (n - 1))
    return None


def config_best_slot(cards, powers, costs=None, lam=3.0):
    """读不出卡面数字时，按**队伍配置**挑最该打的那张。返回 1-based 槽位或 None。

    为什么需要
    ----------
    数字读不出来时（实测占 6.2% 的决策），robot 的兜底是**硬编码 slot 2**，
    注释写的是"slot 2 是全队主攻"。但队伍配置里根本不是这样：

        音速犬      power 0 / 140 / 80  / 100    slot2 确实最强
        银月狼王    power 120 / 130 / 120 / 80   slot2 最强
        卡瓦重      power 80 / 60  / 90  / 105   slot2 **最弱**
        冰钻布鲁斯  power 90 / 70  / 75  / 60    slot2 次弱

    盲目打 slot2，卡瓦重每回合少打 45 点、冰钻布鲁斯少打 20 点。

    cards  : perceive 的四个槽位字典（用 index / lit）
    powers : {1-based 槽位: 技能 power}（来自 perceive._skill_meta）
    costs  : {1-based 槽位: 耗能}（来自 my_card_costs）
    lam    : 每点耗能折算多少伤害（和 decide() 里一致）
    """
    best, best_s = None, None
    for s in cards or []:
        if not s or not s.get("lit", True):
            continue
        try:
            i = int(s.get("index"))
        except (TypeError, ValueError):
            continue
        p = (powers or {}).get(i)
        if not p or p <= 0:
            continue
        c = (costs or {}).get(i, 0) or 0
        score = float(p) - lam * c
        if best_s is None or score > best_s:
            best, best_s = i, score
    return best


def _charge_or_switch(ctx):
    """Out of energy: gather it with F - unless that keeps getting interrupted.

    聚能 is not a free action. The opponent can spend their turn interrupting it,
    and then we take damage and gain nothing; the live log shows seven F presses
    in a row while the foe sat on 67% HP. A pet that is switched in enters with a
    full energy bar, so after two fruitless charges rotating is strictly better
    than pressing F a third time.
    """
    if (ctx.get("charge_streak", 0) >= 2
            and ctx.get("switch_slot")
            and ctx.get("switch_streak", 99) >= 3):
        return ("switch", ctx["switch_slot"]), \
            "no energy, charges keep getting interrupted -> rotate"
    return ("energy",), "nothing affordable, gather energy"


def decide(st, ctx):
    """Pick an action. Returns ((action, why) or (None, reason)).

    action: ("skill", n) | ("energy",)

    Order:
      1. Heal - but only on a real emergency (see below).
      2. Biggest *affordable* (lit) damaging skill that is not resisted.
      3. Nothing affordable -> gather energy with F.
      4. Nothing readable at all -> (None, ...) so the caller can reuse its
         last working plan instead of wasting the turn.

    The heal gate is deliberately strict. Earlier it was "hp <= 35%", but the
    actual damage loop came from elsewhere: a heal got cached as the repeatable
    plan and then replayed every unreadable turn, producing eight consecutive
    heals at 54-99% HP with the foe on 100% - two minutes of no output.
    So: heal only below 40%, only if we were just damaged, and never more than
    HEAL_MAX_STREAK turns running.
    """
    skills = [s for s in st["skills"] if s]
    if not skills:
        return None, "no skill read"
    # 把对面最大血量挂到每张卡上，供下面的合理性过滤用（见 damaged 循环）。
    foe_max = ctx.get("foe_max")
    foe_now = ctx.get("foe_now")
    if foe_max or foe_now:
        for s in skills:
            if foe_max:
                s.setdefault("_foe_max", foe_max)
            if foe_now:
                s.setdefault("_foe_now", foe_now)
    usable = [s for s in skills if s.get("lit", True)]

    slot = _heal_slot(st)
    my = st["my_pct"]
    can_heal = (
        ctx.get("panic_guard", True)
        and slot
        and 1 <= slot <= len(st["skills"])
        and st["skills"][slot - 1]
        and st["skills"][slot - 1].get("lit", True)
        and my is not None
        and my < HEAL_HP
        and ctx.get("took_damage", True)
        and ctx.get("heal_streak", 0) < HEAL_MAX_STREAK
    )
    if can_heal:
        return ("skill", slot), "heal at %.0f%% (hurt last turn, streak %d)" % (
            my, ctx.get("heal_streak", 0))

    # About to lose this pet for nothing: hand the turn to a fresh one rather
    # than feeding the current one to the opponent. This is separate from the
    # "all my numbers are weak" rule below - that one looks at offence, this one
    # looks at the scoreboard.
    # 残血自动撤退已关闭（用户要求）：它会切进一只更残的精灵占掉回合。
    # 下一回合又触发撤退，实际上等于送死。需要换人时交给对位评分。?
    # 强制换人处理，不再由血量单独触发。?
    bench = ctx.get("switch_slot")
    foe_pct = st["foe_pct"]
    if False:
        return ("switch", bench), "retreat disabled"

    damaged = []
    for s in usable:
        if s["kind"] != "num":
            continue
        v = _est_value(s)
        if v is None:
            continue
        # 合理性上限（三道，逐级放宽）。
        #
        # 为什么要三道：实测卡面读出过 660 / 700 / 1000+，都被当成"必杀"
        # 选中，整局白打。而 `foe_max` 在对面不在数据库时是 None
        # （实测「幽冥龙」就是），相对判据会直接短路。
        #
        # 1) 绝对上限 900：PvP 正常精灵最大血量约 600，最强单体技能
        #    power 900，打完减伤后不可能超过 900。（PvE 的 BOSS 有
        #    2 万血，但那些不会出现在排位里。）
        # 2) 相对上限：超过对面最大血量 1.2 倍。
        # 3) 用"对面当前血量"兜底：卡面伤害是打完减伤的实际值，
        #    一击打掉超过对面**当前**血量 1.5 倍必然是误读
        #    （实测 660 打一个 92% 血、总量约 400 的对手）。
        if v > 900:
            continue
        if s.get("_foe_max") and v > s["_foe_max"] * 1.2:
            continue
        _foe_now = s.get("_foe_now")
        if _foe_now and v > _foe_now * 1.5:
            continue
        damaged.append((s, v))
    # 读不全的卡要分两种，不能一刀切：
    #
    #   1?5 / 23?   位数和部分数字都读到了，_est_value 能把值定到很窄的
    #               区间（1?5 推算 150，实际 175）。这种**必须参与比较**，
    #               否则真实的 3 位数会输给旁边的两位数。
    #               实测回归（10:27）：slot1 读 "1?5"，机器人连用 3 回合
    #               slot3 "55" 去打一只残血对手。
    #
    #   ?? / ????   一个数字都没读到，只有位数。推算值纯属猜的，不参与
    #               —— 以前它按过 "dmg ??"，同样是在赌。
    #
    # 旧代码是"只要有一张读得清，就只从读得清的里选"，把所有读不全的
    # 一起排除了，等于把 _est_value 专门处理的那种情况又绕回去。
    _has_digit = [p for p in damaged
                  if p[0].get("value") is not None
                  or any(ch.isdigit() for ch in (p[0].get("text") or ""))]
    if _has_digit:
        damaged = _has_digit

    # 对面已经倒下?%）：这回合别再聚，?等数字，随便打一张就行。?
    # 实测出现?foe=0.0% 还在?F 的情况，纯属浪费回合。
    foe_dead = st.get("foe_pct") is not None and st["foe_pct"] <= 0.5

    if not damaged:
        if usable:
            if foe_dead:
                pick = next((s for s in usable if s.get("kind") == "num"),
                            usable[0])
                return ("skill", pick["index"]), "foe is dead - attack anyway"
            # Cards are lit but no damage number could be read: that is a failed
            # read, not an empty hand. Tell the caller so it can reuse its last
            # working plan instead of burning the turn on 聚能.
            return None, "lit cards but unreadable"
        return _charge_or_switch(ctx)

    # The number on the card is already the damage *after* type effectiveness
    # (the game prints 140 -> 70 against a resisting target), so the biggest
    # number is the right pick and colour only breaks ties.
    #
    # Preferring "not red" first was a real bug: with slot 2 dimmed by energy
    # the hand was 120(red) + a stray "1" misread off slot 4, the filter threw
    # away the 120, kept the 1, and concluded "everything is weak" - so the bot
    # switched pets for nothing.
    # 完整读出的数字永远优先于"推算?。实?09:44:25 出现过：
    # 卡面 [152, 300, --, ???]，它按了?4 张（读不全、推算值虚高）。?
    # 把确定的 300 丢在一边。只要有一张读得清，就不该赌读不清的那张。?
    # 净收益 = 伤害 ?λ×耗能? 费打 168 不该2 费打 160。?
    # 耗能，?我方装备?4 个技，?查（顺序=卡槽顺序），不需要读 ★N 徽标。?
    costs = ctx.get("card_costs") or {}
    lam = 3.0

    def _net(p):
        c = costs.get(p[0]["index"], 0) or 0
        return p[1] - lam * c

    def _pen(p):
        """读不全的卡扣一点分，但**不是绝对优先**。

        踩过的坑（两次，方向相反）：
          · 最早完全不扣 -> 一张 "???（推算 100）" 可能压过确定的 300。
          · 后来改成"读得清的一律优先" -> 真实的 3 位数 `1?5`（推算 150）
            永远输给旁边两位数 `55`。实测 10:27 连用 3 回合弱技能。
        折中：扣 15。推算误差实测在 ±40 量级，扣偏小的一端，
        保证 `1?5`(150-15=135) 仍然赢 `55`，而虚高的 `???`(100-15=85)
        压不过确定的 300。
        """
        return 0.0 if p[0].get("value") is not None else 15.0

    def _rank(p):
        return _net(p) - _pen(p)

    if not damaged:
        # 所有卡面读数都不可信（例如都超过对面血量 1.2 倍）：
        # 不要在这里崩，也不要瞎打 —— 交给调用方走"聚能"兜底。
        return None, "all card damages implausible"

    pool = sorted(damaged,
                  key=lambda p: (_rank(p),
                                 p[0].get("value") is not None,
                                 p[0]["color"] != "red"), reverse=True)
    # ?3% 以内视为等价，随机选一个：同一套阵容每次都走同一线路的话。?
    # 对面摸透一次就能场场赢我们（实测确实如此）。?
    top_v = _rank(pool[0])
    near = [p for p in pool if _rank(p) >= top_v * 0.97][:3]
    best, best_v = random.choice(near) if len(near) > 1 else pool[0]

    # Everything on hand is weak: hand the turn to a fresh pet instead of
    # chipping away. The caller supplies the slot of a living bench pet.
    # Require at least two readable numbers - a single tiny value is usually a
    # misread, not a weak hand.
    slot = ctx.get("switch_slot")
    if (slot and ctx.get("switch_streak", 99) >= 3
            and len(damaged) >= 2 and best_v < SWITCH_DMG):
        return ("switch", slot), "all skills weak (best %s), switch to slot %d" % (
            best["text"], slot)

    return ("skill", best["index"]), "best affordable dmg %s (%s)" % (
        best["text"], best["color"])


def _match_name(st, cands):
    """Best-effort: first dash skill whose index maps to a heal name."""
    names = st.get("names") or []
    for s in cands:
        if s["index"] - 1 < len(names) and any(h in names[s["index"] - 1] for h in HEAL_HINTS):
            return s["index"]
    return None

