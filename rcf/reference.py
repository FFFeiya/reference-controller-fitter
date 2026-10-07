"""Reference trajectories: screen track -> normalized gameplay-space trajectory, plus
automatic phase segmentation (grounded / airborne) and input-event inference.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import savgol_filter

from .model import State
from .sim import Scenario, InputTrack


@dataclass
class ReferenceTrajectory:
    t: np.ndarray        # seconds from clip start
    x: np.ndarray        # world units, +x right
    y: np.ndarray        # world units, +y up, 0 = first frame's feet
    valid: np.ndarray    # bool – frames usable for fitting
    fps: float
    char_height: float   # character bbox height in world units (for tolerances)

    def __len__(self):
        return len(self.t)

    def to_dict(self):
        return {"t": self.t.tolist(), "x": self.x.tolist(), "y": self.y.tolist(),
                "valid": self.valid.astype(int).tolist(), "fps": self.fps, "char_height": self.char_height}

    @classmethod
    def from_dict(cls, d):
        return cls(np.array(d["t"], float), np.array(d["x"], float), np.array(d["y"], float),
                   np.array(d["valid"], bool), float(d["fps"]), float(d["char_height"]))


def trajectory_from_track(track, ppu: float | None = None, char_height_units: float = 1.0,
                          use_camera: bool = True) -> tuple[ReferenceTrajectory, float]:
    """Convert a TrackResult into gameplay-space units.

    ppu = pixels per world unit. If None it is derived from the median box height so that
    the character is ``char_height_units`` tall. Returns (trajectory, ppu).
    """
    foot = track.foot_screen()
    n = len(foot)
    if ppu is None:
        ppu = float(np.median(track.bbox[:, 3])) / char_height_units
    cx = track.camera.cam_x[:n] if use_camera else np.zeros(n)
    cy = track.camera.cam_y[:n] if use_camera else np.zeros(n)
    wx = foot[:, 0] + cx
    wy = foot[:, 1] + cy
    x = (wx - wx[0]) / ppu
    y = -(wy - wy[0]) / ppu
    t = (track.frames - track.frames[0]) / track.fps
    valid = ~track.lost
    ch = float(np.median(track.bbox[:, 3])) / ppu
    return ReferenceTrajectory(t, x, y, valid, track.fps, ch), ppu


# --------------------------------------------------------------------------------------
# Phase segmentation and input inference
# --------------------------------------------------------------------------------------

@dataclass
class AirPhase:
    t_off: float          # take-off time (sub-frame estimate)
    t_land: float | None  # first grounded sample after the phase (None if still airborne)
    y_off: float
    y_land: float | None
    kind: str             # "jump" | "fall" (walked off a ledge / started airborne)
    t_apex: float | None = None
    y_apex: float | None = None


@dataclass
class Inference:
    scenario: Scenario
    grounded: np.ndarray
    phases: list[AirPhase]
    vx_smooth: np.ndarray
    vy_smooth: np.ndarray
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _sg(a, fps, seconds=0.1, order=2, deriv=0):
    """Savitzky-Golay smoothing/derivative over ~``seconds``. At low frame rates the window
    stays short (min 3 samples) and the polynomial order is reduced instead of stretching
    the window over a third of a second."""
    win = max(5 if fps >= 24 else 3, int(round(seconds * fps)) | 1)
    if win <= order:
        order = max(1, win - 2)
    if len(a) < win:
        win = len(a) - (1 - len(a) % 2)
    if win <= order:
        if deriv == 0:
            return a.copy()
        return np.gradient(a, 1.0 / fps)
    return savgol_filter(a, win, order, deriv=deriv, delta=1.0 / fps)


def _fill_invalid(ref: ReferenceTrajectory):
    x, y = ref.x.copy(), ref.y.copy()
    if not ref.valid.all() and ref.valid.sum() >= 2:
        idx = np.arange(len(x))
        x[~ref.valid] = np.interp(idx[~ref.valid], idx[ref.valid], x[ref.valid])
        y[~ref.valid] = np.interp(idx[~ref.valid], idx[ref.valid], y[ref.valid])
    return x, y


def segment_grounded(ref: ReferenceTrajectory) -> np.ndarray:
    t = ref.t
    fps = ref.fps
    x, y = _fill_invalid(ref)
    ys = _sg(y, fps, 0.1)
    vy = _sg(y, fps, 0.1, deriv=1)
    ch = max(ref.char_height, 1e-3)
    noise = np.std(y - ys) if len(y) > 5 else 0.0
    tol = max(3 * noise, 0.04 * ch)
    v_tol = max(1.6 * ch, 6 * noise * fps / 3)
    flat = np.abs(vy) < v_tol
    # also require raw y to be locally flat (vy from a smoother can hide take-off frames)
    dy = np.abs(np.diff(y, prepend=y[0]))
    flat &= dy < max(4 * noise, 0.08 * ch) + (0.25 * ch if fps < 20 else 0.0)
    grounded = np.zeros(len(t), bool)
    min_len = max(2, int(round(0.08 * fps)))
    look = max(1, int(round(0.12 * fps)))
    i = 0
    n = len(t)
    while i < n:
        if not flat[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and flat[j + 1] and abs(y[j + 1] - np.median(y[i:j + 1])) < tol * 2:
            j += 1
        if j - i + 1 >= min_len or (i == 0 and j - i + 1 >= 2) or (j == n - 1 and j - i + 1 >= 2):
            level = np.median(y[i:j + 1])
            before = y[max(0, i - look)] if i > 0 else None
            after = y[min(n - 1, j + look)] if j < n - 1 else None
            # An apex is a dome (the run's ends are lower than its middle) and only briefly flat;
            # standing on a bump/platform is flat across the whole run and lasts longer.
            run = y[i:j + 1]
            dome = (j - i >= 2 and run.max() - run[0] > 0.5 * tol and run.max() - run[-1] > 0.5 * tol)
            plateau = (j - i + 1) / fps >= 0.25 and not dome
            apex_like = (before is not None and after is not None and
                         before < level - tol and after < level - tol and not plateau)
            if not apex_like:
                grounded[i:j + 1] = True
        i = j + 1
    # Bunny-hop contacts: the player lands and jumps again within 1-2 frames, so there is
    # no flat run. Detect sharp V-shaped minima (fast fall straight into a fast rise).
    v_fast = max(3.0 * ch, 8 * noise * fps)
    flat_tol = max(3 * noise, 0.05 * ch)
    for k in range(2, n - 2):
        if grounded[max(0, k - 1):k + 3].any():
            continue
        # bottom of 1 or 2 samples (low frame rates often show the contact twice)
        L = 2 if (k + 3 < n and abs(y[k + 1] - y[k]) < flat_tol) else 1
        e = k + L - 1
        if not (y[k] <= y[k - 1] and y[e] <= y[e + 1]):
            continue
        vin = (y[k] - y[k - 2]) * fps / 2
        vout = (y[e + 2] - y[e]) * fps / 2 if e + 2 < n else (y[e + 1] - y[e]) * fps
        if vin < -v_fast and vout > v_fast:
            grounded[k:e + 1] = True
    # Animation offsets: a short, low "airborne" blip between two ground contacts at the same
    # level (sprite switching from idle to run frames shifts the box bottom) is not a jump.
    i = 0
    while i < n:
        if grounded[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and not grounded[j + 1]:
            j += 1
        if 0 < i and j < n - 1:
            lvl0, lvl1 = y[i - 1], y[j + 1]
            rise = np.max(y[i:j + 1]) - max(lvl0, lvl1)
            dur = (j - i + 1) / fps
            if rise < 0.25 * ch and dur < 0.2 and abs(lvl1 - lvl0) < 0.1 * ch:
                grounded[i:j + 1] = True
        i = j + 1
    return grounded


def _takeoff_time(t, y, i_last_ground, y0, fps):
    """Sub-frame take-off estimate: extrapolate a quadratic fitted to the first airborne samples."""
    k = max(3, int(round(0.12 * fps)))
    a = i_last_ground + 1
    b = min(len(t), a + k)
    if b - a < 3:
        return t[i_last_ground] + 0.5 / fps
    c = np.polyfit(t[a:b] - t[a], y[a:b], 2)
    roots = np.roots([c[0], c[1], c[2] - y0])
    roots = roots[np.isreal(roots)].real + t[a]
    lo, hi = t[i_last_ground], t[a]
    cand = [r for r in roots if lo - 0.5 / fps <= r <= hi + 1e-9]
    if cand:
        return float(max(cand))
    return float(0.5 * (lo + hi))


def decode_horizontal(vx: np.ndarray, fps: float, ch: float, valid: np.ndarray | None = None,
                      switch_penalty: float = 12.0) -> np.ndarray:
    """Most likely horizontal input sequence u[k] in {-1, 0, +1} (Viterbi).

    Each state predicts how the velocity should change over a short horizon: towards
    +max / 0 / -max at a rate estimated from the data. The emission cost is the squared,
    noise-normalized mismatch between predicted and observed velocity change; switching
    input costs ``switch_penalty``. This is far more robust to speed ripple (frame drops,
    animation) than thresholding accelerations frame by frame.
    """
    n = len(vx)
    if n < 3:
        return np.zeros(n, int)
    vmax = float(np.percentile(np.abs(vx), 95))
    if vmax < 0.5 * ch:
        return np.zeros(n, int)
    h = max(1, int(round(0.1 * fps)))
    dv = np.empty(n)
    dv[:-h] = vx[h:] - vx[:-h]
    dv[-h:] = dv[-h - 1] if n > h else 0.0
    T = h / fps
    big = np.abs(dv)[np.abs(dv) > 0.1 * vmax]
    a = float(np.percentile(big, 80)) / T if len(big) else vmax / 0.2
    a = max(a, vmax / 1.0)
    noise = float(np.std(np.diff(vx))) * np.sqrt(h) + 1e-6
    sigma = max(2 * noise, 0.12 * min(a * T, vmax))
    states = (-1, 0, 1)
    cost = np.zeros((n, 3))
    for si, s in enumerate(states):
        target = s * vmax
        pred = np.clip(target - vx, -a * T, a * T)
        cost[:, si] = ((dv - pred) / sigma) ** 2
    if valid is not None:
        cost[~valid] = 0.0
    # Viterbi
    acc = cost[0].copy()
    back = np.zeros((n, 3), int)
    for k in range(1, n):
        new = np.empty(3)
        for si in range(3):
            cand = acc + switch_penalty * (np.arange(3) != si)
            back[k, si] = int(np.argmin(cand))
            new[si] = cand[back[k, si]] + cost[k, si]
        acc = new
    path = np.zeros(n, int)
    path[-1] = int(np.argmin(acc))
    for k in range(n - 1, 0, -1):
        path[k - 1] = back[k, path[k]]
    return np.array([states[i] for i in path], int)


def infer(ref: ReferenceTrajectory) -> Inference:
    """Infer phases and an input timeline (initial guess for the fitter)."""
    t, fps = ref.t, ref.fps
    x, y = _fill_invalid(ref)
    n = len(t)
    notes, warnings = [], []
    grounded = segment_grounded(ref)
    vx = _sg(x, fps, 0.12, deriv=1)
    vy = _sg(y, fps, 0.1, deriv=1)
    ch = max(ref.char_height, 1e-3)
    noise_y = float(np.std(y - _sg(y, fps, 0.1))) if n > 5 else 0.0

    # ---- airborne phases
    phases: list[AirPhase] = []
    i = 0
    while i < n:
        if grounded[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and not grounded[j + 1]:
            j += 1
        if i == 0:
            # started airborne
            ph = AirPhase(t[0], t[j + 1] if j + 1 < n else None, y[0],
                          float(np.median(y[j + 1:j + 1 + max(2, int(0.1 * fps))])) if j + 1 < n else None, "fall")
        else:
            y0 = float(np.median(y[max(0, i - max(2, int(0.1 * fps))):i]))
            peak = float(np.max(y[i:j + 1]))
            kind = "jump" if peak > y0 + max(0.2 * ch, 0.02) else "fall"
            # the smoothed velocity flags take-off a little early: walk forward to the last
            # sample that is still at ground level
            small = max(3 * noise_y, 0.01 * ch)
            il = i - 1
            while il + 1 <= j and abs(y[il + 1] - y0) < small:
                il += 1
            t_off = _takeoff_time(t, y, il, y0, fps) if kind == "jump" else t[il] + 0.5 / fps
            y_land = None
            if j + 1 < n:
                e = j + 1
                while e + 1 < n and grounded[e + 1] and e - j < max(2, int(0.1 * fps)):
                    e += 1
                y_land = float(np.median(y[j + 1:e + 1]))
            ph = AirPhase(t_off, t[j + 1] if j + 1 < n else None, y0, y_land, kind)
        k = i + int(np.argmax(y[i:j + 1]))
        ph.t_apex, ph.y_apex = float(t[k]), float(y[k])
        phases.append(ph)
        i = j + 1

    # ---- horizontal input inference from the smoothed x velocity
    v_eps = max(0.6 * ch, 0.08 * max(np.percentile(np.abs(vx), 95), 1e-6))
    u = decode_horizontal(vx, fps, ch, ref.valid)
    # remove short blips
    min_seg = max(2, int(round(0.06 * fps)))
    segs = []
    k = 0
    while k < n:
        e = k
        while e + 1 < n and u[e + 1] == u[k]:
            e += 1
        segs.append([k, e, u[k]])
        k = e + 1
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for si, s in enumerate(segs):
            if s[1] - s[0] + 1 < min_seg:
                if si > 0:
                    segs[si - 1][1] = s[1]
                else:
                    segs[1][0] = s[0]
                del segs[si]
                changed = True
                break
        # merge equal neighbours
        merged = [segs[0]]
        for s in segs[1:]:
            if s[2] == merged[-1][2]:
                merged[-1][1] = s[1]
            else:
                merged.append(s)
        segs = merged
    axis0 = int(segs[0][2]) if segs else 0
    axis_events = []
    for s in segs[1:]:
        # an input change happens slightly before the velocity change becomes visible
        te = max(0.0, t[s[0]] - 0.5 / fps)
        axis_events.append((float(te), int(s[2])))

    # ---- jumps
    jumps = []
    heights = [ph.y_apex - ph.y_off for ph in phases if ph.kind == "jump"]
    hmax = max(heights) if heights else 1.0
    ledges = []
    floors = []
    for idx, ph in enumerate(phases):
        if ph.kind == "jump":
            rise = max(1e-3, ph.t_apex - ph.t_off)
            frac = float(np.clip(ph.y_apex - ph.y_off, 0, hmax) / hmax)
            # tallest jump ~ held through apex; lower jumps released earlier
            release = ph.t_off + rise * (1.05 if frac > 0.9 else max(0.15, frac ** 2))
            jumps.append((float(ph.t_off), float(release)))
        elif idx > 0 or ph.t_off > t[0]:
            ledges.append(float(ph.t_off))
        # landing floor for this phase, switched on at mid-time of the previous ground contact
        floor_y = ph.y_land if ph.y_land is not None else -1e9
        prev_land = phases[idx - 1].t_land if idx > 0 and phases[idx - 1].t_land is not None else t[0]
        t_switch = max(t[0], 0.5 * (prev_land + ph.t_off)) if idx > 0 or ph.t_off > t[0] else t[0]
        floors.append((float(t_switch), float(floor_y)))
    # floor before any phase = first grounded level
    floor0 = float(np.median(y[grounded][:5])) if grounded.any() else float(y[0])
    start_grounded = bool(grounded[0])
    start = State(x=float(x[0]), y=float(y[0]), vx=float(vx[0]) if abs(vx[0]) > v_eps else 0.0,
                  vy=0.0 if start_grounded else float(vy[0]), grounded=start_grounded, jump_active=False)
    if start_grounded:
        start.y = floor0
    sc = Scenario(float(t[0]), float(t[-1]), start, InputTrack(axis0, axis_events, jumps), floors, ledges, floor0)

    # ---- diagnostics
    n_jumps = len(jumps)
    if n_jumps == 0:
        notes.append("No jump detected: vertical parameters cannot be estimated from this clip.")
    if np.ptp(x) < 1.0 * ch:
        notes.append("Little horizontal motion: run parameters weakly constrained by this clip.")
    if not ref.valid.all():
        warnings.append(f"{int((~ref.valid).sum())} frames have no reliable track and are ignored.")
    for ph in phases:
        if ph.t_land is None and ph.kind == "jump":
            warnings.append("Clip ends while airborne – landing not observed.")
    return Inference(sc, grounded, phases, vx, vy, notes, warnings)
