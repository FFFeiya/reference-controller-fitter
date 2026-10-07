"""Multi-clip controller fitting.

One set of controller parameters is shared by every clip. Per clip we additionally fit
a few *latent input variables* (exact timing of the inferred input events, jump release
time) because the inputs of a video are never known exactly. The residual is the
position error of a full forward simulation against the reference at every valid frame,
normalized by each clip's extent and equalized so every clip counts the same
regardless of its length. A robust (soft-L1) loss limits the influence of tracking
glitches. Optimizer: scipy ``least_squares`` (trust-region reflective) with a sparse
Jacobian structure (latents of clip i only affect clip i), from several starts.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from scipy.optimize import least_squares

from ..model import ControllerParams, CandidateModel, PARAM_SPECS, INF, MODELS_BY_KEY, CANDIDATE_MODELS
from ..reference import ReferenceTrajectory, Inference, _sg, _fill_invalid
from ..sim import Scenario, InputTrack, simulate
from .metrics import clip_scale, compute_metrics, aggregate, ClipMetrics


@dataclass
class FitClip:
    name: str
    ref: ReferenceTrajectory
    inference: Inference
    scenario: Scenario            # initial input timeline (inferred and/or user-edited)
    weight: float = 1.0
    refine_inputs: bool = True    # let the fitter adjust input timings within small bounds
    uid: str = ""                 # stable identity of the source clip (names need not be unique)


@dataclass
class Latent:
    clip: int
    kind: str      # axis | press | release | vx0 | vy0
    index: int
    lo: float
    hi: float
    init: float
    rise: float = 0.0      # release latents: observed rise time of the jump (s)
    height: float = 0.0    # release latents: observed jump height (units)


# --------------------------------------------------------------------------------------
# Data analysis: what can the clips tell us?
# --------------------------------------------------------------------------------------

@dataclass
class DataCoverage:
    has_jump: bool = False
    has_height_variation: bool = False
    has_air_input: bool = False
    has_run: bool = False
    has_stop: bool = False
    has_turn: bool = False
    has_long_fall: bool = False
    jump_heights: list[float] = field(default_factory=list)

    def fixed_params(self) -> dict[str, tuple[float, str]]:
        """Parameters the data cannot identify -> (fixed value, reason)."""
        fx = {}
        if not self.has_jump:
            for n in ("jump_velocity", "gravity", "rise_gravity", "fall_gravity"):
                fx[n] = (float("nan"), "no jump in any clip")
        if not self.has_height_variation:
            fx["jump_cut"] = (1.0, "all jumps have the same height (no short/long pair) – variable jump height cannot be identified")
            fx["release_gravity_mult"] = (1.0, "all jumps have the same height – variable jump height cannot be identified")
        if not self.has_air_input:
            fx["air_control"] = (1.0, "no clip changes horizontal input in the air")
        if not self.has_run:
            for n in ("max_speed", "ground_accel", "ground_decel", "turn_accel"):
                fx[n] = (float("nan"), "no horizontal running in any clip")
        if not self.has_stop and self.has_run:
            fx["ground_decel"] = (float("nan"), "tied to acceleration: no clip shows a stop")
        if not self.has_turn:
            fx["turn_accel"] = (float("nan"), "tied to deceleration: no clip shows a turn")
        if not self.has_long_fall:
            fx["max_fall_speed"] = (INF, "no fall long enough to reach a terminal velocity")
        return fx


def analyse_coverage(clips: list[FitClip]) -> DataCoverage:
    cov = DataCoverage()
    for c in clips:
        sc, inf, ref = c.scenario, c.inference, c.ref
        ch = ref.char_height
        if np.ptp(ref.x[ref.valid]) > 2.0 * ch:
            cov.has_run = True
        prev = sc.inputs.axis0
        for t, a in sc.inputs.axis_events:
            airborne = any(ph.t_off <= t <= (ph.t_land if ph.t_land is not None else sc.t_end) for ph in inf.phases)
            if airborne:
                cov.has_air_input = True
            if a == 0 and prev != 0 and not airborne:
                cov.has_stop = True
            if a != 0 and prev == -a:
                cov.has_turn = True
            prev = a
        for ph in inf.phases:
            if ph.kind == "jump":
                cov.has_jump = True
                cov.jump_heights.append(ph.y_apex - ph.y_off)
            t_end = ph.t_land if ph.t_land is not None else sc.t_end
            if ph.t_apex is not None and t_end - ph.t_apex > 0.7:
                cov.has_long_fall = True
    if len(cov.jump_heights) >= 2 and min(cov.jump_heights) < 0.85 * max(cov.jump_heights):
        cov.has_height_variation = True
    return cov


# --------------------------------------------------------------------------------------
# Initial estimates straight from the data
# --------------------------------------------------------------------------------------

def estimate_initial(clips: list[FitClip]) -> ControllerParams:
    speeds, rise_g, fall_g, jv, accels = [], [], [], [], []
    for c in clips:
        ref = c.ref
        x, y = _fill_invalid(ref)
        vx = np.abs(_sg(x, ref.fps, 0.12, deriv=1))
        speeds.append(np.percentile(vx, 95))
        for ph in c.inference.phases:
            if ph.kind != "jump" or ph.t_apex is None:
                continue
            t_end = ph.t_land if ph.t_land is not None else ref.t[-1]
            r = (ref.t > ph.t_off) & (ref.t <= ph.t_apex) & ref.valid
            f = (ref.t >= ph.t_apex) & (ref.t < t_end) & ref.valid
            if r.sum() >= 3:
                cr = np.polyfit(ref.t[r] - ph.t_off, y[r] - ph.y_off, 2)
                if cr[0] < 0:
                    rise_g.append(-2 * cr[0])
                    jv.append(cr[1])
            if f.sum() >= 3:
                cf = np.polyfit(ref.t[f], y[f], 2)
                if cf[0] < 0:
                    fall_g.append(-2 * cf[0])
        # accel from the first start-up: time from 10 % to 80 % of the plateau speed
        vmax = np.percentile(vx, 95)
        if vmax > 2 * ref.char_height:
            a = np.argmax(vx > 0.1 * vmax); b = np.argmax(vx > 0.8 * vmax)
            if b > a:
                accels.append(0.7 * vmax / max(ref.t[b] - ref.t[a], 1 / ref.fps))
    p = ControllerParams()
    if speeds and max(speeds) > 0.05:
        p.max_speed = float(max(speeds))
    if accels:
        p.ground_accel = p.ground_decel = p.turn_accel = float(np.median(accels))
    else:
        p.ground_accel = p.ground_decel = p.turn_accel = p.max_speed / 0.15
    g = np.median(rise_g + fall_g) if (rise_g or fall_g) else 40.0
    p.rise_gravity = float(np.median(rise_g)) if rise_g else float(g)
    p.fall_gravity = float(np.median(fall_g)) if fall_g else float(g)
    heights = [ph.y_apex - ph.y_off for c in clips for ph in c.inference.phases if ph.kind == "jump"]
    if heights:
        p.jump_velocity = float(math.sqrt(2 * p.rise_gravity * max(heights)))
    elif jv:
        p.jump_velocity = float(max(jv))
    return p


# --------------------------------------------------------------------------------------
# Problem assembly
# --------------------------------------------------------------------------------------

@dataclass
class FitResult:
    model: CandidateModel
    params: ControllerParams
    scenarios: list[Scenario]
    clip_metrics: list[ClipMetrics]
    overall: ClipMetrics
    rss: float                  # sum of squared normalized residuals (non-robust)
    n_obs: int
    n_eff: float
    k_params: int
    k_latent: int
    free_names: list[str]
    fixed: dict[str, str]       # param -> reason it was not fitted
    uncertainty: dict[str, float]  # relative 1-sigma uncertainty of free params
    history: list[tuple[float, dict]] = field(default_factory=list)  # (overall traj error, params dict)
    success: bool = True
    message: str = ""
    seconds: float = 0.0
    bic: float = float("nan")
    cv_error: float | None = None

    @property
    def trajectory_error(self) -> float:
        return self.overall.values.get("trajectory") or float("nan")

    def summary(self) -> dict:
        return {"model": self.model.key, "params": self.params.to_dict(),
                "overall": self.overall.to_dict(),
                "clips": [m.to_dict() for m in self.clip_metrics],
                "rss": self.rss, "n_obs": self.n_obs, "n_eff": self.n_eff, "k_params": self.k_params,
                "k_latent": self.k_latent, "bic": self.bic, "cv_error": self.cv_error,
                "free": self.free_names, "fixed": self.fixed, "uncertainty": self.uncertainty,
                "scenarios": [s.to_dict() for s in self.scenarios], "message": self.message}


class Problem:
    def __init__(self, clips: list[FitClip], model: CandidateModel | None, base: ControllerParams,
                 coverage: DataCoverage | None = None, prior_weight: float = 0.003, frozen: bool = False,
                 forced: dict[str, tuple[float, str]] | None = None):
        """frozen=True: controller parameters are fixed to ``base``; only input latents are fitted.
        forced: extra parameters to hold fixed -> (value, reason)."""
        self.clips = clips
        self.frozen = base.copy() if frozen else None
        if frozen:
            uses_release = base.jump_cut < 1 or base.release_gravity_mult != 1 or base.apex_gravity_mult != 1
            model = CandidateModel("frozen", "frozen", "", [], uses_jump_release=uses_release)
        self.model = model
        self.base = base
        self.cov = coverage or analyse_coverage(clips)
        fixed = self.cov.fixed_params()
        fixed.update(forced or {})
        self.fixed_reasons = {}
        self.free = []
        for n in model.free:
            if n in fixed:
                self.fixed_reasons[n] = fixed[n][1]
            else:
                self.free.append(n)
        # values for fixed-but-in-model params: neutral value or the base estimate
        self.fixed_values = {}
        for n in model.free:
            if n in fixed:
                v = fixed[n][0]
                if isinstance(v, float) and math.isnan(v):
                    if n == "gravity":
                        v = 0.5 * (base.rise_gravity + base.fall_gravity)
                    elif n == "ground_decel" and "ground_accel" in self.free:
                        v = None  # tie to accel (handled in values())
                    elif n == "turn_accel":
                        v = None  # tie to decel
                    else:
                        v = getattr(base, n)
                self.fixed_values[n] = v
        self.prior_weight = prior_weight
        self.latents: list[Latent] = []
        for ci, c in enumerate(clips):
            self.latents += self._clip_latents(ci, c)
        self.scales = [clip_scale(c.ref) for c in clips]
        # jumps that land on a floor clearly above the take-off level: (first, last valid index
        # of the airborne phase, landing height, weight ~ sqrt(#frames the plateau is visible))
        self.raised = []
        for c in clips:
            tv = c.ref.t[c.ref.valid]
            lst = []
            for ph in c.inference.phases:
                if ph.kind == "jump" and ph.t_land is not None and ph.y_land is not None \
                        and ph.y_land > ph.y_off + 0.2 * c.ref.char_height:
                    i0 = int(np.searchsorted(tv, ph.t_off)); i1 = int(np.searchsorted(tv, ph.t_land + 1e-9))
                    lst.append((i0, max(i0 + 1, i1), float(ph.y_land), float(np.sqrt(max(1, i1 - i0)))))
            self.raised.append(lst)

    # ---- parameter transforms
    def _spec(self, n):
        return PARAM_SPECS[n]

    def to_z(self, vals: dict) -> np.ndarray:
        z = []
        for n in self.free:
            s = self._spec(n)
            v = float(np.clip(vals[n], s.lo, s.hi))
            z.append(math.log(v) if s.log else v)
        return np.array(z)

    def from_z(self, z) -> dict:
        out = {}
        for n, zi in zip(self.free, z):
            s = self._spec(n)
            out[n] = math.exp(zi) if s.log else float(zi)
        return out

    def z_bounds(self):
        lo, hi = [], []
        for n in self.free:
            s = self._spec(n)
            lo.append(math.log(s.lo) if s.log else s.lo)
            hi.append(math.log(s.hi) if s.log else s.hi)
        return lo, hi

    def values(self, z) -> dict:
        v = self.from_z(z)
        for n, fv in self.fixed_values.items():
            if fv is not None:
                v[n] = fv
        if "ground_decel" in self.fixed_values and self.fixed_values["ground_decel"] is None and "ground_accel" in v:
            v["ground_decel"] = v["ground_accel"]
        return v

    def params(self, z) -> ControllerParams:
        if self.frozen is not None:
            return self.frozen
        return self.model.build(self.values(z))

    # ---- latents
    def _clip_latents(self, ci: int, c: FitClip) -> list[Latent]:
        sc = c.scenario
        out = []
        if not c.refine_inputs:
            return out
        fps = c.ref.fps
        win = max(0.12, 3.0 / fps)
        for i, (t, a) in enumerate(sc.inputs.axis_events):
            out.append(Latent(ci, "axis", i, max(sc.t_start, t - win), min(sc.t_end, t + win), t))
        jump_phases = [ph for ph in c.inference.phases if ph.kind == "jump"]
        for i, (p, r) in enumerate(sc.inputs.jumps):
            out.append(Latent(ci, "press", i, max(sc.t_start, p - max(0.05, 1.5 / fps)), p + max(0.05, 1.5 / fps), p))
            if self.model.uses_jump_release and (self.frozen is not None or "jump_cut" in self.free
                                                 or "release_gravity_mult" in self.free):
                ph = jump_phases[i] if i < len(jump_phases) else None
                air = ((ph.t_land if ph and ph.t_land is not None else sc.t_end) - p)
                hi = max(0.05, air + 0.1)
                rise = (ph.t_apex - ph.t_off) if ph and ph.t_apex is not None else 0.5 * air
                height = (ph.y_apex - ph.y_off) if ph and ph.y_apex is not None else 0.0
                out.append(Latent(ci, "release", i, 1.0 / 240, hi, float(np.clip(r - p, 1.0 / 240, hi)),
                                  max(rise, 1.0 / 240), height))
        if sc.start.grounded is False:
            out.append(Latent(ci, "vy0", 0, -60.0, 60.0, sc.start.vy))
        if abs(sc.start.vx) > 0:
            out.append(Latent(ci, "vx0", 0, -80.0, 80.0, sc.start.vx))
        return out

    def scenario(self, ci: int, lat_values: dict[int, float]) -> Scenario:
        c = self.clips[ci]
        sc = c.scenario
        axis = list(sc.inputs.axis_events)
        jumps = [list(j) for j in sc.inputs.jumps]
        start = sc.start.copy()
        rel = {}
        for li, l in enumerate(self.latents):
            if l.clip != ci:
                continue
            v = lat_values[li]
            if l.kind == "axis":
                axis[l.index] = (v, axis[l.index][1])
            elif l.kind == "press":
                jumps[l.index][0] = v
            elif l.kind == "release":
                rel[l.index] = v
            elif l.kind == "vx0":
                start.vx = v
            elif l.kind == "vy0":
                start.vy = v
        for i, j in enumerate(jumps):
            if i in rel:
                j[1] = j[0] + rel[i]
            elif not self.model.uses_jump_release:
                j[1] = max(j[1], j[0])
        axis.sort()
        return Scenario(sc.t_start, sc.t_end, start, InputTrack(sc.inputs.axis0, axis, [tuple(j) for j in jumps]),
                        list(sc.floors), list(sc.ledges), sc.floor0)

    # ---- residuals
    def n_global(self):
        return len(self.free)

    def x0(self, init_vals: dict, release: str | float = "init") -> np.ndarray:
        """Start vector. release: "init" (inferred guess), "ranked" (jumps ranked by height
        across ALL clips: the tallest held past the apex, lower ones released earlier), or a
        number = fraction of each jump's observed rise time."""
        lat = []
        rel = [l for l in self.latents if l.kind == "release"]
        hmax = max([l.height for l in rel] + [1e-6])
        for l in self.latents:
            v = l.init
            if l.kind == "release":
                if release == "ranked":
                    frac = l.height / hmax
                    # jump height grows roughly linearly with hold time for short holds
                    v = l.rise * (1.1 if frac > 0.9 else max(0.15, frac))
                elif isinstance(release, (int, float)):
                    v = l.rise * float(release)
                v = float(np.clip(v, l.lo, l.hi))
            lat.append(v)
        return np.concatenate([self.to_z(init_vals), lat])

    def bounds(self):
        lo, hi = self.z_bounds()
        lo = lo + [l.lo for l in self.latents]
        hi = hi + [l.hi for l in self.latents]
        return np.array(lo), np.array(hi)

    def split(self, xv):
        g = self.n_global()
        z = xv[:g]
        lat = {i: xv[g + i] for i in range(len(self.latents))}
        return z, lat

    def residuals(self, xv) -> np.ndarray:
        z, lat = self.split(xv)
        p = self.params(z)
        res = []
        raised = []
        for ci, c in enumerate(self.clips):
            sc = self.scenario(ci, lat)
            v = c.ref.valid
            sim = simulate(p, sc, c.ref.t[v])
            w = math.sqrt(c.weight / max(1, v.sum()))
            S = self.scales[ci]
            res.append(w * (sim["x"] - c.ref.x[v]) / S)
            res.append(w * (sim["y"] - c.ref.y[v]) / S)
            # "reach the platform": landing on a raised floor is a discrete event, so an arc that
            # peaks just below it gets no gradient from the plateau frames. Penalize the shortfall.
            yv = sim["y"]
            for (i0, i1, y_land, wn) in self.raised[ci]:
                top = float(np.max(yv[i0:i1])) if i1 > i0 else y_land
                raised.append(w * wn * max(0.0, y_land + 0.02 * c.ref.char_height - top) / S)
        res.append(np.array(raised))   # rows ordered as in sparsity(): all clips, then these
        # weak priors: keep unidentified "modifier" params near neutral
        pr = []
        vals = self.from_z(z)
        for n in ("jump_cut", "air_control", "apex_gravity_mult", "release_gravity_mult"):
            if n in vals:
                pr.append(self.prior_weight * (vals[n] - 1.0))
        return np.concatenate(res + [np.array(pr)])

    def sparsity(self):
        g = self.n_global()
        rows = []
        for ci, c in enumerate(self.clips):
            nv = int(c.ref.valid.sum())
            block = np.zeros((2 * nv, g + len(self.latents)), np.int8)
            block[:, :g] = 1
            for li, l in enumerate(self.latents):
                if l.clip == ci:
                    block[:, g + li] = 1
            rows.append(block)
        n_raised = sum(len(r) for r in self.raised)
        if n_raised:
            rb = np.zeros((n_raised, g + len(self.latents)), np.int8)
            rb[:, :g] = 1
            k = 0
            for ci in range(len(self.clips)):
                for _ in self.raised[ci]:
                    for li, l in enumerate(self.latents):
                        if l.clip == ci:
                            rb[k, g + li] = 1
                    k += 1
            rows.append(rb)
        npr = sum(1 for n in ("jump_cut", "air_control", "apex_gravity_mult", "release_gravity_mult") if n in self.free)
        pr = np.zeros((npr, g + len(self.latents)), np.int8)
        pr[:, :g] = 1
        rows.append(pr)
        return np.vstack(rows)


