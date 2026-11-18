"""pipeline_check.py - one command to see whether every link is ready.

No game input, no capture. It only probes.

    python pipeline_check.py

Each line is one link: OK / DEGRADED / DOWN, with the reason.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time

sys.path.insert(0, r"D:\Projects\RocoAgent\src")
sys.path.insert(0, r"D:\Tools")

ROOT = r"D:\Projects\RocoAgent"
PROTO_STATE = os.path.join(ROOT, "logs", "proto_state.jsonl")
DATA = r"D:\GameData\洛克王国世界\roco-claude\data\raw"
HELPER = r"D:\GameData\洛克王国世界\rocom-helper"

rows = []


def add(link, status, detail=""):
    rows.append((link, status, detail))


def check_window():
    try:
        import robot
        g = robot.find_game()
        if g:
            add("game window", "OK", "hwnd=%s rect=%s" % (g[0], g[1:]))
        else:
            add("game window", "DOWN", "not found")
    except Exception as e:
        add("game window", "DOWN", str(e))


def check_data():
    missing = [f for f in ("pokemon.json", "skills.json", "type_chart.json")
               if not os.path.exists(os.path.join(DATA, f))]
    if missing:
        add("game data", "DOWN", "missing " + ",".join(missing))
    else:
        add("game data", "OK", DATA)


def check_engine():
    try:
        import pvp_engine as E
        r = E.worst_incoming("水灵", "寂灭骨龙", top=1)
        if r:
            add("damage engine", "OK", "worst incoming %s dmg" % r[0]["dmg"])
        else:
            add("damage engine", "DEGRADED", "no incoming resolved")
    except Exception as e:
        add("damage engine", "DOWN", str(e))


def check_policy():
    try:
        import policy_v2
        w = policy_v2.weights()
        add("policy weights", "OK", "%d keys from config" % len(w))
    except Exception as e:
        add("policy weights", "DOWN", str(e))


def check_ble():
    try:
        s = socket.create_connection(("127.0.0.1", 9999), timeout=1.0)
        s.close()
        add("BLE daemon :9999", "OK", "accepting commands")
    except Exception:
        add("BLE daemon :9999", "DOWN", "start me_daemon.py")


def check_vision():
    try:
        import robot
        scr = robot.Screen()
        if scr.rect is None:
            add("vision channel", "DOWN", "no window")
            return
        perc = robot.Perc(scr)
        t0 = time.time()
        st = perc.observe()
        dt = (time.time() - t0) * 1000
        add("vision channel", "OK", "observe %.0f ms, battle=%s bar=%s"
            % (dt, st["battle"], st["bar"]))
    except Exception as e:
        add("vision channel", "DOWN", str(e))


def check_proto():
    if not os.path.exists(HELPER):
        add("protocol helper", "DOWN", "rocom-helper not cloned")
        return
    try:
        s = socket.create_connection(("127.0.0.1", 18731), timeout=0.5)
        s.close()
        add("protocol helper", "OK", "api listening on 18731")
    except Exception:
        add("protocol helper", "DEGRADED", "api not running (offline tests still pass)")

    if os.path.exists(PROTO_STATE):
        age = time.time() - os.path.getmtime(PROTO_STATE)
        n = sum(1 for _ in open(PROTO_STATE, encoding="utf-8", errors="ignore"))
        add("proto state file", "OK" if age < 10 else "DEGRADED",
            "%d records, last %.0fs ago" % (n, age))
    else:
        add("proto state file", "DOWN", PROTO_STATE)


def main():
    check_window()
    check_data()
    check_engine()
    check_policy()
    check_vision()
    check_ble()
    check_proto()

    print("%-20s %-9s %s" % ("LINK", "STATUS", "DETAIL"))
    print("-" * 78)
    bad = 0
    for link, status, detail in rows:
        if status != "OK":
            bad += 1
        print("%-20s %-9s %s" % (link, status, detail))
    print()
    print("%d/%d links OK" % (len(rows) - bad, len(rows)))
    return 0 if bad <= 2 else 1


if __name__ == "__main__":
    sys.exit(main())
