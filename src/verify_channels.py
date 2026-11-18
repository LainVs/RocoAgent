"""verify_channels.py - run perception channels in parallel and measure agreement.

This is the tool that answers "which channel is lying, and how often". It never
acts in game; it only reads and reports, so it is safe to run while playing.

usage:
    python verify_channels.py --seconds 60
    python verify_channels.py --seconds 300 --proto logs/proto_state.jsonl
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
import time

sys.path.insert(0, r"D:\Projects\RocoAgent\src")
sys.path.insert(0, r"D:\Tools")

from state_bus import StateBus  # noqa: E402
from providers import VisionProvider, FileProtocolProvider  # noqa: E402

PROTO_DEFAULT = r"D:\Projects\RocoAgent\logs\proto_state.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--proto", default=PROTO_DEFAULT)
    ap.add_argument("--hz", type=float, default=8.0)
    args = ap.parse_args()

    provs = [VisionProvider()]
    if os.path.exists(args.proto):
        provs.append(FileProtocolProvider(args.proto))
        print("proto channel: %s" % args.proto)
    else:
        print("proto channel: absent (%s) - only vision is being read" % args.proto)

    bus = StateBus(provs, priority={"proto": 2.0, "vision": 1.0})
    seen = collections.Counter()
    agreed = collections.Counter()
    conflicted = collections.Counter()
    end = time.time() + args.seconds
    polls = 0
    while time.time() < end:
        t0 = time.time()
        fused = bus.poll(log_conflicts=False)
        polls += 1
        for fname, src in fused.sources.items():
            seen[fname] += 1
            if fname in [c["field"] for c in fused.conflicts]:
                conflicted[fname] += 1
            else:
                agreed[fname] += 1
        dt = time.time() - t0
        time.sleep(max(0.0, 1.0 / args.hz - dt))

    print("\npolls: %d in %.0fs" % (polls, args.seconds))
    print("%-14s %6s %6s %8s" % ("field", "read", "ok", "conflict"))
    for fname in sorted(seen, key=lambda k: -seen[k]):
        n, c = seen[fname], conflicted[fname]
        print("%-14s %6d %6d %7.1f%%" % (
            fname, n, n - c, 100.0 * c / n if n else 0.0))
    total = sum(conflicted.values())
    print("\ntotal conflicts: %d" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
