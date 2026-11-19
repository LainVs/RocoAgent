"""providers.py - concrete perception channels for state_bus.

Each one is deliberately dumb: read, return fields, no fusion, no decisions.

  vision      - the existing pixel reader (robot.Perc). Gives HP percentages and
                the four skill cards. Fast (25 ms) but the weakest channel: the
                card reader is the thing that misses a whole column.
  proto       - tail of a JSONL written by the packet sniffer. Exact, but only
                exists once capture is running.
  synthetic   - the test double.
"""
from __future__ import annotations
import paths

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(paths.TOOLS))

from state_bus import Provider


class VisionProvider(Provider):
    name = "vision"
    confidence = {
        "my_hp_pct": 0.90,
        "foe_hp_pct": 0.90,
        "skills": 0.60,
        "energy": 0.70,
    }

    def __init__(self, screen=None):
        import robot
        self.robot = robot
        self.screen = screen or robot.Screen()
        self.perc = robot.Perc(self.screen)

    def read(self, now):
        st = self.perc.observe()
        out = {}
        # Only claim HP when the battle HUD is actually on screen. Outside a
        # battle the bar ROI is scenery and _read_bar reports 0.0, which the bus
        # would happily accept as a real reading and cross-check against the
        # protocol, producing false conflicts. Unknown must stay unknown.
        if not st.get("battle"):
            return out
        if st.get("my_pct") is not None:
            out["my_hp_pct"] = st["my_pct"]
        if st.get("foe_pct") is not None:
            out["foe_hp_pct"] = st["foe_pct"]
        if st.get("bar") and st.get("skills"):
            out["skills"] = st["skills"]
        return out


class FileProtocolProvider(Provider):
    """Reads the newest line of a sniffer-written JSONL.

    Contract with the sniffer: one JSON object per line, keys named like the
    bus fields (my_hp_abs, my_hp_max, foe_hp_abs, foe_hp_max, energy, my_name,
    foe_name, weather). Anything it does not know it must omit.
    """
    name = "proto"
    confidence = {k: 0.98 for k in (
        "my_hp_abs", "foe_hp_abs", "my_hp_max", "foe_hp_max",
        "energy", "my_name", "foe_name", "weather")}

    def __init__(self, path, max_age=3.0):
        self.path = path
        self.max_age = max_age
        self._offset = 0

    def read(self, now):
        try:
            if not os.path.exists(self.path):
                return {}
            size = os.path.getsize(self.path)
            if size < self._offset:
                self._offset = 0
            with open(self.path, "rb") as f:
                f.seek(self._offset)
                chunk = f.read()
            if not chunk:
                return {}
            self._offset += len(chunk)
            lines = [ln for ln in chunk.splitlines() if ln.strip()]
            if not lines:
                return {}
            # A Windows tool may have written this file with a UTF-8 BOM.
            rec = json.loads(lines[-1].lstrip(b"\xef\xbb\xbf").decode("utf-8"))
            age = now - float(rec.get("_t") or now)
            if age > self.max_age:
                return {}
            return {k: v for k, v in rec.items() if not k.startswith("_")}
        except Exception:
            return {}


class SyntheticProvider(Provider):
    """Fixed or callable readings, for tests."""

    def __init__(self, name, values, confidence=None):
        self.name = name
        self._values = values
        self.confidence = confidence or {}

    def read(self, now):
        v = self._values(now) if callable(self._values) else self._values
        return dict(v or {})
