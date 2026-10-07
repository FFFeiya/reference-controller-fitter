"""Optimizer, multi-clip fitting and model selection on synthetic trajectories (no video)."""
import numpy as np
import pytest

from rcf.model import ControllerParams, State, MODELS_BY_KEY
from rcf.reference import ReferenceTrajectory, infer
from rcf.sim import Scenario, InputTrack, simulate
from rcf.fitting.fit import FitClip, fit_model, analyse_coverage, estimate_initial
from rcf.fitting.selection import select_model
from rcf.fitting.metrics import compute_metrics

GT_D = ControllerParams(max_speed=8, ground_accel=40, ground_decel=55, turn_accel=55, jump_velocity=12,
                        rise_gravity=38, fall_gravity=52, jump_cut=0.45, air_control=0.55)
# a model-B controller: finite accel, constant gravity, no variable jump, full air control
GT_B = ControllerParams(max_speed=6, ground_accel=30, ground_decel=30, turn_accel=30, jump_velocity=10,
                        rise_gravity=35, fall_gravity=35)


def sc(t_end, axis=(), jumps=()):
    return Scenario(0.0, t_end, State(), InputTrack(0, list(axis), list(jumps)), floors=[(0.0, 0.0)])


SCENARIOS = {
    "long": sc(2.2, jumps=[(0.4, 1.6)]),
    "short": sc(1.6, jumps=[(0.4, 0.52)]),
    "running": sc(3.4, axis=[(0.3, 1), (2.4, 0)], jumps=[(1.25, 1.95)]),
    "turn": sc(3.6, axis=[(0.3, 1), (1.4, -1), (2.6, 0)]),
    "air_turn": sc(3.2, axis=[(0.3, 1), (1.3, -1), (2.1, 0)], jumps=[(1.0, 1.6)]),
}


def make_clips(p, names, fps=60.0, noise=0.006, seed=0):
    rng = np.random.default_rng(seed)
    clips = []
    for n in names:
        s = SCENARIOS[n]
        t = np.arange(s.t_start, s.t_end, 1 / fps)
        r = simulate(p, s, t)
        x = r["x"] + rng.normal(0, noise, len(t)); y = r["y"] + rng.normal(0, noise, len(t))
        ref = ReferenceTrajectory(t, x - x[0], y - y[0], np.ones(len(t), bool), fps, 1.2)
        inf = infer(ref)
        clips.append(FitClip(n, ref, inf, inf.scenario))
    return clips


def test_initial_estimates_are_in_the_right_ballpark():
    p0 = estimate_initial(make_clips(GT_D, ["long", "running"]))
    assert 0.8 < p0.max_speed / 8 < 1.2
    assert 0.6 < p0.jump_velocity / 12 < 1.5
    assert 0.6 < p0.rise_gravity / 38 < 1.6


def test_multi_clip_fit_recovers_ground_truth_behaviour():
    clips = make_clips(GT_D, list(SCENARIOS))
    r = fit_model(clips, MODELS_BY_KEY["D"])
    assert r.trajectory_error < 0.01
    for n, tol in (("max_speed", 0.03), ("jump_velocity", 0.05), ("rise_gravity", 0.08),
                   ("fall_gravity", 0.08), ("jump_cut", 0.15), ("air_control", 0.15)):
        assert abs(getattr(r.params, n) / getattr(GT_D, n) - 1) < tol, n
    # parameters are shared: one set for all clips
    assert len(r.scenarios) == len(clips)


def test_selection_prefers_simple_model_for_simple_data():
    clips = make_clips(GT_B, ["long", "running", "turn"])
    sel = select_model(clips, keys=["A", "B", "C", "D", "E"])
    assert sel.chosen == "B", sel.explanation
    assert sel.best.trajectory_error < 0.02


def test_selection_needs_variable_jump_for_short_and_long_jumps():
    clips = make_clips(GT_D, list(SCENARIOS))
    sel = select_model(clips, keys=["A", "B", "C", "D", "E"])
    assert sel.chosen == "D", sel.explanation
    assert sel.results["C"].trajectory_error > 2 * sel.results["D"].trajectory_error


def test_unidentifiable_parameters_are_reported_not_guessed():
    clips = make_clips(GT_D, ["long"])        # one jump, no running, no air input
    cov = analyse_coverage(clips)
    fixed = cov.fixed_params()
    assert "jump_cut" in fixed and "air_control" in fixed and "max_speed" in fixed
    r = fit_model(clips, MODELS_BY_KEY["D"])
    assert "jump_cut" in r.fixed and r.params.jump_cut == 1.0


def test_model_cannot_explain_clip_gives_low_confidence():
    clips = make_clips(GT_D, ["long", "running"])
    # corrupt one clip: teleport halfway (e.g. tracking jumped to another object)
    c = clips[1]
    c.ref.y[len(c.ref.y) // 2:] += 3.0
    sel = select_model(clips, keys=["A", "B", "C"])
    assert sel.confidence == "LOW"
    assert any("cannot explain" in r for r in sel.reasons)


def test_metrics_are_zero_for_perfect_model():
    clips = make_clips(GT_D, ["running"], noise=0.0)
    from rcf.sim import Scenario as S
    m = compute_metrics(GT_D, SCENARIOS["running"], clips[0].ref, clips[0].inference)
    assert m.values["trajectory"] < 1e-6
    assert m.values["apex_timing"] < 0.01 and m.values["max_height"] < 0.01
