"""Trajectory normalization and input/phase inference."""
import numpy as np
import pytest

from rcf.model import ControllerParams, State
from rcf.reference import ReferenceTrajectory, trajectory_from_track, infer, decode_horizontal
from rcf.sim import Scenario, InputTrack, simulate
from rcf.tracking.camera import CameraTrack
from rcf.tracking.pipeline import TrackResult

GT = ControllerParams(max_speed=8, ground_accel=40, ground_decel=55, turn_accel=55, jump_velocity=12,
                      rise_gravity=38, fall_gravity=52, jump_cut=0.45, air_control=0.55)


def _ref(sc, fps=60.0, noise=0.01, seed=0):
    t = np.arange(sc.t_start, sc.t_end, 1 / fps)
    s = simulate(GT, sc, t)
    rng = np.random.default_rng(seed)
    x = s["x"] + rng.normal(0, noise, len(t))
    y = s["y"] + rng.normal(0, noise, len(t))
    return ReferenceTrajectory(t, x - x[0], y - y[0], np.ones(len(t), bool), fps, 1.2)


def sc(t_end, axis=(), jumps=()):
    return Scenario(0.0, t_end, State(), InputTrack(0, list(axis), list(jumps)), floors=[(0.0, 0.0)])


def test_trajectory_from_track_applies_camera_scale_and_flips_y():
    n = 5
    frames = np.arange(n)
    # player stands still on screen while the camera pans right 10 px/frame -> moves right in the world
    bbox = np.array([[100, 200, 20, 40]] * n, float)
    bbox[:, 1] -= np.arange(n) * 4      # rises on screen (y down) by 4 px/frame
    cam = CameraTrack(np.arange(n) * 10.0, np.zeros(n), np.ones(n))
    tr = TrackResult("v", 30.0, 0, frames, bbox, np.ones(n), np.zeros(n, bool), cam, "template")
    ref, ppu = trajectory_from_track(tr, ppu=20.0)
    assert ppu == 20.0
    np.testing.assert_allclose(ref.x, np.arange(n) * 0.5)
    np.testing.assert_allclose(ref.y, np.arange(n) * 0.2)
    np.testing.assert_allclose(ref.t, frames / 30.0)
    assert ref.char_height == pytest.approx(2.0)
    # without camera compensation the screen motion is taken literally
    ref2, _ = trajectory_from_track(tr, ppu=20.0, use_camera=False)
    np.testing.assert_allclose(ref2.x, 0.0)
    # default scale: character height = 1 unit
    _, ppu3 = trajectory_from_track(tr)
    assert ppu3 == pytest.approx(40.0)


def test_infer_recovers_jumps_and_axis_events():
    s = sc(3.4, axis=[(0.3, 1), (2.4, 0)], jumps=[(1.25, 1.95)])
    inf = infer(_ref(s))
    i = inf.scenario.inputs
    assert len(i.jumps) == 1 and abs(i.jumps[0][0] - 1.25) < 0.02
    vals = [a for _, a in i.axis_events]
    assert vals == [1, 0]
    assert abs(i.axis_events[0][0] - 0.3) < 0.12 and abs(i.axis_events[1][0] - 2.4) < 0.12
    assert [p.kind for p in inf.phases] == ["jump"]


def test_infer_detects_turn():
    s = sc(3.6, axis=[(0.3, 1), (1.4, -1), (2.6, 0)])
    i = infer(_ref(s)).scenario.inputs
    assert [a for _, a in i.axis_events] == [1, -1, 0]


def test_no_jump_reports_note():
    inf = infer(_ref(sc(2.0, axis=[(0.2, 1)])))
    assert not inf.scenario.inputs.jumps
    assert any("No jump" in n for n in inf.notes)


def test_bunny_hop_contact_is_split_into_two_jumps():
    s = sc(2.5, jumps=[(0.2, 0.9)])
    t = np.arange(0, 2.5, 1 / 30)
    sim = simulate(GT, s, t)
    # land and immediately jump again: find landing then re-press the next frame
    land = t[np.argmax((t > 0.4) & sim["grounded"])]
    s2 = sc(2.5, jumps=[(0.2, 0.9), (land + 1 / 60, land + 0.8)])
    inf = infer(_ref(s2, fps=30.0))
    assert sum(p.kind == "jump" for p in inf.phases) == 2


def test_animation_blip_is_not_a_jump():
    r = _ref(sc(1.5, axis=[(0.2, 1)]), fps=30.0, noise=0.002)
    r.y[10:12] += 0.13      # feet estimate wobbles when the sprite switches animation
    inf = infer(r)
    assert not [p for p in inf.phases if p.kind == "jump"]


def test_decode_horizontal_is_robust_to_speed_ripple():
    fps = 30.0
    t = np.arange(0, 3, 1 / fps)
    vx = np.clip(t * 20, 0, 7.5)
    vx = vx + 0.8 * np.sin(t * 40)       # frame-drop style ripple
    u = decode_horizontal(vx, fps, 1.0)
    changes = np.count_nonzero(np.diff(u))
    assert changes <= 2 and u[-1] == 1
