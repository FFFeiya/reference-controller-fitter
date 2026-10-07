"""Playable prototype: the fitted controller in a small level with AABB collisions.

Uses exactly the same integrator as the fitter (model.advance) and the same collision
scheme as the exported Unity controller: a downward probe gives the landing floor so
landings happen at the exact time inside the step, then horizontal walls and ceilings
clamp the displacement.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import ControllerParams, State, advance, try_jump, release_jump, INF


@dataclass
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float   # y up; y1 = top surface


def demo_level(params: ControllerParams | None = None) -> list[Rect]:
    """Flat ground with steps and platforms sized relative to the controller's jump."""
    h = 2.0
    if params is not None and params.rise_gravity > 0:
        h = max(0.5, min(12.0, params.jump_velocity ** 2 / (2 * params.rise_gravity)))
    run = 6.0 if params is None else max(3.0, min(30.0, params.max_speed * 0.8))
    rects = [Rect(-62, -5, -60, 40)]                    # left wall
    x = 6.0
    for frac in (0.35, 0.6, 0.85):                      # steps at increasing heights
        rects.append(Rect(x, 0, x + 2.5, frac * h))
        x += 2.5 + run * 0.5
    rects.append(Rect(x, 0, x + 1.0, 1.15 * h))         # too high to jump onto: a wall
    x += 1.0 + run * 0.6
    rects.append(Rect(x, 0.55 * h, x + 4.0, 0.55 * h + 0.4))  # floating platform
    x += 4.0 + run * 0.6
    gap = max(2.0, run * 0.55)
    rects.append(Rect(-60, -5, x, 0))                   # ground up to the gap
    rects.append(Rect(x + gap, -5, 220, 0))             # ground after the gap
    rects.append(Rect(x, -40, x + gap, -38))            # pit floor (you can't fall forever)
    return rects


@dataclass
class Player:
    state: State = field(default_factory=lambda: State(0.0, 0.0, 0.0, 0.0, True, False))
    width: float = 0.8
    height: float = 1.0
    jump_held: bool = False


class Playground:
    def __init__(self, params: ControllerParams, level: list[Rect] | None = None,
                 width: float = 0.8, height: float = 1.0):
        self.params = params
        self.level = level if level is not None else demo_level(params)
        self.player = Player(width=width, height=height)
        self.time = 0.0
        self.trail: list[tuple[float, float]] = []

    def reset(self, x: float = 0.0, y: float = 0.0):
        on_ground = self.ground_below(x, y, 1e-3)
        self.player.state = State(x, on_ground if on_ground is not None else y, 0.0, 0.0,
                                  on_ground is not None, False)
        self.player.jump_held = False
        self.time = 0.0
        self.trail.clear()

    # ---- geometry queries (feet at state.x, state.y; box = [x-w/2, x+w/2] x [y, y+h])
    def _overlap_x(self, r: Rect, x: float) -> bool:
        w = self.player.width / 2
        return r.x0 < x + w - 1e-6 and r.x1 > x - w + 1e-6

    def ground_below(self, x: float, y: float, max_drop: float) -> float | None:
        best = None
        for r in self.level:
            if self._overlap_x(r, x) and r.y1 <= y + 1e-6 and y - r.y1 <= max_drop:
                if best is None or r.y1 > best:
                    best = r.y1
        return best

    def step(self, axis: int, jump: bool, dt: float) -> None:
        p, pl = self.params, self.player
        s = pl.state
        # ground check / walked off
        if s.grounded and self.ground_below(s.x, s.y, 1e-3) is None:
            s.grounded = False
            s.jump_active = False
            s.vy = 0.0
        if jump and not pl.jump_held:
            try_jump(s, p)
        if not jump and pl.jump_held:
            release_jump(s, p)
        pl.jump_held = jump
        # 1) free motion with the fitted model
        saved = s.copy()
        x0, y0 = s.x, s.y
        advance(s, p, axis, jump, dt, -INF)
        w, h = pl.width / 2, pl.height
        # 2) walls first, at the current height
        new_x, hit_wall = s.x, False
        dx = s.x - x0
        if dx != 0:
            for r in self.level:
                if r.y0 < y0 + h - 1e-6 and r.y1 > y0 + 1e-6:   # vertical overlap at the old height
                    if dx > 0 and x0 + w <= r.x0 + 1e-6 < new_x + w:
                        new_x, hit_wall = r.x0 - w, True
                    elif dx < 0 and x0 - w >= r.x1 - 1e-6 > new_x - w:
                        new_x, hit_wall = r.x1 + w, True
        # 3) floor / ceiling probed at the NEW x (same order as the Unity export)
        if s.y < y0 and not s.grounded:
            fb = self.ground_below(new_x, y0, y0 - s.y + 1e-6)
            if fb is not None:
                # redo the step knowing the floor, so the landing happens at the exact time
                s.x, s.y, s.vx, s.vy, s.grounded, s.jump_active = (saved.x, saved.y, saved.vx, saved.vy,
                                                                   saved.grounded, saved.jump_active)
                advance(s, p, axis, jump, dt, fb)
                if not hit_wall:
                    new_x = s.x
                if not s.grounded:
                    s.y = fb; s.vy = 0.0; s.grounded = True; s.jump_active = False
        elif s.y > y0:
            for r in self.level:
                if self._overlap_x(r, new_x) and y0 + h <= r.y0 + 1e-6 < s.y + h:
                    s.y = r.y0 - h
                    s.vy = 0.0
                    s.jump_active = False
        s.x = new_x
        if hit_wall:
            s.vx = 0.0
        # walked past an edge during this step -> start falling now (not one step later)
        if s.grounded and self.ground_below(s.x, s.y, 1e-3) is None:
            s.grounded = False
            s.jump_active = False
            s.vy = 0.0
        self.time += dt
        self.trail.append((s.x, s.y))
        if len(self.trail) > 600:
            del self.trail[:-600]
