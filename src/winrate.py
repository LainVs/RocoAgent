"""winrate.py - the number the whole project is judged by.

Reads logs/results.jsonl (written when the ladder badge delta is observed) and
prints the win rate overall and per hour, so a change can be credited or blamed.

usage:
    python winrate.py
    python winrate.py --last 20
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

LOG_DIR = r"D:\Projects\RocoAgent\logs"
RESULTS = os.path.join(LOG_DIR, "results.jsonl")


def load():
    out = []
    if not os.path.exists(RESULTS):
        return out
    with open(RESULTS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--last", type=int, default=0)
    args = ap.parse_args()

    rows = load()
    if not rows:
        print("no results yet (%s)" % RESULTS)
        print("results are recorded when the bot re-enters the lobby and the")
        print("ladder badge changes; play a match with robot.py running first.")
        return 1
    if args.last:
        rows = rows[-args.last:]

    c = Counter(r["result"] for r in rows)
    decided = c["win"] + c["loss"]
    print("matches recorded : %d  (win %d / loss %d / flat %d)"
          % (len(rows), c["win"], c["loss"], c["flat"]))
    if decided:
        print("win rate         : %.1f%%  (of %d decided)" % (
            100.0 * c["win"] / decided, decided))
        # Wilson lower bound at 95%: how much of the rate is actually earned
        n, w = decided, c["win"]
        p = w / n
        z = 1.96
        denom = 1 + z * z / n
        centre = (p + z * z / (2 * n)) / denom
        half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
        print("95%% lower bound  : %.1f%%  (need ~100 decided matches to trust)"
              % (100.0 * (centre - half)))
    print()
    by_hour = {}
    for r in rows:
        h = (r.get("ts") or "00:00:00")[:2]
        d = by_hour.setdefault(h, Counter())
        d[r["result"]] += 1
    for h in sorted(by_hour):
        d = by_hour[h]
        dec = d["win"] + d["loss"]
        rate = ("%.0f%%" % (100.0 * d["win"] / dec)) if dec else "-"
        print("  %s:00  win %2d  loss %2d  flat %2d   %s" % (
            h, d["win"], d["loss"], d["flat"], rate))
    return 0


if __name__ == "__main__":
    sys.exit(main())
