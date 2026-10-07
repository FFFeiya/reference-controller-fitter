"""Candidate model search and model selection.

Every candidate model is fitted to *all* clips (shared parameters). Models are ranked
with a BIC computed on the effective sample size of the (autocorrelated) residuals,
with the mean squared error floored at the tracking noise level: once a model explains
the data down to the measurement noise, extra parameters can only cost. Among models
whose score is within ``tie_margin`` of the best, the one with fewest parameters wins
("the simplest controller that explains all clips"). Optionally a leave-one-clip-out
cross-validation is run for the final candidates.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from ..model import CANDIDATE_MODELS, MODELS_BY_KEY, ControllerParams
from ..reference import _sg, _fill_invalid
from .fit import FitClip, FitResult, fit_model, estimate_initial, analyse_coverage, fit_inputs_only
from .metrics import clip_scale

# warm-start chain: which simpler model initialises which
_PARENT = {"B": "A", "C": "B", "D": "C", "D2": "C", "E": "D"}


@dataclass
class Selection:
    results: dict[str, FitResult]
    chosen: str
    scores: dict[str, float]
    noise_floor: float
    explanation: list[str] = field(default_factory=list)
    confidence: str = "LOW"
    reasons: list[str] = field(default_factory=list)

    @property
    def best(self) -> FitResult:
        return self.results[self.chosen]

    def to_dict(self):
        return {"chosen": self.chosen, "scores": self.scores, "noise_floor": self.noise_floor,
                "explanation": self.explanation, "confidence": self.confidence, "reasons": self.reasons,
                "results": {k: r.summary() for k, r in self.results.items()}}


def tracking_noise_floor(clips: list[FitClip]) -> float:
    """Mean squared normalized position noise per frame (x and y summed)."""
    vals = []
    for c in clips:
        x, y = _fill_invalid(c.ref)
        S = clip_scale(c.ref)
        sx = np.std(x - _sg(x, c.ref.fps, 0.1, order=3))
        sy = np.std(y - _sg(y, c.ref.fps, 0.1, order=3))
        # smoothing residual underestimates white noise by ~sqrt((w-1)/w); good enough
        vals.append((sx ** 2 + sy ** 2) / S ** 2)
    return float(np.mean(vals)) if vals else 0.0


def bic_score(r: FitResult, floor: float) -> float:
    mse = max(r.rss, floor, 1e-12)
    k = r.k_params + r.k_latent
    return float(r.n_eff * math.log(mse) + k * math.log(max(r.n_eff, 2.0)))


def select_model(clips: list[FitClip], keys: list[str] | None = None,
                 progress: Callable[[str, float, ControllerParams | None, float | None], None] | None = None,
                 tie_margin: float = 6.0, cross_validate: bool = False,
                 cancel: Callable[[], bool] | None = None, polish: bool = True) -> Selection:
    keys = keys or [m.key for m in CANDIDATE_MODELS]
    cov = analyse_coverage(clips)
    base = estimate_initial(clips)
    results: dict[str, FitResult] = {}
    for i, key in enumerate(keys):
        if cancel is not None and cancel():
            break
        parent = _PARENT.get(key)
        init = results[parent].params if parent in results else base
        if progress:
            progress(f"Fitting model {key} ({i + 1}/{len(keys)})", float("nan"), None, i / len(keys))
        results[key] = fit_model(clips, MODELS_BY_KEY[key], init=init, progress=progress, coverage=cov,
                                 cancel=cancel)
    if not results:
        raise RuntimeError("No model was fitted.")
    # Polish pass: local minima make single fits luck-dependent. Re-start every model from the
    # parameters of the currently best-fitting model (projected onto that model's structure)
    # and keep whichever solution is better. Makes the model comparison much more stable.
    if polish and not (cancel is not None and cancel()):
        best_key = min(results, key=lambda k: results[k].rss)
        src = results[best_key].params
        for i, key in enumerate(list(results)):
            if key == best_key or key == "A" or (cancel is not None and cancel()):
                continue
            if progress:
                progress(f"Polishing model {key}", float("nan"), None, i / len(results))
            r2 = fit_model(clips, MODELS_BY_KEY[key], init=src, progress=progress, coverage=cov,
                           cancel=cancel, extra_starts=0)
            if r2.rss < results[key].rss * 0.98:
                r2.seconds += results[key].seconds
                results[key] = r2
    floor = tracking_noise_floor(clips)
    scores = {k: bic_score(r, floor) for k, r in results.items()}
    best_score = min(scores.values())
    order = [m.key for m in CANDIDATE_MODELS if m.key in results]
    # A model is "adequate" if it is statistically tied with the best score, OR if its
    # trajectory error is practically indistinguishable from the best error (within 10 %
    # relative + 0.2 percentage points). The second rule matters because with hundreds of
    # frames even BIC on an effective sample size rewards parameters that only soak up
    # tracking bias (e.g. sprite animation) – a 0.05 % gain is not worth a parameter.
    best_err = min(r.trajectory_error for r in results.values())
    adequate = [k for k in order if scores[k] <= best_score + tie_margin
                or results[k].trajectory_error <= best_err * 1.10 + 0.002]
    # simplest adequate model (complexity = number of *fitted* parameters)
    # fewest parameters first; among equally complex models the better score wins
    chosen = min(adequate, key=lambda k: (results[k].k_params, scores[k]))

    if cross_validate and len(clips) >= 3:
        cands = sorted(adequate, key=lambda k: results[k].k_params)[:3]
        for k in cands:
            results[k].cv_error = loco_cv(clips, k, results[k].params, cancel=cancel)
        cvs = {k: results[k].cv_error for k in cands if results[k].cv_error is not None}
        if cvs:
            bestcv = min(cvs.values())
            ok = [k for k in cvs if cvs[k] <= bestcv * 1.1 + 0.002]
            chosen = min(ok, key=lambda k: (results[k].k_params, order.index(k)))

    sel = Selection(results, chosen, scores, floor)
    _explain(sel, clips)
    return sel


def loco_cv(clips: list[FitClip], key: str, init: ControllerParams, cancel=None) -> float | None:
    """Leave-one-clip-out: fit shared parameters on n-1 clips, then fit only the held-out
    clip's input timings with the parameters frozen; return the mean held-out trajectory error."""
    from .fit import fit_model as _fit
    errs = []
    model = MODELS_BY_KEY[key]
    for i in range(len(clips)):
        if cancel is not None and cancel():
            return None
        train = [c for j, c in enumerate(clips) if j != i]
        r = _fit(train, model, init=init, extra_starts=0, max_nfev=150)
        _, m = fit_inputs_only(clips[i], r.params)
        errs.append(m.values["trajectory"])
    return float(np.mean(errs))