def fit_inputs_only(clip: FitClip, params: ControllerParams, max_nfev: int = 200) -> tuple[Scenario, ClipMetrics]:
    """Keep the controller fixed and only refine this clip's input timings."""
    prob = Problem([clip], None, params, frozen=True)
    if not prob.latents:
        return clip.scenario, compute_metrics(params, clip.scenario, clip.ref, clip.inference)
    lo, hi = prob.bounds()
    x0 = np.clip(prob.x0({}), lo + 1e-9, hi - 1e-9)
    sol = least_squares(prob.residuals, x0, bounds=(lo, hi), method="trf", loss="soft_l1", f_scale=0.02,
                        max_nfev=max_nfev)
    _, lat = prob.split(sol.x)
    sc = prob.scenario(0, lat)
    return sc, compute_metrics(params, sc, clip.ref, clip.inference)


def _effective_n(res_blocks: list[np.ndarray]) -> float:
    """Effective sample size of autocorrelated residuals: sum n(1-rho)/(1+rho) per series."""
    n_eff = 0.0
    for r in res_blocks:
        if len(r) < 4:
            n_eff += len(r)
            continue
        r = r - r.mean()
        den = float(np.dot(r, r))
        rho = float(np.dot(r[:-1], r[1:]) / den) if den > 0 else 0.0
        rho = float(np.clip(rho, 0.0, 0.98))
        n_eff += len(r) * (1 - rho) / (1 + rho)
    return max(n_eff, 2.0)


