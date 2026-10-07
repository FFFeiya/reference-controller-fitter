"""Movement IR: controller parameters, candidate model definitions and the core integrator.

Conventions
-----------
* World units, Y axis points UP, position = the character's feet (bottom-centre).
* Time in seconds, velocities in units/s, accelerations in units/s^2.
* The integrator is *exact* for the piecewise-constant-acceleration model: inside one
  call to :func:`advance` the step is split at every point where the acceleration
  changes (target speed reached, vy crosses 0 / the apex band / terminal velocity,
  landing). Results are therefore independent of the step size, which lets the fitter,
  the playable prototype and the exported Unity/Godot controllers (which contain a
  line-by-line port of :func:`advance`) produce the same motion at any tick rate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, asdict

INF = float("inf")
# "Instant" acceleration used by the simplest model (Model A). Large but finite so the
# integrator still splits the step cleanly.
INSTANT_ACCEL = 10000.0


@dataclass
class ControllerParams:
    # Horizontal
    max_speed: float = 6.0             # units/s
    ground_accel: float = 40.0         # units/s^2, speeding up towards max_speed
    ground_decel: float = 50.0         # units/s^2, slowing down with no input
    turn_accel: float = 50.0           # units/s^2, input opposite to current velocity
    # Vertical
    jump_velocity: float = 12.0        # units/s, initial vy on jump
    rise_gravity: float = 40.0         # units/s^2 while vy > 0
    fall_gravity: float = 40.0         # units/s^2 while vy <= 0
    jump_cut: float = 1.0              # vy multiplier applied once when jump is released while rising (1 = off)
    release_gravity_mult: float = 1.0  # rise gravity multiplier while jump is NOT held (1 = off)
    apex_gravity_mult: float = 1.0     # gravity multiplier near the apex while jump held (1 = off)
    apex_threshold: float = 0.0        # |vy| band (units/s) in which the apex multiplier applies
    max_fall_speed: float = INF        # terminal velocity (units/s, positive)
    # Air
    air_control: float = 1.0           # multiplier on all horizontal rates while airborne

    @classmethod
    def names(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def to_dict(self) -> dict:
        d = asdict(self)
        # JSON has no infinity
        d["max_fall_speed"] = None if math.isinf(self.max_fall_speed) else self.max_fall_speed
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ControllerParams":
        kw = {}
        for n in cls.names():
            if n in d:
                v = d[n]
                kw[n] = INF if (v is None and n == "max_fall_speed") else float(v)
        return cls(**kw)

    def copy(self, **changes) -> "ControllerParams":
        d = asdict(self)
        d.update(changes)
        return ControllerParams(**d)


# --------------------------------------------------------------------------------------
# Candidate models
# --------------------------------------------------------------------------------------

@dataclass
class ParamSpec:
    name: str
    lo: float
    hi: float
    log: bool = True   # optimise in log space (scale parameters)


# Bounds for every free parameter (world units; generous so they don't bias the fit).
PARAM_SPECS: dict[str, ParamSpec] = {
    "max_speed": ParamSpec("max_speed", 0.2, 80.0),
    "ground_accel": ParamSpec("ground_accel", 0.5, 2000.0),
    "ground_decel": ParamSpec("ground_decel", 0.5, 2000.0),
    "turn_accel": ParamSpec("turn_accel", 0.5, 2000.0),
    "jump_velocity": ParamSpec("jump_velocity", 0.5, 120.0),
    "gravity": ParamSpec("gravity", 0.5, 1500.0),
    "rise_gravity": ParamSpec("rise_gravity", 0.5, 1500.0),
    "fall_gravity": ParamSpec("fall_gravity", 0.5, 1500.0),
    "jump_cut": ParamSpec("jump_cut", 0.02, 1.0, log=False),
    "release_gravity_mult": ParamSpec("release_gravity_mult", 1.0, 8.0, log=False),
    "apex_gravity_mult": ParamSpec("apex_gravity_mult", 0.1, 1.0, log=False),
    "max_fall_speed": ParamSpec("max_fall_speed", 0.5, 200.0),
    "air_control": ParamSpec("air_control", 0.0, 1.5, log=False),
}


@dataclass
class CandidateModel:
    key: str
    title: str
    description: str
    free: list[str]
    uses_jump_release: bool = False

    @property
    def complexity(self) -> int:
        return len(self.free)

    def build(self, values: dict[str, float]) -> ControllerParams:
        """Expand the free-parameter values of this model into a full ControllerParams."""
        v = dict(values)
        p = ControllerParams()
        if "max_speed" in v:
            p.max_speed = v["max_speed"]
        if "ground_accel" in v:
            p.ground_accel = v["ground_accel"]
            p.ground_decel = v.get("ground_decel", v["ground_accel"])
        else:
            p.ground_accel = p.ground_decel = INSTANT_ACCEL
        p.turn_accel = v.get("turn_accel", p.ground_decel)
        p.jump_velocity = v.get("jump_velocity", p.jump_velocity)
        if "gravity" in v:
            p.rise_gravity = p.fall_gravity = v["gravity"]
        else:
            p.rise_gravity = v.get("rise_gravity", p.rise_gravity)
            p.fall_gravity = v.get("fall_gravity", p.fall_gravity)
        p.jump_cut = v.get("jump_cut", 1.0)
        p.release_gravity_mult = v.get("release_gravity_mult", 1.0)
        p.air_control = v.get("air_control", 1.0)
        if "apex_gravity_mult" in v:
            p.apex_gravity_mult = v["apex_gravity_mult"]
            # Band tied to the jump velocity (Celeste: HalfGravThreshold 40 / JumpSpeed 105)
            p.apex_threshold = 0.35 * p.jump_velocity
        p.max_fall_speed = v.get("max_fall_speed", INF)
        return p

    def extract(self, p: ControllerParams) -> dict[str, float]:
        """Inverse of build(): read this model's free values out of a full parameter set."""
        out = {}
        for n in self.free:
            if n == "gravity":
                out[n] = 0.5 * (p.rise_gravity + p.fall_gravity)
            else:
                val = getattr(p, n)
                if math.isinf(val):
                    val = PARAM_SPECS[n].hi
                out[n] = val
        return out


