"""pvp_engine.py - data-driven matchup maths for the PvP bot.

The bot currently picks skills by looking at the number printed on the card.
That number already includes type effectiveness and our own buffs, so it answers
"how hard do I hit this turn" - but it says nothing about the other half of the
decision: how many turns we have left.

Everything needed for that half is already in the local dataset (ported from the
S5 build of roco-cal / roco-claude):

    pokemon.json    stats, attributes_list, ability,
                    defense_profile {takes_3x_from, takes_2x_from,
                                     takes_half_from, takes_one_third_from},
                    skills {moves, jinengshi, xuemai}
    skills.json     per-skill power / attribute / energy cost / category
    type_chart.json 18-type matrix (verified against roco-cal's _CHART)

Formulas cross-checked against the in-game numbers at 2026-11-18:
    隼鳞 (龙, 140) from 寂灭骨龙 vs 水灵  ->  card showed 175, we compute 175
    隼鳞 vs 公平鸽                        ->  card showed 175, panel showed base 140

usage:
    python pvp_engine.py 水灵 寂灭骨龙      # matchup report
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import damage as D


def pet(name):
    """精灵数据。**DB 优先**，json 兜底。

    为什么改
    --------
    原来只查 `damage.pokemon()`（roco-claude 的 pokemon.json，499 只）。
    实测 2026-11-21 04:05：银月狼王、冥古龙 在这份 json 里没有，
    于是 best_damage_from 返回 0、worst_incoming 返回 None，
    换人评分变成 "dmg 0 taken 0"（等于没数据）。

    现在优先查我们自己的库（1242 只，已用 roco-cal 清洗过），
    json 只在 DB 查不到时兜底（它带 defense_profile 这类附加字段）。

    形态变体回退
    ------------
    对面常是形态变体（如 "黑化加尔（黑化的样子）"），DB 里没有这条，
    但基础形态"加尔"有。属性通常一致，所以回退到基础形态的属性
    （标注 from="db-base" 便于上层区分）。
    """
    import re as _re
    import sqlite3
    from portrait_db import DB_PATH

    def _lookup(nm):
        try:
            db = sqlite3.connect(DB_PATH)
            db.row_factory = sqlite3.Row
            r = db.execute(
                "SELECT * FROM species WHERE name=? "
                "ORDER BY implemented DESC, total DESC LIMIT 1",
                (nm,)).fetchone()
            db.close()
            return r
        except Exception:
            return None

    r = _lookup(name)
    src = "db"
    if r is None:
        # 形态变体：去掉括号部分再查一次
        base = _re.sub(r"[（(].*?[)）]", "", name or "").strip()
        if base and base != name:
            r = _lookup(base)
            src = "db-base"
    if r is not None:
        types = []
        try:
            types = json.loads(r["type_names"] or "[]")
        except ValueError:
            pass
        return {
            "display_name": r["name"],
            "attributes_list": types,
            "stats": {
                "hp": r["hp"], "attack": r["atk"],
                "special_attack": r["spa"], "defense": r["def"],
                "special_defense": r["spd"], "speed": r["spe"],
            },
            "from": src,
        }
    return D.pokemon(name)


def taken_multiplier(def_name, atk_type):
    """Damage multiplier `def_name` takes from `atk_type` (18-type rules).

    优先用 json 的 defense_profile（现成字段）；DB 里没有这个字段，
    所以查不到时用 statmodel.type_effect 现算（结果等价，已验证 324/324）。
    """
    p = pet(def_name)
    if p:
        prof = p.get("defense_profile") or {}
        if prof:
            if atk_type in (prof.get("takes_3x_from") or []):
                return 3.0
            if atk_type in (prof.get("takes_2x_from") or []):
                return 2.0
            if atk_type in (prof.get("takes_one_third_from") or []):
                return 1.0 / 3.0
            if atk_type in (prof.get("takes_half_from") or []):
                return 0.5
            return 1.0
    # DB 兜底：现算
    try:
        import statmodel as S
        types = (p or {}).get("attributes_list") or []
        if types:
            return S.type_effect(atk_type, types)
    except Exception:
        pass
    return None


_CACHE = {}


def move_pool(name):
    """Every skill the pet could legally have.

    优先用我们库的 species_skills（1242 只、已清洗），
    再退回 json 的 moves/jinengshi/xuemai（带血统技能，DB 里没有）。
    形态变体会回退到基础形态（属性/技能通常一致）。
    """
    import re as _re
    out = []
    import sqlite3
    from portrait_db import DB_PATH

    def _skills_of(nm):
        try:
            db = sqlite3.connect(DB_PATH)
            db.row_factory = sqlite3.Row
            r = db.execute(
                "SELECT id FROM species WHERE name=? "
                "ORDER BY implemented DESC, total DESC LIMIT 1",
                (nm,)).fetchone()
            got = []
            if r:
                for row in db.execute(
                        "SELECT DISTINCT s.name FROM species_skills ss "
                        "JOIN skills s ON s.id=ss.skill_id "
                        "WHERE ss.species_id=? AND s.power>0", (r["id"],)):
                    if row[0]:
                        got.append(row[0])
            db.close()
            return got
        except Exception:
            return []

    out = _skills_of(name)
    if not out:
        base = _re.sub(r"[（(].*?[)）]", "", name or "").strip()
        if base and base != name:
            out = _skills_of(base)
    # json 兜底：补 DB 没有的血统/基因技能
    p = D.pokemon(name)
    if p:
        raw = p.get("skills") or {}
        for key in ("moves", "jinengshi", "xuemai"):
            for s in raw.get(key) or []:
                if s not in out:
                    out.append(s)
    return out


def weighted_incoming(foe_name, my_name, blend=0.75, top=6):
    """概率加权承伤：学习到的技能分布 × 单技能伤害，再对最坏值取底。

    为什么不是纯期望（2026-11-22 实测，见 docs/PLAN_POLICY_INCOMING.md）
    ----------------------------------------------------------------
    对 12 组真实对位统计（opponent_actions.jsonl 的实际掉血）：

        worst_incoming 高估实伤 1.5~3 倍（对面整池取 max，池里大半技能
        实际不带）—— 机器人过度保守，不敢站桩输出；
        纯期望会**漏掉致命技能**（叮叮卯 exp/obs=0.29，学习分布还没
        收敛时漏掉它带的大招 = 站在必死范围里）。

    所以取两者混合：max(期望, blend × 最坏)。地板保住"最坏情况也扛得住"
    的安全语义；blend 系数让常见对位不再被 3 倍高估锁死。

    返回 (dmg, source)，source ∈ {"weighted", "worst", "assumed"}。
    """
    rows = worst_incoming(foe_name, my_name, top=1)
    worst = rows[0]["dmg"] if rows else 0
    if worst <= 0:
        return 0, "worst"
    try:
        import foe_moves
        exp = 0.0
        for name, p, _pw, _tid in foe_moves.distribution(foe_name, top=top):
            d = expected_incoming(foe_name, my_name, skill_name=name)
            if d:
                exp += p * d
        exp = int(exp)
    except Exception:
        exp = 0
    if exp <= 0:
        return worst, "worst"
    return int(max(exp, round(worst * blend))), "weighted"


def worst_incoming(foe_name, my_name, top=5):
    """Biggest hits the opponent *could* land on us, as (skill, dmg, pct_of_my_hp).

    The opponent's actual four moves are unknown until revealed, so this is the
    worst case over the whole legal pool. Conservative on purpose: it is used to
    decide whether standing still is survivable, and over-estimating that is the
    safe direction.

    为什么不用 D.estimate
    -------------------
    `damage.estimate` 只查 roco-claude 的 pokemon.json（499 只），
    实测银月狼王/冥古龙/形态变体都查不到 -> 返回 None ->
    worst_incoming 变成空列表 -> 换人评分显示 "taken 0"。
    这里直接用 statmodel + DB 数据算，覆盖面 1242 只。
    """
    key = (foe_name, my_name, top)
    if key in _CACHE:
        return _CACHE[key]
    me = pet(my_name)
    foe = pet(foe_name)
    if not me or not foe:
        return []
    import statmodel as S
    hp = S.calc_real_stat(int(me["stats"]["hp"]), S.IV_PVP, 1.0, is_hp=True)
    my_def = int(me["stats"]["defense"])
    foe_atk = int(foe["stats"]["attack"])
    foe_types = foe.get("attributes_list") or []
    my_types = me.get("attributes_list") or []
    rows = []
    for skill in move_pool(foe_name):
        sk = D.skill(skill)
        if not sk:
            continue
        power = 0
        try:
            power = int(sk.get("power") or 0)
        except (TypeError, ValueError):
            continue
        if power <= 0:
            continue
        atk_type = sk.get("attribute")
        stab = 1.25 if atk_type in foe_types else 1.0
        try:
            dmg = S.damage(
                S.calc_real_stat(foe_atk, S.IV_PVP, 1.0),
                S.calc_real_stat(my_def, S.IV_PVP, 1.0),
                power,
                type_eff=S.type_effect(atk_type, my_types),
                stab=stab)
        except Exception:
            continue
        rows.append({"skill": skill, "dmg": int(dmg),
                     "pct": round(100.0 * dmg / hp, 1) if hp else 0.0,
                     "attribute": atk_type})
    rows.sort(key=lambda r: r["dmg"], reverse=True)
    out = rows[:top]
    _CACHE[key] = out
    return out


def expected_incoming(foe_name, my_name, skill_name=None):
    """Damage for one specific skill; falls back to the worst case."""
    r = D.estimate(skill_name, foe_name, my_name) if skill_name else None
    if r and r.get("dmg"):
        return int(r["dmg"])
    rows = worst_incoming(foe_name, my_name)
    return rows[0]["dmg"] if rows else 0


def best_damage_from(name, foe_name):
    """Hardest hit `name` can land on `foe_name`, over its whole legal pool.

    This is the "how much can I actually do to it" half of the switch score.
    It went missing for a while: robot._best_damage called it, the attribute
    did not exist, and the caller's `except Exception: continue` swallowed the
    AttributeError - so every switch silently degraded to "first living pet"
    (six live occurrences in bot_stdout.log on 2026-11-20).

    Uses statmodel (the one verified model) with the DB species stats, and
    returns 0 rather than raising when either side is unknown.
    """
    import statmodel as S
    import damage_v2 as DMG
    import sqlite3
    from portrait_db import DB_PATH

    me = _db_species(name)
    foe = _db_species(foe_name)
    if not me or not foe:
        return 0

    my_types = json.loads(me.get("type_names") or "[]")
    foe_types = json.loads(foe.get("type_names") or "[]")

    my_atk = _num(me.get("atk"))
    foe_def = _num(foe.get("def"))
    if my_atk <= 0 or foe_def <= 0:
        return 0

    best = 0
    for skill_name in move_pool(name):
        sk = D.skill(skill_name)
        if not sk:
            continue
        power = _num(sk.get("power"))
        if power <= 0:
            continue
        atk_type = sk.get("attribute")
        stab = 1.25 if atk_type in my_types else 1.0
        try:
            dmg = DMG.damage(
                S.calc_real_stat(my_atk, S.IV_PVP, 1.0),
                S.calc_real_stat(foe_def, S.IV_PVP, 1.0),
                power,
                type_eff=S.type_effect(atk_type, foe_types),
                stab=stab,
            )
        except Exception:
            continue
        best = max(best, int(dmg))
    return best


def _db_species(name):
    """species row by name, preferring implemented/complete entries."""
    import sqlite3
    from portrait_db import DB_PATH
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        r = db.execute(
            "SELECT * FROM species WHERE name=? "
            "ORDER BY implemented DESC, total DESC LIMIT 1", (name,)
        ).fetchone()
        return dict(r) if r else None
    finally:
        db.close()


def _num(v):
    """DB 里的种族值可能是 '--' 占位（9178 导入的未实装宠）。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def ko_turns(target_hp, per_turn):
    """How many turns of `per_turn` damage are needed. None when it cannot."""
    if not per_turn or per_turn <= 0:
        return None
    return max(1, -(-int(target_hp) // int(per_turn)))


def my_max_hp(name):
    p = pet(name)
    if not p:
        return None
    return D.calc_hp(int(p["stats"]["hp"]))


# Max HP read off the in-game HUD ("cur / max"). The PvP mode does not match
# calc_hp() for every pet: 音速犬 and 冰钻布鲁斯 land exactly on the formula,
# 寂灭骨龙 and 巨鼓象 come out ~15% higher, so the formula cannot be trusted for
# turn-to-die arithmetic. Observed 2026-11-18.
CALIBRATED_HP = {
    "音速犬": 366,
    "冰钻布鲁斯": 345,
    "寂灭骨龙": 490,
    "巨鼓象": 510,
}


def max_hp(name):
    """Max HP to use in TTD/TKO maths: measured value first, formula second."""
    if name in CALIBRATED_HP:
        return CALIBRATED_HP[name]
    return my_max_hp(name)


def foe_max_hp(name):
    return my_max_hp(name)


def matchup(foe_name, my_name, my_pct=100.0, foe_pct=100.0):
    """One-shot report for a single pair."""
    me = pet(my_name)
    you = pet(foe_name)
    if not me or not you:
        return None
    my_hp = max_hp(my_name)
    foe_hp = max_hp(foe_name)
    inc = worst_incoming(foe_name, my_name)
    return {
        "me": my_name, "me_types": me.get("attributes_list"),
        "me_hp": my_hp, "me_hp_now": round(my_hp * my_pct / 100.0),
        "me_ability": me.get("ability"),
        "foe": foe_name, "foe_types": you.get("attributes_list"),
        "foe_hp": foe_hp, "foe_hp_now": round(foe_hp * foe_pct / 100.0),
        "foe_ability": you.get("ability"),
        "foe_defense_profile": you.get("defense_profile"),
        "incoming": inc,
    }


def _selftest():
    ok = True

    # The in-game card printed 175 for this exact pair (2026-11-18 10:59).
    r = D.estimate("隼鳞", "寂灭骨龙", "水灵")
    want = 175
    line = "  shown power 隼鳞 vs 水灵: %s (card showed %s)" % (
        r["shown"] if r else None, want)
    hit = bool(r) and abs(r["shown"] - want) < 1e-6
    ok &= hit
    print(line + ("  OK" if hit else "  **FAIL**"))

    m = matchup("水灵", "寂灭骨龙")
    for row in m["incoming"]:
        print("  incoming %-8s %4d dmg  %.1f%% of my HP  (%s)" % (
            row["skill"], row["dmg"], row["pct"], row["attribute"]))
    if not m["incoming"]:
        print("  **FAIL** no incoming moves resolved")
        ok = False

    print("  ko_turns(434, 196) = %s (expect 3)" % ko_turns(434, 196))
    ok &= ko_turns(434, 196) == 3
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        rep = matchup(sys.argv[1], sys.argv[2])
        print(json.dumps(rep, ensure_ascii=False, indent=2) if rep else "unknown pet")
    else:
        sys.exit(_selftest())
