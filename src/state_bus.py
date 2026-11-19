"""state_bus.py - several independent perception channels, fused per field.

Why: no single channel is trustworthy on its own. The vision reader misses the
damage number on one card in 112/112 frames for a particular pet; protocol
sniffing is exact but needs elevation; OCR of the HUD text is slow but reliable.
Running them in parallel and voting per field turns their disagreements into a
signal - a conflict is exactly the moment we should not trust the state.

Each provider returns a partial state. Fusion rules:

  * per field, the highest-priority provider wins
  * if the others disagree beyond a tolerance, the field is still taken from the
    winner but marked low-confidence and the conflict is logged
  * a field nobody produced is simply absent (callers must treat it as unknown,
    never as zero)

usage:
    from state_bus import StateBus, VisionProvider
    bus = StateBus([VisionProvider(screen)])
    st, conflicts = bus.poll()
"""
from __future__ import annotations
import sys
import paths

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

LOG_DIR = str(paths.ROOT / "logs")
CONFLICT_LOG = os.path.join(LOG_DIR, "state_conflicts.jsonl")

# field -> (tolerance for "same value", max age in seconds)
FIELD_SPEC = {
    "my_hp_pct": (1.5, 2.0),
    "foe_hp_pct": (1.5, 2.0),
    "my_hp_abs": (1.0, 2.0),
    "foe_hp_abs": (1.0, 2.0),
    "my_hp_max": (1.0, 300.0),
    "foe_hp_max": (1.0, 300.0),
    "energy": (0.0, 3.0),
    "my_name": (None, 120.0),
    "foe_name": (None, 120.0),
    "skills": (None, 1.5),
    "weather": (None, 60.0),
}


@dataclass
class Reading:
    provider: str
    field: str
    value: Any
    t: float
    conf: float = 1.0


@dataclass
class Fused:
    state: Dict[str, Any] = field(default_factory=dict)
    sources: Dict[str, str] = field(default_factory=dict)
    confidence: Dict[str, float] = field(default_factory=dict)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)


def _skills_key(skills):
    if not skills:
        return None
    out = []
    for s in skills:
        if not s:
            out.append(None)
        else:
            out.append((s.get("text"), s.get("kind"), bool(s.get("lit"))))
    return tuple(out)


def _same(a, b, tol):
    if tol is None:                       # non-numeric: exact comparison
        if isinstance(a, list) or isinstance(b, list):
            return _skills_key(a) == _skills_key(b)
        return a == b
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return a == b


class Provider:
    """Base class. Subclasses implement read(now) -> Dict[field, value]."""

    name = "base"

    def read(self, now: float) -> Dict[str, Any]:
        raise NotImplementedError

    # Per-field confidence for this channel, 0..1. Overridden per subclass.
    confidence = {}

    def conf(self, field_name):
        # A provider that does not declare a confidence is not penalised: it is
        # assumed to be as good as it claims to be.
        return float(self.confidence.get(field_name, 1.0))


class StateBus:
    def __init__(self, providers: List[Provider],
                 priority: Optional[Dict[str, float]] = None):
        self.providers = providers
        self.priority = priority or {}
        self.readings: List[Reading] = []

    def _priority(self, name):
        return float(self.priority.get(name, 1.0))

    def collect(self, now: Optional[float] = None):
        now = now if now is not None else time.time()
        for p in self.providers:
            try:
                partial = p.read(now)
            except Exception as e:
                partial = {"_error": str(e)}
            for k, v in (partial or {}).items():
                if k.startswith("_"):
                    continue
                if k not in FIELD_SPEC:
                    continue
                self.readings.append(Reading(p.name, k, v, now, p.conf(k)))
        # keep the buffer small
        cutoff = now - 180.0
        self.readings = [r for r in self.readings if r.t >= cutoff]

    def fuse(self, now: Optional[float] = None) -> Fused:
        now = now if now is not None else time.time()
        out = Fused()
        for fname, (tol, max_age) in FIELD_SPEC.items():
            rs = [r for r in self.readings if r.field == fname and now - r.t <= max_age]
            if not rs:
                continue
            rs.sort(key=lambda r: (-self._priority(r.provider), -r.conf))
            top = rs[0]
            out.state[fname] = top.value
            out.sources[fname] = top.provider
            out.confidence[fname] = top.conf
            others = [r for r in rs[1:] if r.provider != top.provider]
            disagree = [r for r in others if not _same(top.value, r.value, tol)]
            if disagree:
                out.confidence[fname] = min(out.confidence[fname], 0.4)
                out.conflicts.append({
                    "field": fname,
                    "chosen": {"provider": top.provider, "value": _plain(top.value)},
                    "other": [{"provider": r.provider,
                               "value": _plain(r.value)} for r in disagree],
                })
        self._cross_check(out)
        return out

    @staticmethod
    def _cross_check(out: Fused):
        """Percentage and absolute HP must tell the same story.

        Vision reads the bar as a percentage; the protocol (or an OCR read of
        "490/490") gives absolutes. If 100*abs/max disagrees with the bar, one of
        the two channels is lying and the turn is not safe to act on.
        """
        for side in ("my", "foe"):
            pct = out.state.get("%s_hp_pct" % side)
            abs_ = out.state.get("%s_hp_abs" % side)
            mx = out.state.get("%s_hp_max" % side)
            if pct is None or abs_ is None or not mx:
                continue
            derived = 100.0 * float(abs_) / float(mx)
            if abs(derived - float(pct)) <= 4.0:
                continue
            out.confidence["%s_hp_pct" % side] = 0.3
            out.conflicts.append({
                "field": "%s_hp_pct" % side,
                "chosen": {"provider": out.sources.get("%s_hp_pct" % side, "?"),
                           "value": pct},
                "other": [{"provider": "%s_hp_abs/max" % side,
                           "value": round(derived, 1)}],
            })

    def poll(self, now: Optional[float] = None, log_conflicts=True) -> Fused:
        self.collect(now)
        fused = self.fuse(now)
        if fused.conflicts and log_conflicts:
            self._log(fused.conflicts)
        return fused

    @staticmethod
    def _log(conflicts):
        try:
            with open(CONFLICT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": time.strftime("%H:%M:%S"),
                    "conflicts": conflicts,
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass


def _plain(v):
    if isinstance(v, list):
        return [(_skills_key([s])[0] if s else None) for s in v]
    return v
