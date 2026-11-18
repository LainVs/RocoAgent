"""test_decide.py - regression test for the heal loop.

Replays the exact states from logs/review_after_fix.md, where the bot healed
eight turns in a row at 54-99% HP while the foe stayed on 100%.
"""
import sys

sys.path.insert(0, r"D:\Projects\RocoAgent\src")
sys.path.insert(0, r"D:\Tools")
import robot  # noqa: E402


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
if fails:
    print("%d FAILURE(S)" % fails)
    sys.exit(1)
print("all decide() cases pass")
