"""damage.py - 洛克王国世界伤害计算引擎

移植自 roco-claude（基于「洛克计算器 2.0」公式），数据直接读它的原始 json。

公式：
  能力值 = round(round(1.1 × (种族值 + 3×个体)) + 10) × 性格 + 50
  HP     = round(round(1.7 × (种族值 + 3×个体)) + 70) × 性格 + 100
  有效威力 = 基础威力 × 本系(1.25) × 属性克制 × (1 + 攻击buff层数×0.1)
  伤害 = INT(ROUND(攻 × 连击 × 有效威力 × 减伤 × 37/41) / 防)
"""

import io
import json
import sys


DATA = r"D:\GameData\洛克王国世界\roco-claude\data\raw"

NATURE_MULT = {"加": 1.2, "无": 1.0, "减": 0.9}

_pokemon = None
_skills = None
_types = None


def _load():
    global _pokemon, _skills, _types
    if _pokemon is None:
        _pokemon = {}
        for d in json.load(open(DATA + r"\pokemon.json", encoding="utf-8")):
            _pokemon[d.get("display_name")] = d
        _skills = {s["name"]: s for s in json.load(open(DATA + r"\skills.json", encoding="utf-8"))}
        _types = json.load(open(DATA + r"\type_chart.json", encoding="utf-8"))
    return _pokemon, _skills, _types


def pokemon(name):
    p, _, _ = _load()
    return p.get(name)


def skill(name):
    _, s, _ = _load()
    return s.get(name)


def calc_stat(base, iv=10, nature="无"):
    inner = round(1.1 * (base + 3 * iv)) + 10
    return round(inner * NATURE_MULT[nature]) + 50


def calc_hp(base, iv=10, nature="无"):
    inner = round(1.7 * (base + 3 * iv)) + 70
    return round(inner * NATURE_MULT[nature]) + 100


def type_effectiveness(atk_type, def_attrs):
    """PVP 双属性加法规则（用 multiplier_matrix）。"""
    _, _, chart = _load()
    mm = chart.get("multiplier_matrix", {})
    mults = [mm.get(atk_type, {}).get(a, 1.0) for a in def_attrs]
    if any(m == 0 for m in mults):
        return 0.0
    if len(mults) == 1:
        return mults[0]
    eff = sum(1 for m in mults if m > 1)
    res = sum(1 for m in mults if m < 1)
    if eff == 2:
        return 3.0
    if res == 2:
        return 0.25
    if eff == 1 and res == 1:
        return 1.0
    if eff == 1:
        return 2.0
    if res == 1:
        return 0.5
    return 1.0


def shown_power(skill_name, atk_pet, def_pet, atk_buff_layers=0):
    """返回该技能对目标的有效威力（含本系/克制/攻击buff）。"""
    sk = skill(skill_name)
    if not sk:
        return None
    base = int(sk["power"]) if sk.get("power") and str(sk["power"]).isdigit() else 0
    attr = sk.get("attribute")
    stab = 1.25 if atk_pet and attr in (atk_pet.get("attributes_list") or []) else 1.0
    tmul = type_effectiveness(attr, def_pet.get("attributes_list") or []) if def_pet else 1.0
    buff = 1 + atk_buff_layers * 0.1
    return {
        "name": skill_name,
        "base": base,
        "attribute": attr,
        "category": sk.get("category"),
        "cost": int(sk["energy_consumption"]) if sk.get("energy_consumption") else 0,
        "stab": stab,
        "type_mult": tmul,
        "shown": base * stab * tmul * buff,
        "resisted": tmul < 1.0,
        "strong": tmul > 1.0,
    }


def damage(atk_stat, def_stat, eff_power, hit_count=1, mitigation=1.0):
    return int(round(atk_stat * hit_count * eff_power * mitigation * 37 / 41) / def_stat)


def estimate(skill_name, atk_pet_name, def_pet_name, atk_buff_layers=0,
             atk_nature="无", def_nature="无", level_iv=10):
    """完整估算：返回 {skill, shown, dmg_min, dmg_max, pct}"""
    ap = pokemon(atk_pet_name)
    dp = pokemon(def_pet_name)
    if not ap or not dp:
        return None
    sp = shown_power(skill_name, ap, dp, atk_buff_layers)
    if not sp:
        return None
    cat = sp["category"] or ""
    is_physical = ("物攻" in cat) or ("物理" in cat) or ("phys" in cat.lower()) or ("attack" == cat.lower())
    if is_physical:
        atk = calc_stat(int(ap["stats"]["attack"]), level_iv, atk_nature)
        dfn = calc_stat(int(dp["stats"]["defense"]), level_iv, def_nature)
    else:
        atk = calc_stat(int(ap["stats"]["special_attack"]), level_iv, atk_nature)
        dfn = calc_stat(int(dp["stats"]["special_defense"]), level_iv, def_nature)
    d = damage(atk, dfn, sp["shown"])
    hp = calc_hp(int(dp["stats"]["hp"]), level_iv, def_nature)
    return {
        "skill": skill_name,
        "base": sp["base"],
        "cost": sp["cost"],
        "shown": round(sp["shown"], 1),
        "type_mult": sp["type_mult"],
        "resisted": sp["resisted"],
        "strong": sp["strong"],
        "dmg": d,
        "defender_hp": hp,
        "pct": round(100.0 * d / hp, 1) if hp else 0,
    }


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("usage: python damage.py <技能> <我方精灵> <敌方精灵> [buff层数]")
        sys.exit(2)
    r = estimate(sys.argv[1], sys.argv[2], sys.argv[3],
                 int(sys.argv[4]) if len(sys.argv) > 4 else 0)
    print(json.dumps(r, ensure_ascii=False, indent=2))

