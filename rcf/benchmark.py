"""Synthetic ground-truth benchmark: video -> tracking -> fitting -> compare with the known controller.

The fitter never sees the ground truth. The only information taken from the truth file
is what a user would provide: the character box in the first frame (drawn slightly
loose, as a person would) and the unit scale (pixels per world unit).
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np

from .model import ControllerParams, State
from .project import Clip, Project
from .sim import Scenario, InputTrack, simulate
from .synthetic.render import GROUND_TRUTH, benchmark_scenarios, generate_all, PPU
from .fitting.selection import select_model
from .fitting.metrics import clip_scale


def ensure_videos(root: Path) -> None:
    names = benchmark_scenarios().keys()
    if not all((root / f"synthetic_{n}.mp4").exists() for n in names):
        generate_all(root)


def behaviour_error(fitted: ControllerParams, truth: ControllerParams, sc: Scenario) -> float:
    """Normalized trajectory difference between two controllers driven by the same inputs."""
    t = np.arange(sc.t_start, sc.t_end, 1 / 120)
    a = simulate(truth, sc, t)
    b = simulate(fitted, sc, t)
    S = max(np.ptp(a["x"]), np.ptp(a["y"]), 1.0)
    return float(np.sqrt(np.mean((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2)) / S)


def generalization_scenarios() -> dict[str, Scenario]:
    """Input sequences NOT used for fitting – the fitted controller must still behave like the truth."""
    def sc(t_end, axis_events=(), jumps=()):
        return Scenario(0.0, t_end, State(), InputTrack(0, list(axis_events), list(jumps)), floors=[(0.0, 0.0)])
    return {
        "hold_0.3s_jump": sc(1.6, jumps=[(0.2, 0.5)]),
        "run_left_jump_back": sc(3.0, axis_events=[(0.1, -1), (1.0, 1), (2.2, 0)], jumps=[(0.8, 1.1)]),
        "tap_tap_tap": sc(3.0, axis_events=[(0.1, 1), (0.4, 0), (0.9, 1), (1.1, 0)], jumps=[(1.5, 1.58), (2.2, 2.9)]),
    }


def run(root: Path = Path("benchmark/synthetic"), method: str = "template", keys=None, log=print,
        cross_validate: bool = False, save_project: Path | None = None) -> dict:
    root = Path(root)
    ensure_videos(root)
    proj = Project()
    truths = {}
    t0 = time.time()
    for name in benchmark_scenarios():
        truth = json.loads((root / f"synthetic_{name}.truth.json").read_text())
        truths[name] = truth
        x, y, w, h = truth["bbox"][0]
        user_box = (x - 2, y - 2, w + 4, h + 3)   # a slightly loose, hand-drawn box
        clip = Clip(name, str(root / f"synthetic_{name}.mp4"), 0, len(truth["bbox"]), user_box, method=method,
                    use_camera=True, ppu=truth["ppu"])
        clip.run_tracking()
        proj.clips.append(clip)
        log(f"tracked {name}: {len(clip.track.frames)} frames, camera {clip.track.camera.level}, "
            f"lost {clip.track.lost_fraction * 100:.1f}%")
    t_track = time.time() - t0
    t1 = time.time()
    sel = select_model(proj.fit_clips(), keys=keys, cross_validate=cross_validate,
                       progress=lambda msg, cost, p, frac: log(msg) if p is None else None)
    t_fit = time.time() - t1
    best = sel.best
    if save_project is not None:
        proj.params, proj.model_key, proj.selection = best.params, sel.chosen, sel.to_dict()
        proj.save(save_project)
    gt = ControllerParams.from_dict(truths[next(iter(truths))]["params"])
    report = {"chosen": sel.chosen, "confidence": sel.confidence, "reasons": sel.reasons,
              "explanation": sel.explanation, "seconds_tracking": t_track, "seconds_fitting": t_fit,
              "models": {}, "clips": {}, "behaviour": {}, "generalization": {}, "params": {}}
    for k, r in sel.results.items():
        report["models"][k] = {"trajectory": r.trajectory_error, "overall": r.overall.overall, "k": r.k_params,
                               "score": sel.scores[k], "seconds": r.seconds}
    for c, m in zip(proj.clips, best.clip_metrics):
        report["clips"][c.name] = m.values
    for name, truth in truths.items():
        sc = Scenario.from_dict(truth["scenario"])
        report["behaviour"][name] = behaviour_error(best.params, gt, sc)
    for name, sc in generalization_scenarios().items():
        report["generalization"][name] = behaviour_error(best.params, gt, sc)
    for n in ["max_speed", "ground_accel", "ground_decel", "jump_velocity", "rise_gravity", "fall_gravity",
              "jump_cut", "air_control"]:
        fv, tv = getattr(best.params, n), getattr(gt, n)
        report["params"][n] = {"fitted": fv, "truth": tv, "rel_error": abs(fv - tv) / abs(tv)}
    report["fitted_params"] = best.params.to_dict()
    log("")
    log("Model comparison:")
    for line in sel.explanation:
        log("  " + line)
    log(f"Chosen model: {sel.chosen}  (confidence {sel.confidence})")
    log("Per-clip trajectory error vs. tracked reference:")
    for name, v in report["clips"].items():
        log(f"  {name:14s} {100 * v['trajectory']:5.2f}%")
    log("Behaviour error vs. ground-truth controller (same true inputs):")
    for name, v in report["behaviour"].items():
        log(f"  {name:14s} {100 * v:5.2f}%")
    log("Generalization (unseen input sequences):")
    for name, v in report["generalization"].items():
        log(f"  {name:20s} {100 * v:5.2f}%")
    log("Parameters:")
    for n, d in report["params"].items():
        log(f"  {n:14s} fitted {d['fitted']:8.3f}  truth {d['truth']:8.3f}  ({100 * d['rel_error']:4.1f}%)")
    log(f"time: tracking {t_track:.1f}s, fitting {t_fit:.1f}s")
    return report


if __name__ == "__main__":
    import sys
    rep = run(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("benchmark/synthetic"),
              save_project=Path("examples/synthetic.rcf.json"))
    Path("benchmark/synthetic_report.json").write_text(json.dumps(rep, indent=1, default=float))