NEUTRAL = {"jump_cut": 1.0, "air_control": 1.0, "release_gravity_mult": 1.0, "apex_gravity_mult": 1.0}


def fit_model(clips: list[FitClip], model: CandidateModel, init: ControllerParams | None = None,
              progress: Callable[[str, float, ControllerParams | None, float | None], None] | None = None,
              extra_starts: int = 3, max_nfev: int = 400, coverage: DataCoverage | None = None,
              cancel: Callable[[], bool] | None = None, prune_unidentifiable: bool = True) -> FitResult:
    """Fit one candidate model. If a "modifier" parameter (jump cut, air control, ...) comes
    out with a relative uncertainty above 100 %, the clips cannot determine it: it is fixed
    to its neutral value and the model refitted, so the exported controller does not inherit
    an arbitrary value that happened to fit the noise."""
    r = _fit_model(clips, model, init, progress, extra_starts, max_nfev, coverage, cancel, {})
    if not prune_unidentifiable:
        return r
    forced = {}
    for n, u in r.uncertainty.items():
        if n in NEUTRAL and u > 1.0:
            forced[n] = (NEUTRAL[n], f"not identifiable from these clips (±{100 * u:.0f}%) – fixed to neutral {NEUTRAL[n]:g}")
    if not forced:
        return r
    r2 = _fit_model(clips, model, r.params, progress, 0, max_nfev, coverage, cancel, forced)
    if r2.rss > r.rss * 1.10 + 1e-7:
        # fixing the parameter made the fit clearly worse: it is not actually free to drop
        return r
    r2.seconds += r.seconds
    r2.history = r.history + r2.history
    return r2