CANDIDATE_MODELS: list[CandidateModel] = [
    CandidateModel(
        "A", "A: constant gravity, instant speed",
        "Jump impulse + one gravity; horizontal speed changes instantly.",
        ["max_speed", "jump_velocity", "gravity"]),
    CandidateModel(
        "B", "B: + ground accel/decel",
        "Adds finite ground acceleration and deceleration (turning uses deceleration).",
        ["max_speed", "ground_accel", "ground_decel", "jump_velocity", "gravity"]),
    CandidateModel(
        "C", "C: + asymmetric gravity",
        "Separate rise and fall gravity.",
        ["max_speed", "ground_accel", "ground_decel", "jump_velocity", "rise_gravity", "fall_gravity"]),
    CandidateModel(
        "D", "D: + jump cut, air control",
        "Variable jump height via velocity cut on release; reduced horizontal control in the air.",
        ["max_speed", "ground_accel", "ground_decel", "jump_velocity", "rise_gravity", "fall_gravity",
         "jump_cut", "air_control"], uses_jump_release=True),
    CandidateModel(
        "D2", "D2: + held-jump gravity, air control",
        "Variable jump height via stronger rise gravity once jump is released (Mario-style).",
        ["max_speed", "ground_accel", "ground_decel", "jump_velocity", "rise_gravity", "fall_gravity",
         "release_gravity_mult", "air_control"], uses_jump_release=True),
    CandidateModel(
        "E", "E: D + apex gravity, terminal velocity, turn accel",
        "Adds a reduced-gravity apex band, a maximum fall speed and a separate turn-around rate.",
        ["max_speed", "ground_accel", "ground_decel", "turn_accel", "jump_velocity", "rise_gravity",
         "fall_gravity", "jump_cut", "air_control", "apex_gravity_mult", "max_fall_speed"],
        uses_jump_release=True),
]
MODELS_BY_KEY = {m.key: m for m in CANDIDATE_MODELS}


# --------------------------------------------------------------------------------------
# Core integrator (ported 1:1 to the exported controllers)
# --------------------------------------------------------------------------------------

@dataclass
class State:
    x: float = 0.0
    y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    grounded: bool = True
    jump_active: bool = False  # rising from a jump and jump-cut not yet consumed

    def copy(self) -> "State":
        return State(self.x, self.y, self.vx, self.vy, self.grounded, self.jump_active)


def try_jump(s: State, p: ControllerParams) -> bool:
    """Jump press. Only from the ground (no coyote time / buffering in the IR)."""
    if not s.grounded:
        return False
    s.vy = p.jump_velocity
    s.grounded = False
    s.jump_active = True
    return True


