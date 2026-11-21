"""test_decide.py - regression test for the heal loop.

Replays the exact states from logs/review_after_fix.md, where the bot healed
eight turns in a row at 54-99% HP while the foe stayed on 100%.
"""
import os
import paths
import sys

sys.path.insert(0, str(paths.ROOT / "src"))
sys.path.insert(0, str(paths.TOOLS))
import robot  # noqa: E402
from policy_legacy import _est_value as policy_legacy_est  # noqa: E402


def milk(slot, value=None, lit=True):
    if value is None:
        return {"index": slot, "value": None, "text": "--", "kind": "dash",
                "color": None, "score": 1.0, "glyphs": 0, "lit": lit}
    return {"index": slot, "value": value, "text": str(value), "kind": "num",
            "color": "green", "score": 1.0, "glyphs": 3, "lit": lit}


# 奶龙: 1 泥浆铠甲(-) 2 过充电(num) 3 电磁偏转(-) 4 休息回复(-)
NAILONG = [milk(1), milk(2, 354), milk(3), milk(4)]
HEAL_SLOT = 4

fails = 0


def check(name, st, ctx, want_heal):
    global fails
    action, why = robot.decide(st, ctx)
    healed = bool(action and action[0] == "skill" and action[1] == HEAL_SLOT)
    ok = healed == want_heal
    if not ok:
        fails += 1
    print("%-52s %-6s %s   (%s)" % (
        name, "HEAL" if healed else "attack",
        "OK" if ok else "**FAIL**", why))


def state(my, foe, skills=None):
    return {"my_pct": my, "foe_pct": foe, "skills": skills or NAILONG}


base = {"panic_guard": True, "took_damage": True, "heal_streak": 0}

print("--- the reported bug: 54-99%% HP must attack, not heal ---")
for hp in (54.1, 84.0, 96.7, 98.8, 64.3):
    check("hp %.1f%% hurt, streak 0" % hp, state(hp, 100.0), base, False)

print()
print("--- real emergencies should still heal ---")
check("hp 24.2% hurt, streak 0", state(24.2, 100.0), base, True)
check("hp 39.9% hurt, streak 2", state(39.9, 100.0),
      {"panic_guard": True, "took_damage": True, "heal_streak": 2}, True)

print()
print("--- guards ---")
check("hp 24% but NOT hurt last turn", state(24.0, 100.0),
      {"panic_guard": True, "took_damage": False, "heal_streak": 0}, False)
check("hp 24% hurt but streak already 3", state(24.0, 100.0),
      {"panic_guard": True, "took_damage": True, "heal_streak": 3}, False)
check("hp 24% hurt but heal card dimmed",
      state(24.0, 100.0, [milk(1), milk(2, 354), milk(3), milk(4, None, False)]),
      base, False)

print()
print("--- attacks pick the biggest number ---")
a, why = robot.decide(state(90.0, 100.0), base)
print("hp 90%% -> %s (%s)" % (a, why))

print()
print("--- a resisted big number beats a stray tiny one ---")
# Live regression, 10:21:50: slot 2 dimmed by energy, hand was 120(red) + 1.
# The old "prefer non-red" filter kept the 1 and switched pets.
mixed = [milk(1), milk(2, 150, False), milk(3, 120), milk(4, 1)]
mixed[2]["color"] = "red"
mixed[3]["color"] = "white"
a, why = robot.decide(state(100.0, 83.3, mixed),
                      {"panic_guard": True, "took_damage": False,
                       "heal_streak": 0, "switch_slot": 2,
                       "switch_streak": 99})
ok = a == ("skill", 3)
if not ok:
    fails += 1
print("%-52s %-6s %s   (%s)" % (
    "120(red) + 1(white) -> attack slot 3",
    "attack" if ok else str(a), "OK" if ok else "**FAIL**", why))

print()
print("--- a partially-read big number must not lose to a small one ---")
# Live regression, 10:27:16: slot 1 read "1?5" (one glyph failed to match, so
# value was None) and the bot used slot 3 "55" for three turns instead.
_partial = [
    {"index": 1, "value": None, "text": "1?5", "kind": "num", "color": "green",
     "score": 0.4, "glyphs": 3, "lit": True},
    {"index": 2, "value": None, "text": "--", "kind": "dash", "color": None,
     "score": 1.0, "glyphs": 0, "lit": True},
    {"index": 3, "value": 55, "text": "55", "kind": "num", "color": "white",
     "score": 1.0, "glyphs": 2, "lit": True},
    {"index": 4, "value": 5, "text": "05", "kind": "num", "color": "white",
     "score": 1.0, "glyphs": 2, "lit": True},
]
a, why = robot.decide(state(100.0, 100.0, _partial), base)
ok = a == ("skill", 1)
if not ok:
    fails += 1
print("%-52s %-6s %s   (%s)" % (
    "\"1?5\" (3 glyphs) vs \"55\" -> attack slot 1",
    "attack %s" % (a[1] if a else None), "OK" if ok else "**FAIL**", why))

print()
print("--- 但推算值虚高的全未知卡不能压过确定的大数字 ---")
# 反向回归：只扣 15 分不等于"读不全优先"。
# "???" 只有位数信息，_est_value 推算 100；旁边是确定的 300，
# 必须选 300。否则会退回"赌一张完全读不出的卡"的老毛病。
_vague = [
    {"index": 1, "value": None, "text": "???", "kind": "num", "color": "green",
     "score": 0.3, "glyphs": 3, "lit": True},
    {"index": 2, "value": 300, "text": "300", "kind": "num", "color": "white",
     "score": 1.0, "glyphs": 3, "lit": True},
    {"index": 3, "value": None, "text": "--", "kind": "dash", "color": None,
     "score": 1.0, "glyphs": 0, "lit": True},
    {"index": 4, "value": None, "text": "--", "kind": "dash", "color": None,
     "score": 1.0, "glyphs": 0, "lit": True},
]
a, why = robot.decide(state(100.0, 100.0, _vague), base)
ok = a == ("skill", 2)
if not ok:
    fails += 1