def _fit_model(clips, model, init, progress, extra_starts, max_nfev, coverage, cancel, forced) -> FitResult:
    t_start = time.time()
    base = init or estimate_initial(clips)
    prob = Problem(clips, model, base, coverage, forced=forced)
    init_vals = model.extract(base)
    # start 0: the parent model's solution exactly (modifiers neutral) -> a nested model can
    # never end up worse than its parent just because of a bad start
    parent_vals = {n: v for n, v in init_vals.items() if n in prob.free}
    for n in parent_vals:
        if n in NEUTRAL and abs(getattr(base, n) - NEUTRAL[n]) < 1e-9:
            parent_vals[n] = NEUTRAL[n]
    if "max_fall_speed" in parent_vals and not math.isfinite(base.max_fall_speed):
        parent_vals["max_fall_speed"] = PARAM_SPECS["max_fall_speed"].hi
    # sensible starting values for modifiers that the base may hold at neutral
    if "jump_cut" in prob.free and init_vals.get("jump_cut", 1.0) >= 0.99:
        init_vals["jump_cut"] = 0.5
    if "release_gravity_mult" in prob.free and init_vals.get("release_gravity_mult", 1.0) <= 1.01:
        init_vals["release_gravity_mult"] = 2.0
    if "air_control" in prob.free and init_vals.get("air_control", 1.0) >= 0.99:
        init_vals["air_control"] = 0.8
    if "apex_gravity_mult" in prob.free and init_vals.get("apex_gravity_mult", 1.0) >= 0.99:
        init_vals["apex_gravity_mult"] = 0.7
    if "max_fall_speed" in prob.free and not math.isfinite(base.max_fall_speed):
        init_vals["max_fall_speed"] = 1.5 * base.jump_velocity
    lo, hi = prob.bounds()
    sparsity = prob.sparsity()
    history: list[tuple[float, dict]] = []
    best = {"cost": float("inf")}

    uses_release = any(l.kind == "release" for l in prob.latents)
    starts = [prob.x0({**init_vals, **parent_vals}, "init"), prob.x0(init_vals, "ranked" if uses_release else "init")]
    if uses_release:
        starts.append(prob.x0(init_vals, 0.45))
    rng = np.random.default_rng(12345)
    release_opts = ["ranked", 0.3, 0.7, 1.1]
    for k in range(extra_starts):
        v = dict(init_vals)
        for n in v:
            s = PARAM_SPECS[n]
            if s.log:
                v[n] = float(np.clip(v[n] * math.exp(rng.normal(0, 0.35)), s.lo, s.hi))
            else:
                v[n] = float(np.clip(v[n] + rng.normal(0, 0.2), s.lo, s.hi))
        starts.append(prob.x0(v, release_opts[k % len(release_opts)] if uses_release else "init"))

    class Cancelled(Exception):
        pass

    def fun(xv):
        if cancel is not None and cancel():
            raise Cancelled()
        r = prob.residuals(xv)
        c = float(np.dot(r, r))
        if c < best["cost"] * 0.995:
            best["cost"] = c
            best["x"] = xv.copy()
            z, _ = prob.split(xv)
            p = prob.params(z)
            history.append((c, p.to_dict()))
            if progress is not None:
                progress(f"model {model.key}", float(c), p, None)
        return r

    sols = []
    message = ""
    try:
        for si, x0 in enumerate(starts):
            x0 = np.clip(x0, lo + 1e-9, hi - 1e-9)
            try:
                sol = least_squares(fun, x0, bounds=(lo, hi), jac_sparsity=sparsity, method="trf",
                                    loss="soft_l1", f_scale=0.02, x_scale="jac", max_nfev=max_nfev)
                sols.append(sol)
            except Cancelled:
                raise
            except Exception as e:  # pragma: no cover - numerical failure of one start
                message += f"start {si} failed: {e}. "
    except Cancelled:
        message += "Cancelled by user. "
    if not sols and "x" not in best:
        raise RuntimeError("Optimizer failed from every start: " + message)
    if sols:
        sol = min(sols, key=lambda s: s.cost)
        xbest = sol.x
        jac = sol.jac
        success = bool(sol.success)
        costs = sorted(s.cost for s in sols)
        if len(costs) > 1 and costs[-1] > 3 * costs[0] + 1e-9:
            message += "Some optimizer starts converged to worse local minima (best kept). "
    else:
        xbest, jac, success = best["x"], None, False
    z, lat = prob.split(xbest)
    params = prob.params(z)
    scen = [prob.scenario(ci, lat) for ci in range(len(clips))]
    cms = [compute_metrics(params, scen[ci], c.ref, c.inference) for ci, c in enumerate(clips)]
    overall = aggregate(cms, [c.weight for c in clips])
    # plain residuals for model selection
    blocks = []
    for ci, c in enumerate(clips):
        v = c.ref.valid
        sim = simulate(params, scen[ci], c.ref.t[v])
        S = prob.scales[ci]
        blocks.append((sim["x"] - c.ref.x[v]) / S)
        blocks.append((sim["y"] - c.ref.y[v]) / S)
    rss_terms = []
    for ci, c in enumerate(clips):
        nv = max(1, int(c.ref.valid.sum()))
        rss_terms.append(c.weight * (np.sum(blocks[2 * ci] ** 2) + np.sum(blocks[2 * ci + 1] ** 2)) / nv)
    rss = float(np.sum(rss_terms) / max(1e-9, sum(c.weight for c in clips)))  # mean squared normalized error
    n_obs = int(sum(len(b) for b in blocks))
    n_eff = _effective_n(blocks)
    # parameter uncertainty (relative, 1 sigma), inflated for autocorrelation
    unc = {}
    if jac is not None:
        J = jac.toarray() if hasattr(jac, "toarray") else np.asarray(jac)
        m = J.shape[0]
        dof = max(1, m - J.shape[1])
        r = prob.residuals(xbest)
        s2 = float(np.dot(r, r)) / dof * (n_obs / n_eff)
        try:
            cov = np.linalg.pinv(J.T @ J) * s2
            for i, n in enumerate(prob.free):
                sd = math.sqrt(max(cov[i, i], 0.0))
                spec = PARAM_SPECS[n]
                val = prob.from_z(z)[n]
                unc[n] = sd if spec.log else (sd / max(abs(val), 1e-3))
        except np.linalg.LinAlgError:
            pass
    # history in terms of trajectory error for the UI (convert cost -> approx NRMSE)
    hist = [(math.sqrt(max(c, 0.0) / max(1, len(clips))), p) for c, p in history]
    return FitResult(model, params, scen, cms, overall, rss, n_obs, n_eff, len(prob.free), len(prob.latents),
                     list(prob.free), dict(prob.fixed_reasons), unc, hist, success, message.strip(),
                     time.time() - t_start)
