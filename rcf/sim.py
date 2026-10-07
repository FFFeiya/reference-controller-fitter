"""Scripted simulation of a controller against an input timeline (used by the fitter).

A :class:`Scenario` describes one reference clip from the controller's point of view:
start state, the input timeline (horizontal axis + jump press/release) and the terrain
contacts that were observed in the reference (landing floor heights, walk-off times).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .model import ControllerParams, State, advance, try_jump, release_jump, leave_ground, INF


@dataclass
class InputTrack:
    axis0: int = 0                                                  # axis value at t_start
    axis_events: list[tuple[float, int]] = field(default_factory=list)  # (t, -1|0|1)
    jumps: list[tuple[float, float]] = field(default_factory=list)      # (press, release)

    def axis_at(self, t: float) -> int:
        v = self.axis0
        for te, a in self.axis_events:
            if te <= t:
                v = a
            else:
                break
        return v

    def held_at(self, t: float) -> bool:
        return any(p <= t < r for p, r in self.jumps)

    def to_dict(self) -> dict:
        return {"axis0": self.axis0,
                "axis_events": [[float(t), int(a)] for t, a in self.axis_events],
                "jumps": [[float(p), float(r)] for p, r in self.jumps]}

    @classmethod
    def from_dict(cls, d: dict) -> "InputTrack":
        return cls(int(d.get("axis0", 0)),
                   [(float(t), int(a)) for t, a in d.get("axis_events", [])],
                   [(float(p), float(r)) for p, r in d.get("jumps", [])])

    def sorted(self) -> "InputTrack":
        return InputTrack(self.axis0, sorted(self.axis_events), sorted(self.jumps))


@dataclass
class Scenario:
    t_start: float
    t_end: float
    start: State
    inputs: InputTrack
    floors: list[tuple[float, float]] = field(default_factory=list)  # (from time, floor y)
    ledges: list[float] = field(default_factory=list)                 # walk-off times
    floor0: float = 0.0

    def to_dict(self) -> dict:
        s = self.start
        return {"t_start": self.t_start, "t_end": self.t_end,
                "start": {"x": s.x, "y": s.y, "vx": s.vx, "vy": s.vy, "grounded": s.grounded,
                          "jump_active": s.jump_active},
                "inputs": self.inputs.to_dict(),
                "floors": [[float(t), float(y)] for t, y in self.floors],
                "ledges": [float(t) for t in self.ledges],
                "floor0": self.floor0}

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        s = d["start"]
        return cls(float(d["t_start"]), float(d["t_end"]),
                   State(s["x"], s["y"], s["vx"], s["vy"], bool(s["grounded"]), bool(s.get("jump_active", False))),
                   InputTrack.from_dict(d["inputs"]),
                   [(float(t), float(y)) for t, y in d.get("floors", [])],
                   [float(t) for t in d.get("ledges", [])],
                   float(d.get("floor0", 0.0)))


# event kinds, ordered so that simultaneous events resolve deterministically
_EV_FLOOR, _EV_LEDGE, _EV_AXIS, _EV_JUMP_UP, _EV_JUMP_DOWN = 0, 1, 2, 3, 4


def _events(sc: Scenario) -> list[tuple[float, int, float]]:
    ev = []
    for t, y in sc.floors:
        ev.append((t, _EV_FLOOR, y))
    for t in sc.ledges:
        ev.append((t, _EV_LEDGE, 0.0))
    for t, a in sc.inputs.axis_events:
        ev.append((t, _EV_AXIS, float(a)))
    for p, r in sc.inputs.jumps:
        ev.append((p, _EV_JUMP_DOWN, 0.0))
        ev.append((max(r, p), _EV_JUMP_UP, 0.0))
    ev.sort(key=lambda e: (e[0], e[1]))
    return ev


def simulate(p: ControllerParams, sc: Scenario, times: np.ndarray) -> dict[str, np.ndarray]:
    """Simulate the scenario and sample the state at the given (sorted) times."""
    times = np.asarray(times, dtype=float)
    n = len(times)
    out = {k: np.empty(n) for k in ("x", "y", "vx", "vy")}
    out["grounded"] = np.zeros(n, dtype=bool)
    s = sc.start.copy()
    t = sc.t_start
    u = sc.inputs.axis0
    held = False
    floor = sc.floor0
    events = _events(sc)
    # events at or before t_start
    ei = 0
    def apply(e):
        nonlocal u, held, floor
        _, kind, val = e
        if kind == _EV_FLOOR:
            floor = val
        elif kind == _EV_LEDGE:
            leave_ground(s)
        elif kind == _EV_AXIS:
            u = int(val)
        elif kind == _EV_JUMP_DOWN:
            held = True
            try_jump(s, p)
        elif kind == _EV_JUMP_UP:
            held = False
            release_jump(s, p)
    while ei < len(events) and events[ei][0] <= t:
        apply(events[ei])
        ei += 1
    for i in range(n):
        target = times[i]
        while ei < len(events) and events[ei][0] <= target:
            te = events[ei][0]
            if te > t:
                advance(s, p, u, held, te - t, floor if not s.grounded else -INF)
                t = te
            apply(events[ei])
            ei += 1
        if target > t:
            advance(s, p, u, held, target - t, floor if not s.grounded else -INF)
            t = target
        out["x"][i] = s.x
        out["y"][i] = s.y
        out["vx"][i] = s.vx
        out["vy"][i] = s.vy
        out["grounded"][i] = s.grounded
    return out


def simulate_dense(p: ControllerParams, sc: Scenario, dt: float = 1 / 120) -> dict[str, np.ndarray]:
    times = np.arange(sc.t_start, sc.t_end + 1e-9, dt)
    r = simulate(p, sc, times)
    r["t"] = times
    return r
