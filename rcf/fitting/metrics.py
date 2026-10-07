"""Behavioural error metrics between a reference clip and a simulated controller."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..model import ControllerParams
from ..reference import ReferenceTrajectory, Inference, _sg, _fill_invalid
from ..sim import Scenario, simulate

METRIC_NAMES = ["trajectory", "velocity", "apex_timing", "landing_timing", "max_height", "horizontal"]
METRIC_LABELS = {"trajectory": "Trajectory", "velocity": "Velocity profile", "apex_timing": "Apex timing",
                 "landing_timing": "Landing timing", "max_height": "Max height", "horizontal": "Horizontal"}


def clip_scale(ref: ReferenceTrajectory) -> float:
    """Normalization length of a clip: its extent of motion (at least 1.5 character heights)."""
    v = ref.valid if ref.valid.any() else np.ones(len(ref.t), bool)
    return float(max(np.ptp(ref.x[v]), np.ptp(ref.y[v]), 1.5 * ref.char_height, 1e-6))


@dataclass
class ClipMetrics:
    values: dict[str, float] = field(default_factory=dict)  # fraction (0.05 = 5 %)

    @property
    def overall(self) -> float:
        vals = [v for v in self.values.values() if v is not None and np.isfinite(v)]
        return float(np.mean(vals)) if vals else float("nan")

    def to_dict(self):
        return {"values": self.values, "overall": self.overall}


def _apex_and_landing(t, y, grounded, t0, t1):
    """Apex time/height and landing time of the simulated motion within a window."""
    m = (t >= t0) & (t <= t1)
    if not m.any():
        return None, None, None
    idx = np.where(m)[0]
    k = idx[np.argmax(y[idx])]
    land = None
    for j in range(k, len(t)):
        if grounded[j]:
            land = t[j]
            break
    return t[k], y[k], land


def compute_metrics(p: ControllerParams, sc: Scenario, ref: ReferenceTrajectory, inf: Inference) -> ClipMetrics:
    S = clip_scale(ref)
    v = ref.valid
    sim = simulate(p, sc, ref.t)
    dx = sim["x"] - ref.x
    dy = sim["y"] - ref.y
    vals: dict[str, float] = {}
    vals["trajectory"] = float(np.sqrt(np.mean((dx[v] ** 2 + dy[v] ** 2))) / S)
    # velocity profile: differentiate reference AND simulation with the same Savitzky-Golay
    # filter (comparing exact sim velocities to a filtered derivative would penalise the
    # filter, not the controller)
    x, y = _fill_invalid(ref)
    win = max(0.15, 5.0 / ref.fps)
    vxr = _sg(x, ref.fps, win, deriv=1)
    vyr = _sg(y, ref.fps, win, deriv=1)
    vxs = _sg(sim["x"], ref.fps, win, deriv=1)
    vys = _sg(sim["y"], ref.fps, win, deriv=1)
    vmax = max(np.max(np.hypot(vxr, vyr)), 1e-6)
    dv = np.hypot(vxs - vxr, vys - vyr)
    vals["velocity"] = float(np.sqrt(np.mean(dv[v] ** 2)) / vmax)
    # event timing on a dense simulation
    dense_t = np.arange(ref.t[0], ref.t[-1] + 1e-9, 1 / 240)
    dsim = simulate(p, sc, dense_t)
    apex_e, land_e, h_e = [], [], []
    for ph in inf.phases:
        if ph.kind != "jump" or ph.t_apex is None:
            continue
        t_end = ph.t_land if ph.t_land is not None else ref.t[-1]
        # only complete-enough jumps: the apex must be observed and be more than a blip
        if ph.t_apex >= ref.t[-1] - 1.5 / ref.fps or (t_end - ph.t_off) < 4.0 / ref.fps:
            continue
        air = max(t_end - ph.t_off, 0.25)
        ta, ya, tl = _apex_and_landing(dense_t, dsim["y"], dsim["grounded"], ph.t_off - 0.1, t_end + 0.25 * air)
        if ta is None:
            continue
        apex_e.append(abs(ta - ph.t_apex) / air)
        h_ref = max(ph.y_apex - ph.y_off, 0.5 * ref.char_height)
        h_e.append(abs((ya - ph.y_off) - (ph.y_apex - ph.y_off)) / h_ref)
        if ph.t_land is not None:
            land_e.append(abs((tl if tl is not None else dense_t[-1] + air) - ph.t_land) / air)
    vals["apex_timing"] = float(np.mean(apex_e)) if apex_e else None
    vals["landing_timing"] = float(np.mean(land_e)) if land_e else None
    vals["max_height"] = float(np.mean(h_e)) if h_e else None
    vi = np.where(v)[0]
    if len(vi) >= 2:
        disp_ref = ref.x[vi[-1]] - ref.x[vi[0]]
        disp_sim = sim["x"][vi[-1]] - sim["x"][vi[0]]
        vals["horizontal"] = float(abs(disp_sim - disp_ref) / max(abs(disp_ref), np.ptp(ref.x[v]), 1.5 * ref.char_height))
    return ClipMetrics({k: vals.get(k) for k in METRIC_NAMES})


def aggregate(metrics: list[ClipMetrics], weights: list[float] | None = None) -> ClipMetrics:
    if not metrics:
        return ClipMetrics({})
    weights = weights or [1.0] * len(metrics)
    out = {}
    for k in METRIC_NAMES:
        vs = [(m.values.get(k), w) for m, w in zip(metrics, weights) if m.values.get(k) is not None]
        out[k] = float(sum(v * w for v, w in vs) / sum(w for _, w in vs)) if vs else None
    return ClipMetrics(out)
