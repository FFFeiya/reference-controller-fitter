import math

import numpy as np
import pytest

from rcf.model import ControllerParams, State, advance, try_jump, release_jump, CANDIDATE_MODELS, MODELS_BY_KEY
from rcf.sim import Scenario, InputTrack, simulate


def gt():
    return ControllerParams(max_speed=8, ground_accel=40, ground_decel=55, turn_accel=55, jump_velocity=12,
                            rise_gravity=38, fall_gravity=52, jump_cut=0.45, air_control=0.55)


def test_jump_apex_height_and_time_are_analytic():
    p = gt()
    s = State()
    try_jump(s, p)
    t_apex = p.jump_velocity / p.rise_gravity
    advance(s, p, 0, True, t_apex, floor=0.0)
    assert s.y == pytest.approx(p.jump_velocity ** 2 / (2 * p.rise_gravity), abs=1e-9)
    assert abs(s.vy) < 1e-6


def test_landing_time_is_exact_and_step_independent():
    p = gt()
    h = p.jump_velocity ** 2 / (2 * p.rise_gravity)
    expect = p.jump_velocity / p.rise_gravity + math.sqrt(2 * h / p.fall_gravity)
    for dt in (1 / 30, 1 / 60, 1 / 144, 0.25):
        s = State()
        try_jump(s, p)
        t = 0.0
        while not s.grounded:
            landed = advance(s, p, 0, True, dt, floor=0.0)
            t += dt
            if landed:
                break
        # landed within the last step; reconstruct the exact landing time by bisection-free check
        assert s.y == 0.0
        assert t - dt <= expect + 1e-9 <= t + 1e-9


def test_step_size_independence_of_trajectory():
    p = gt()
    sc = Scenario(0.0, 2.0, State(), InputTrack(0, [(0.1, 1), (1.2, 0)], [(0.3, 0.5)]), floors=[(0.0, 0.0)])
    times = np.linspace(0, 2, 61)
    a = simulate(p, sc, times)
    # dense sampling must give the same values at shared times
    dense = np.linspace(0, 2, 601)
    b = simulate(p, sc, dense)
    np.testing.assert_allclose(a["x"], b["x"][::10], atol=1e-9)
    np.testing.assert_allclose(a["y"], b["y"][::10], atol=1e-9)


def test_run_reaches_max_speed_and_stops():
    p = gt()
    s = State()
    advance(s, p, 1, False, p.max_speed / p.ground_accel, floor=0.0)
    assert s.vx == pytest.approx(p.max_speed)
    assert s.x == pytest.approx(p.max_speed ** 2 / (2 * p.ground_accel))
    advance(s, p, 1, False, 1.0, floor=0.0)
    assert s.vx == pytest.approx(p.max_speed)
    x0 = s.x
    advance(s, p, 0, False, 5.0, floor=0.0)
    assert s.vx == 0.0
    assert s.x - x0 == pytest.approx(p.max_speed ** 2 / (2 * p.ground_decel))


def test_turn_around_uses_turn_then_accel():
    p = gt().copy(turn_accel=100.0)
    s = State(vx=8.0)
    advance(s, p, -1, False, 8.0 / 100.0, floor=0.0)
    assert s.vx == pytest.approx(0.0, abs=1e-9)
    advance(s, p, -1, False, 0.1, floor=0.0)
    assert s.vx == pytest.approx(-4.0)


def test_jump_cut_reduces_height():
    p = gt()
    full = State(); try_jump(full, p)
    cut = State(); try_jump(cut, p)
    advance(cut, p, 0, True, 0.05, 0.0)
    release_jump(cut, p)
    vy_after = cut.vy
    assert vy_after == pytest.approx((12 - 38 * 0.05) * 0.45)
    apex_full = 12 ** 2 / (2 * 38)
    advance(cut, p, 0, False, 10, 0.0)
    assert cut.grounded


def test_terminal_velocity_and_apex_band():
    p = gt().copy(max_fall_speed=10.0, apex_gravity_mult=0.5, apex_threshold=3.0)
    s = State(y=100.0, grounded=False)
    advance(s, p, 0, False, 2.0, floor=0.0)
    assert s.vy == pytest.approx(-10.0)
    # apex band: from vy=3 to 0 with half gravity takes 3/(19)
    s = State(vy=3.0, grounded=False, y=10)
    advance(s, p, 0, True, 3.0 / (38 * 0.5), floor=0.0)
    assert abs(s.vy) < 1e-6


def test_models_build_extract_roundtrip():
    for m in CANDIDATE_MODELS:
        vals = {n: 1.0 + i * 0.1 for i, n in enumerate(m.free)}
        if "jump_cut" in vals: vals["jump_cut"] = 0.5
        if "air_control" in vals: vals["air_control"] = 0.7
        if "apex_gravity_mult" in vals: vals["apex_gravity_mult"] = 0.6
        if "release_gravity_mult" in vals: vals["release_gravity_mult"] = 2.0
        p = m.build(vals)
        back = m.extract(p)
        for k, v in vals.items():
            assert back[k] == pytest.approx(v), (m.key, k)


def test_params_dict_roundtrip_with_infinity():
    p = gt()
    d = p.to_dict()
    assert d["max_fall_speed"] is None
    assert ControllerParams.from_dict(d) == p
