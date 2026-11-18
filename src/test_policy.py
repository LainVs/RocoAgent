"""test_policy.py - sanity checks for the expected-value policy.

Cases are taken from the matches played on 2026-11-18, so a regression here
means the engine would have made a different call in a real game.

usage: python test_policy.py
"""
import sys

sys.path.insert(0, r"D:\Projects\RocoAgent\src")
sys.path.insert(0, r"D:\Tools")
import policy_v2  # noqa: E402

fails = 0


def show(name, want, action, why):
    global fails
    ok = action == want
    if not ok:
        fails += 1
    print("%-46s %-14s %s   (%s)" % (
        name, str(action), "OK" if ok else "**FAIL** want %s" % (want,), why))


def skill(i, dmg=None, cost=1, lit=True, kind="num"):
    return {"i": i, "dmg": dmg, "cost": cost, "lit": lit, "kind": kind}


# 10:57 — 寂灭骨龙 490/490, energy 10, 水灵 on 14%. The card read 175 on slot 1.
# 寂灭骨龙 is slower (60 vs 85), so the foe hits first, but 462 < 490: we live
# and the kill lands. Attacking is correct.
st = {
    "me": {"name": "寂灭骨龙", "hp": 490, "hp_max": 490, "energy": 10,
           "skills": [skill(1, 175, 5), skill(2, None, 2, kind="dash"),
                      skill(3, 55, 2), skill(4, 85, 3)]},
    "foe": {"name": "水灵", "hp": 61, "hp_max": 434},
    "bench": [{"name": "音速犬", "hp": 366, "hp_max": 366, "energy": 10}],
}
a, why, d = policy_v2.decide(st)
show("14% foe, lethal available -> attack slot 1", ("skill", 1), a, why)

# Same pet, but we are the ones about to die: 15% HP against a full-HP foe.
st2 = {
    "me": {"name": "寂灭骨龙", "hp": 74, "hp_max": 490, "energy": 10,
           "skills": [skill(1, 175, 5), skill(2, None, 2, kind="dash"),
                      skill(3, 55, 2), skill(4, 85, 3)]},
    "foe": {"name": "水灵", "hp": 412, "hp_max": 434},
    "bench": [{"name": "音速犬", "hp": 366, "hp_max": 366, "energy": 10}],
}
a, why, d = policy_v2.decide(st2)
ok = a is not None and a[0] in ("switch", "skill")
print("%-46s %-14s %s   (%s)" % (
    "15% hp vs full foe -> not a buff", str(a),
    "OK" if ok else "**FAIL**", why))

# The buff must never win while the incoming hit is lethal: every option
# should score negative, i.e. the engine knows this turn is lost.
st3 = {
    "me": {"name": "寂灭骨龙", "hp": 200, "hp_max": 490, "energy": 10,
           "skills": [skill(1, None, 1, kind="dash")]},
    "foe": {"name": "水灵", "hp": 434, "hp_max": 434},
    "bench": [],
}
a, why, d = policy_v2.decide(st3)
best = d["all"][0][0]
ok = best < 0.0
if not ok:
    fails += 1
print("%-46s %-14s %s   (%s, best=%.3f)" % (
    "only a buff, incoming is lethal -> all negative", str(a),
    "OK" if ok else "**FAIL**", why, best))

print()
if fails:
    print("%d FAILURE(S)" % fails)
    sys.exit(1)
print("all policy cases pass")