print("%-52s %-6s %s   (%s)" % (
    "\"???\" vs 300 -> attack slot 2",
    "attack %s" % (a[1] if a else None), "OK" if ok else "**FAIL**", why))

print()
print("--- 4 位以上的读数是切分错误，不能当数字用 ---")
# 实测 turns.jsonl 里 25 条 4 位读数全部 >999（"1062"/"1294"/"??????"），
# 真实值都是 3 位数。不拦住的话 "1062" 会被当成必定秒杀，
# "??????" 更会被按位数估成 10**5 = 100000。
_garbage = [
    {"index": 1, "value": 1062, "text": "1062", "kind": "num", "color": "green",
     "score": 1.0, "glyphs": 4, "lit": True},
    {"index": 2, "value": 300, "text": "300", "kind": "num", "color": "white",
     "score": 1.0, "glyphs": 3, "lit": True},
    {"index": 3, "value": None, "text": "??????", "kind": "num",
     "color": "white", "score": 0.0, "glyphs": 6, "lit": True},
    {"index": 4, "value": None, "text": "--", "kind": "dash", "color": None,
     "score": 1.0, "glyphs": 0, "lit": True},
]
a, why = robot.decide(state(100.0, 100.0, _garbage), base)
ok = a == ("skill", 2)
if not ok:
    fails += 1
print("%-52s %-6s %s   (%s)" % (
    "\"1062\" 和 \"??????\" 都不能参与 -> attack slot 2",
    "attack %s" % (a[1] if a else None), "OK" if ok else "**FAIL**", why))
print("%-52s %-6s %s" % (
    "_est_value 对 4 位读数返回 None",
    str(policy_legacy_est(_garbage[0])),
    "OK" if policy_legacy_est(_garbage[0]) is None
    and policy_legacy_est(_garbage[2]) is None else "**FAIL**"))
if not (policy_legacy_est(_garbage[0]) is None
        and policy_legacy_est(_garbage[2]) is None):
    fails += 1

print()
print("--- out of energy: two charges, then rotate to a full-energy pet ---")
dark = [milk(1, None, False), milk(2, None, False),
        milk(3, None, False), milk(4, None, False)]
a, why = robot.decide(state(90.0, 60.0, dark),
                      dict(base, switch_slot=3, switch_streak=5,
                           charge_streak=0))
ok = a == ("energy",)
if not ok:
    fails += 1
print("%-52s %-6s %s   (%s)" % (
    "no energy, charge_streak 0 -> F", str(a), "OK" if ok else "**FAIL**", why))

a, why = robot.decide(state(90.0, 60.0, dark),
                      dict(base, switch_slot=3, switch_streak=5,
                           charge_streak=2))
ok = a == ("switch", 3)
if not ok:
    fails += 1
print("%-52s %-6s %s   (%s)" % (
    "no energy, 2 interruptions -> rotate", str(a),
    "OK" if ok else "**FAIL**", why))

print()
print("--- 读不出数字时按队伍配置挑最强（不再硬编码 slot 2）---")
# 实测背景：6.2% 的决策是"卡面亮着但数字读不出"，旧兜底硬编码 slot2，
# 注释称"slot 2 是全队主攻"——但卡瓦重 slot2 power=60 是最弱的（slot4=105）。
_cards = lambda: [{"index": i, "lit": True, "kind": "num", "value": None}
                  for i in range(1, 5)]
_best = robot.config_best_slot(_cards(), {1: 80, 2: 60, 3: 90, 4: 105},
                               {1: 3, 2: 1, 3: 3, 4: 4})
ok = _best == 4
if not ok:
    fails += 1
print("%-52s %-6s %s" % (
    "卡瓦重 80/60/90/105 -> slot 4（旧代码给 2）",
    str(_best), "OK" if ok else "**FAIL**"))

_best = robot.config_best_slot(_cards(), {1: 0, 2: 140, 3: 80, 4: 100},
                               {1: 0, 2: 5, 3: 2, 4: 3})
ok = _best == 2
if not ok:
    fails += 1
print("%-52s %-6s %s" % (
    "音速犬 0/140/80/100 -> slot 2（借用不可用）",
    str(_best), "OK" if ok else "**FAIL**"))

_dark = _cards()
_dark[3]["lit"] = False
_best = robot.config_best_slot(_dark, {1: 80, 2: 60, 3: 90, 4: 105},
                               {1: 3, 2: 1, 3: 3, 4: 4})
ok = _best == 3
if not ok:
    fails += 1
print("%-52s %-6s %s" % (
    "slot4 暗掉 -> 退回 slot 3",
    str(_best), "OK" if ok else "**FAIL**"))

for x in _dark:
    x["lit"] = False
ok = robot.config_best_slot(_dark, {1: 80}, {}) is None
if not ok:
    fails += 1
print("%-52s %-6s %s" % (
    "全暗 -> None（调用方走聚能）",
    "OK" if ok else "**FAIL**", ""))

print()
if fails:
    print("%d FAILURE(S)" % fails)
    sys.exit(1)
print("all decide() cases pass")