def _pct(v):
    return "n/a" if v is None or not np.isfinite(v) else f"{100 * v:.1f}%"


def _explain(sel: Selection, clips: list[FitClip]) -> None:
    res = sel.results
    ch = sel.chosen
    best = res[ch]
    lines = []
    for k, r in res.items():
        mark = "→" if k == ch else " "
        lines.append(f"{mark} {r.model.title}: trajectory {_pct(r.trajectory_error)}, "
                     f"{r.k_params} params, score {sel.scores[k]:.0f}"
                     + (f", CV {_pct(r.cv_error)}" if r.cv_error is not None else ""))
    simpler = [k for k in res if res[k].k_params < best.k_params]
    if simpler:
        s = min(simpler, key=lambda k: sel.scores[k])
        lines.append(f"Simpler model {s} is rejected: error {_pct(res[s].trajectory_error)} vs "
                     f"{_pct(best.trajectory_error)} – the extra parameters are needed to explain the clips.")
    richer = [k for k in res if res[k].k_params > best.k_params]
    if richer:
        r = min(richer, key=lambda k: sel.scores[k])
        lines.append(f"More complex model {r} is not justified: error {_pct(res[r].trajectory_error)} "
                     f"(vs {_pct(best.trajectory_error)}) does not improve enough for its extra parameters.")
    sel.explanation = lines
    # ---- confidence
    reasons = []
    level = 2  # HIGH
    for c, m in zip(clips, best.clip_metrics):
        e = m.values.get("trajectory") or 0.0
        if e > 0.08:
            reasons.append(f"Model cannot explain clip '{c.name}' (trajectory error {_pct(e)}).")
            level = min(level, 0)
        elif e > 0.05:
            reasons.append(f"Clip '{c.name}' is only approximately explained ({_pct(e)}).")
            level = min(level, 1)
    for n, why in best.fixed.items():
        reasons.append(f"'{n}' not fitted: {why}.")
    for n, u in best.uncertainty.items():
        if u > 0.3:
            reasons.append(f"'{n}' is poorly constrained by the clips (±{100 * u:.0f}%).")
            level = min(level, 1)
    # equally complex alternatives that fit just as well -> the mechanism is ambiguous
    for k, r in res.items():
        if k != ch and r.k_params == best.k_params and r.trajectory_error <= best.trajectory_error * 1.15 + 0.002:
            reasons.append(f"Model {k} ({r.model.description.split(';')[0]}) explains the clips about as well "
                           f"({_pct(r.trajectory_error)} vs {_pct(best.trajectory_error)}): the clips do not determine "
                           f"which mechanism the original game uses. Add clips with short and long jumps to decide.")
    if "C" in res and "B" in res and ch in ("B",) and "rise_gravity" not in best.fixed:
        d = abs(res["C"].params.rise_gravity - res["C"].params.fall_gravity) / max(res["C"].params.fall_gravity, 1e-6)
        if d > 0.1:
            reasons.append("Clips do not contain enough information to distinguish constant gravity from "
                           "asymmetric gravity (B chosen; C fits similarly).")
    if not best.success:
        reasons.append("Optimizer did not report convergence (iteration limit) – result may not be optimal.")
        level = min(level, 1)
    if best.message and "worse local minima" not in best.message:
        reasons.append(best.message)
    sel.confidence = ["LOW", "MEDIUM", "HIGH"][level]
    sel.reasons = reasons
