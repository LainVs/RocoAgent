"""bench_loop.py - where does a turn's time actually go?

Measures, on the live machine:
  * Perc.observe()          - capture + read the four cards + HP + countdown
  * policy_v2.decide()      - the expected-value decision
  * BLE round trip          - optional, via the resident daemon if it is up

Then prints the budget against the 15 s turn.

usage:
    python bench_loop.py [--n 30] [--ble]
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import sys
import time

sys.path.insert(0, r"D:\Projects\RocoAgent\src")
sys.path.insert(0, r"D:\Tools")

import robot          # noqa: E402
import policy_v2      # noqa: E402


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return 0.0
    k = max(0, min(len(xs) - 1, int(round(p / 100.0 * (len(xs) - 1)))))
    return xs[k]


def bench(python_callable, n, warmup=3):
    for _ in range(warmup):
        python_callable()
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        r = python_callable()
        xs.append((time.perf_counter() - t0) * 1000.0)
    return xs, r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--ble", action="store_true")
    args = ap.parse_args()

    screen = robot.Screen()
    if screen.rect is None:
        print("game window not found")
        return 1
    perc = robot.Perc(screen)

    print("== perception ==")
    xs, st = bench(perc.observe, args.n)
    print("  observe()      median %5.1f ms  p90 %5.1f ms  max %5.1f ms"
          % (statistics.median(xs), pct(xs, 90), max(xs)))
    print("  battle=%s bar=%s skills=%s" % (
        st["battle"], st["bar"],
        [s["text"] if s else None for s in st["skills"]]))

    print()
    print("== decision ==")
    demo = {
        "me": {"name": "寂灭骨龙", "hp": 490, "hp_max": 490, "energy": 10,
               "skills": [
                   {"i": 1, "dmg": 175, "cost": 5, "lit": True, "kind": "num"},
                   {"i": 2, "dmg": None, "cost": 2, "lit": True, "kind": "dash"},
                   {"i": 3, "dmg": 55, "cost": 2, "lit": True, "kind": "num"},
                   {"i": 4, "dmg": 85, "cost": 3, "lit": True, "kind": "num"}]},
        "foe": {"name": "水灵", "hp": 300, "hp_max": 434},
        "bench": [{"name": "音速犬", "hp": 366, "hp_max": 366, "energy": 10}],
    }
    xs2, out = bench(lambda: policy_v2.decide(demo), 50)
    print("  decide()       median %5.2f ms  p90 %5.2f ms  max %5.2f ms"
          % (statistics.median(xs2), pct(xs2, 90), max(xs2)))
    print("  -> %s (%s)" % (out[0], out[1]))

    ble_ms = None
    if args.ble:
        print()
        print("== BLE round trip ==")
        try:
            import state_bus  # noqa: F401  (only to keep imports warm)
            s = socket.create_connection(("127.0.0.1", 9999), timeout=5)
            xs3 = []
            for _ in range(5):
                t0 = time.perf_counter()
                s.sendall(b"key x\n")           # harmless dummy command
                s.recv(64)
                xs3.append((time.perf_counter() - t0) * 1000.0)
            ble_ms = statistics.median(xs3)
            print("  daemon key cmd median %.1f ms (includes TCP, not BLE-only)" % ble_ms)
        except Exception as e:
            print("  daemon not reachable (%s)" % e)

    print()
    print("== turn budget ==")
    obs = statistics.median(xs)
    dec = statistics.median(xs2)
    core = obs + dec + (ble_ms or 0)
    print("  capture+decide+send  %6.0f ms" % core)
    print("  deliberate waits     %6.0f ms  (stable-read 150, patience 2500)" % 2650)
    print("  worst case           %6.0f ms  (patience 2500 + retry 8000 excluded)" % (core + 2650))
    print("  turn length          %6.0f ms" % 15000)
    return 0


if __name__ == "__main__":
    sys.exit(main())