def release_jump(s: State, p: ControllerParams) -> None:
    """Jump release: apply the jump cut once while still rising."""
    if s.jump_active and s.vy > 0.0:
        s.vy *= p.jump_cut
    s.jump_active = False


def leave_ground(s: State) -> None:
    """Walked off a ledge."""
    if s.grounded:
        s.grounded = False
        s.jump_active = False
        s.vy = 0.0


def _horizontal(s: State, p: ControllerParams, u: int) -> tuple[float, float]:
    """Return (segment target velocity, rate >= 0)."""
    mult = 1.0 if s.grounded else p.air_control
    if u == 0:
        return 0.0, p.ground_decel * mult
    if s.vx * u < 0.0:
        # Turning around: brake at turn rate until vx crosses zero, then accelerate.
        return 0.0, p.turn_accel * mult
    target = u * p.max_speed
    if abs(s.vx) > p.max_speed:
        return target, p.ground_decel * mult
    return target, p.ground_accel * mult


def _gravity(s: State, p: ControllerParams, held: bool) -> float:
    if s.vy > 0.0:
        g = p.rise_gravity
        if not held:
            g *= p.release_gravity_mult
    else:
        g = p.fall_gravity
    if held and p.apex_gravity_mult != 1.0 and p.apex_threshold > 0.0 and abs(s.vy) <= p.apex_threshold:
        g *= p.apex_gravity_mult
    return g


def advance(s: State, p: ControllerParams, u: int, held: bool, h: float, floor: float = -INF) -> bool:
    """Advance the state by h seconds with constant input.

    u: horizontal input (-1, 0, +1); held: jump button held; floor: height of the ground
    below the character (used for landing while airborne). Returns True if it landed.
    """
    landed = False
    remaining = h
    for _ in range(64):
        if remaining <= 1e-12:
            break
        # ---- horizontal segment
        target, rate = _horizontal(s, p, u)
        dv = target - s.vx
        if dv == 0.0 or rate <= 0.0:
            ax, tx = 0.0, INF
        else:
            ax = rate if dv > 0.0 else -rate
            tx = abs(dv) / rate
        # ---- vertical segment
        ay, ty, tl = 0.0, INF, INF
        if not s.grounded:
            if s.vy <= -p.max_fall_speed:
                s.vy = -p.max_fall_speed
                ay = 0.0
            else:
                g = _gravity(s, p, held)
                ay = -g
                # next velocity threshold below current vy where gravity changes
                nxt = -p.max_fall_speed
                for thr in (0.0, p.apex_threshold, -p.apex_threshold):
                    if thr < s.vy and thr > nxt and (thr == 0.0 or p.apex_threshold > 0.0):
                        nxt = thr
                if g > 0.0 and not math.isinf(nxt):
                    ty = (s.vy - nxt) / g
            # landing time
            c = s.y - floor
            if c >= 0.0 and not math.isinf(floor):
                a = 0.5 * ay
                b = s.vy
                if a == 0.0:
                    if b < 0.0:
                        tl = c / -b
                else:
                    disc = b * b - 4.0 * a * c
                    if disc >= 0.0:
                        tl = (-b - math.sqrt(disc)) / (2.0 * a)
                        if tl < 0.0:
                            tl = INF
        seg = min(remaining, tx, ty, tl)
        # ---- integrate exactly over seg
        s.x += s.vx * seg + 0.5 * ax * seg * seg
        s.vx += ax * seg
        if seg == tx:
            s.vx = target
        if not s.grounded:
            s.y += s.vy * seg + 0.5 * ay * seg * seg
            s.vy += ay * seg
            if seg == tl:
                s.y = floor
                s.vy = 0.0
                s.grounded = True
                s.jump_active = False
                landed = True
            elif seg == ty:
                # snap onto the threshold so the next segment uses the new gravity
                s.vy = s.vy if abs(s.vy) > 1e-9 else 0.0
                s.vy = _snap_threshold(s.vy, p)
        if s.vy <= 0.0:
            s.jump_active = False
        remaining -= seg
    return landed


def _snap_threshold(vy: float, p: ControllerParams) -> float:
    for thr in (0.0, p.apex_threshold, -p.apex_threshold, -p.max_fall_speed):
        if abs(vy - thr) < 1e-7:
            # nudge just past the threshold in the direction of travel (downwards)
            return thr - 1e-9
    return vy
