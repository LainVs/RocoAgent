"""proto_bridge.py - turn the protocol helper's battle state into bus fields.

rocom-helper owns the hard part (BE21 framing, AES, protobuf, the state machine)
and exposes the result at GET /api/battle/state. This script polls that endpoint
and appends one mapped record per change to a JSONL file, which is exactly what
providers.FileProtocolProvider reads.

Keeping the two processes separate on purpose: their code is AGPL and belongs to
them, ours only consumes plain JSON. Nothing here touches the game process.

Contract -> bus fields (see rocom-helper src/analysis/contracts.py):

    my_active.name          -> my_name
    my_active.current_hp    -> my_hp_abs
    my_active.max_hp        -> my_hp_max
    my_active.hp_pct*100    -> my_hp_pct
    my_active.energy        -> energy
    opp_active.*            -> foe_*
    weather[name]           -> weather
    result                  -> _result      (win / lose / None)

usage:
    python proto_bridge.py --selftest
    python proto_bridge.py --url http://127.0.0.1:18731/api/battle/state \
                           --out logs/proto_state.jsonl
"""
from __future__ import annotations
import paths

import argparse
import json
import os
import sys
import time
import urllib.request

OUT_DEFAULT = str(paths.ROOT / "logs/proto_state.jsonl")
URL_DEFAULT = "http://127.0.0.1:18731/api/battle/state"


def _pct(pet):
    try:
        return round(float(pet["hp_pct"]) * 100.0, 1)
    except (KeyError, TypeError, ValueError):
        cur, mx = pet.get("current_hp"), pet.get("max_hp")
        if cur is not None and mx:
            return round(100.0 * float(cur) / float(mx), 1)
    return None


def _weather(w):
    if w is None:
        return None
    if isinstance(w, str):
        return w
    if isinstance(w, dict):
        return w.get("name") or w.get("weather_name") or w.get("id")
    return str(w)


def map_state(state):
    """rocom-helper battle state dict -> our bus fields. Unknown stays absent."""
    out = {}
    if not isinstance(state, dict):
        return out
    me = state.get("my_active") or {}
    foe = state.get("opp_active") or {}

    if me.get("name"):
        out["my_name"] = me["name"]
    if me.get("current_hp") is not None:
        out["my_hp_abs"] = int(me["current_hp"])
    if me.get("max_hp"):
        out["my_hp_max"] = int(me["max_hp"])
    p = _pct(me)
    if p is not None:
        out["my_hp_pct"] = p
    if me.get("energy") is not None:
        out["energy"] = int(me["energy"])

    if foe.get("name"):
        out["foe_name"] = foe["name"]
    if foe.get("current_hp") is not None:
        out["foe_hp_abs"] = int(foe["current_hp"])
    if foe.get("max_hp"):
        out["foe_hp_max"] = int(foe["max_hp"])
    p = _pct(foe)
    if p is not None:
        out["foe_hp_pct"] = p

    w = _weather(state.get("weather"))
    if w is not None:
        out["weather"] = w

    if state.get("result"):
        out["_result"] = state["result"]
    out["_round"] = state.get("round")
    return out


def poll_once(url, timeout=1.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def run(url, out_path, hz=10.0, seconds=0):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    last = None
    end = time.time() + seconds if seconds else None
    n = 0
    while end is None or time.time() < end:
        t0 = time.time()
        raw = poll_once(url)
        if raw is not None:
            # some builds wrap the state in {"state": ...}
            state = raw.get("state", raw) if isinstance(raw, dict) else {}
            rec = map_state(state)
            if rec:
                rec["_t"] = round(t0, 3)
                key = json.dumps(rec, sort_keys=True)
                if key != last:
                    with open(out_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    last = key
                    n += 1
                    print("+ %s" % json.dumps(rec, ensure_ascii=False))
        time.sleep(max(0.0, 1.0 / hz - (time.time() - t0)))
    print("wrote %d records to %s" % (n, out_path))
    return 0


def selftest():
    sample = {
        "my_active": {"name": "寂灭骨龙", "current_hp": 343, "max_hp": 490,
                      "hp_pct": 0.7, "energy": 6},
        "opp_active": {"name": "水灵", "current_hp": 61, "max_hp": 434,
                       "hp_pct": 0.14, "energy": 9},
        "weather": {"name": "下雨"},
        "round": 7,
        "result": None,
    }
    rec = map_state(sample)
    want = {"my_name": "寂灭骨龙", "my_hp_abs": 343, "my_hp_max": 490,
            "my_hp_pct": 70.0, "energy": 6, "foe_name": "水灵",
            "foe_hp_abs": 61, "foe_hp_max": 434, "foe_hp_pct": 14.0,
            "weather": "下雨", "_round": 7}
    ok = True
    for k, v in want.items():
        if rec.get(k) != v:
            ok = False
            print("  %-12s got %r want %r" % (k, rec.get(k), v))
    print("selftest:", "OK" if ok else "**FAIL**", json.dumps(rec, ensure_ascii=False))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=URL_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--hz", type=float, default=10.0)
    ap.add_argument("--seconds", type=float, default=0)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    return run(args.url, args.out, args.hz, args.seconds)


if __name__ == "__main__":
    sys.exit(main())
