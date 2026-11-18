"""test_state_bus.py - fusion, voting and conflict detection.

usage: python test_state_bus.py
"""
import os
import sys
import time

sys.path.insert(0, r"D:\Projects\RocoAgent\src")
sys.path.insert(0, r"D:\Tools")
from state_bus import StateBus  # noqa: E402
from providers import SyntheticProvider  # noqa: E402

fails = 0


def check(name, cond, extra=""):
    global fails
    if not cond:
        fails += 1
    print("%-56s %s %s" % (name, "OK" if cond else "**FAIL**", extra))


now = time.time()

# --- 1. agreement: two channels, small difference, no conflict --------------
bus = StateBus([
    SyntheticProvider("vision", {"foe_hp_pct": 71.4}),
    SyntheticProvider("proto", {"foe_hp_pct": 72.0}),
], priority={"proto": 2.0, "vision": 1.0})
f = bus.poll(now, log_conflicts=False)
check("agreeing channels -> no conflict", not f.conflicts, str(f.state))
check("higher priority wins", f.sources.get("foe_hp_pct") == "proto",
      f.sources.get("foe_hp_pct"))
check("both agree -> high confidence", f.confidence.get("foe_hp_pct") >= 0.9)

# --- 2. disagreement: conflict recorded, confidence drops -------------------
bus = StateBus([
    SyntheticProvider("vision", {"foe_hp_pct": 71.4}),
    SyntheticProvider("proto", {"foe_hp_pct": 18.0}),
], priority={"proto": 2.0, "vision": 1.0})
f = bus.poll(now, log_conflicts=False)
check("disagreeing channels -> conflict", len(f.conflicts) == 1,
      str(f.conflicts))
check("conflict lowers confidence", f.confidence.get("foe_hp_pct") <= 0.4)

# --- 3. cross-check: percentage must match absolute/max ---------------------
bus = StateBus([
    SyntheticProvider("vision", {"foe_hp_pct": 50.0}),
    SyntheticProvider("proto", {"foe_hp_abs": 300, "foe_hp_max": 600}),
])
f = bus.poll(now, log_conflicts=False)
check("50% == 300/600 -> consistent", not f.conflicts, str(f.conflicts))

bus = StateBus([
    SyntheticProvider("vision", {"foe_hp_pct": 50.0}),
    SyntheticProvider("proto", {"foe_hp_abs": 400, "foe_hp_max": 600}),
])
f = bus.poll(now, log_conflicts=False)
check("50% vs 400/600=66.7% -> conflict", len(f.conflicts) == 1,
      str(f.conflicts))

# --- 4. a field nobody produced stays absent (never zero) -------------------
bus = StateBus([SyntheticProvider("vision", {"my_hp_pct": 99.0})])
f = bus.poll(now, log_conflicts=False)
check("unknown field is absent, not 0", "energy" not in f.state, str(f.state))

# --- 5. stale readings expire ----------------------------------------------
state = {"n": 0}


def once(now):
    state["n"] += 1
    return {"energy": 7} if state["n"] == 1 else {}


bus = StateBus([SyntheticProvider("proto", once)])
bus.poll(now - 10.0, log_conflicts=False)          # 10 s old, then stops
f = bus.poll(now, log_conflicts=False)
check("stale reading dropped", "energy" not in f.state, str(f.state))

print()
if fails:
    print("%d FAILURE(S)" % fails)
    sys.exit(1)
print("all state-bus cases pass")
