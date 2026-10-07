"""Headless play tests of the prototype: idle, run, stop, turn, long/short jump, air turn, walls."""
import pytest

from rcf.model import ControllerParams
from rcf.playground import Playground, Rect

DT = 1 / 60


def gt():
    return ControllerParams(max_speed=8, ground_accel=40, ground_decel=55, turn_accel=55, jump_velocity=12,
                            rise_gravity=38, fall_gravity=52, jump_cut=0.45, air_control=0.55)


def flat():
    return [Rect(-100, -5, 100, 0)]


def run(pg, seconds, axis=0, jump=False):
    for _ in range(int(round(seconds / DT))):
        pg.step(axis, jump, DT)


def test_idle_stays_put():
    pg = Playground(gt(), flat()); pg.reset()
    run(pg, 1.0)
    s = pg.player.state
    assert s.grounded and s.x == 0 and s.y == 0


def test_run_reaches_max_speed_and_stops():
    p = gt()
    pg = Playground(p, flat()); pg.reset()
    run(pg, 1.0, axis=1)
    assert pg.player.state.vx == pytest.approx(p.max_speed)
    run(pg, 1.0, axis=0)
    assert pg.player.state.vx == 0 and pg.player.state.grounded


def test_turn_around():
    pg = Playground(gt(), flat()); pg.reset()
    run(pg, 1.0, axis=1)
    run(pg, 1.0, axis=-1)
    assert pg.player.state.vx == pytest.approx(-8)


def test_long_vs_short_jump_heights():
    p = gt()
    pg = Playground(p, flat()); pg.reset()
    top = 0
    for _ in range(90):
        pg.step(0, True, DT); top = max(top, pg.player.state.y)
    assert top == pytest.approx(12 ** 2 / 76, rel=1e-3)
    run(pg, 1.0)
    assert pg.player.state.grounded
    pg2 = Playground(p, flat()); pg2.reset()
    pg2.step(0, True, DT)       # one-frame tap
    top2 = 0
    for _ in range(90):
        pg2.step(0, False, DT); top2 = max(top2, pg2.player.state.y)
    assert top2 < 0.4 * top


def test_air_turn_is_weaker_than_ground_turn():
    p = gt()
    pg = Playground(p, flat()); pg.reset()
    run(pg, 1.0, axis=1)
    pg.step(1, True, DT)
    run(pg, 0.2, axis=-1, jump=True)
    vx_air = pg.player.state.vx
    pg2 = Playground(p, flat()); pg2.reset()
    run(pg2, 1.0, axis=1)
    run(pg2, 0.2 + DT, axis=-1)
    assert vx_air > pg2.player.state.vx   # air control < 1 -> turns slower in the air


def test_wall_blocks_and_platform_landing():
    p = gt()
    level = [Rect(-100, -5, 100, 0), Rect(3, 0, 4, 5), Rect(-8, 1.2, -4, 1.4)]
    pg = Playground(p, level); pg.reset()
    run(pg, 2.0, axis=1)
    assert pg.player.state.x == pytest.approx(3 - 0.4)
    assert pg.player.state.vx == 0
    # jump onto the floating platform on the left
    pg.reset(-2.5, 0)
    pg.step(-1, True, DT)
    run(pg, 0.5, axis=-1, jump=True)
    run(pg, 1.0, axis=0)
    s = pg.player.state
    assert s.grounded and s.y == pytest.approx(1.4)


def test_running_off_a_raised_step_never_floats_at_step_height():
    # floor is probed at the NEW x: once fully past the edge the character must be falling
    level = [Rect(-100, -5, 100, 0), Rect(-2, 0, 3, 1.0)]
    pg = Playground(gt(), level); pg.reset(2.0, 1.0)
    assert pg.player.state.grounded and pg.player.state.y == 1.0
    for _ in range(60):
        pg.step(1, False, DT)
        s = pg.player.state
        if s.x - 0.4 > 3.0 + 1e-6:          # fully past the edge
            assert not (s.grounded and s.y == pytest.approx(1.0))
    assert pg.player.state.grounded and pg.player.state.y == 0.0


def test_walk_off_ledge_falls():
    level = [Rect(-100, -5, 2, 0), Rect(2, -10, 100, -3)]
    pg = Playground(gt(), level); pg.reset()
    run(pg, 1.5, axis=1)
    run(pg, 1.0)
    s = pg.player.state
    assert s.grounded and s.y == pytest.approx(-3)
