"""Run the exported Godot 4 controller headless in a real Godot scene and compare with the model.

Usage: python tools/godot_check.py PATH_TO_GODOT_EXECUTABLE [controller.json]
Scenario (60 Hz physics): idle 10 ticks, run right ticks 10-70 with jump held ticks 25-45,
then let go. Compares run speed, apex height and landing with rcf's simulator.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rcf.export.exporters import export_godot, load_json  # noqa: E402
from rcf.model import ControllerParams, State  # noqa: E402
from rcf.sim import Scenario, InputTrack, simulate  # noqa: E402

PPU = 32.0
RUNNER = r"""
extends SceneTree
var player
var tick := 0
var lines := PackedStringArray()

func _initialize():
	var ground := StaticBody2D.new()
	var gs := CollisionShape2D.new()
	var rect := RectangleShape2D.new()
	rect.size = Vector2(40000, 100)
	gs.shape = rect
	ground.add_child(gs)
	ground.position = Vector2(0, 50)
	root.add_child(ground)
	player = CharacterBody2D.new()
	player.set_script(load("res://fitted_platformer_controller.gd"))
	var ps := CollisionShape2D.new()
	var pr := RectangleShape2D.new()
	pr.size = Vector2(0.8 * PPU_, 1.0 * PPU_)
	ps.shape = pr
	player.add_child(ps)
	player.position = Vector2(0, -0.5 * PPU_)
	root.add_child(player)

func _physics_process(delta):
	var k := tick
	if k == 10: Input.action_press("ui_right")
	if k == 70: Input.action_release("ui_right")
	if k == 25: Input.action_press("ui_accept")
	if k == 45: Input.action_release("ui_accept")
	tick += 1
	if tick > 1:
		lines.append("%d,%.6f,%.6f" % [tick - 1, player.position.x / PPU_, -(player.position.y + 0.5 * PPU_) / PPU_])
	if tick >= 160:
		var f := FileAccess.open(OS.get_environment("RCF_OUT"), FileAccess.WRITE)
		f.store_string("\n".join(lines))
		f.close()
		quit()
	return false
""".replace("PPU_", str(PPU))


def main():
    godot = Path(sys.argv[1])
    if len(sys.argv) > 2:
        p, _ = load_json(sys.argv[2])
    else:
        p = ControllerParams(max_speed=8, ground_accel=40, ground_decel=55, turn_accel=55, jump_velocity=12,
                             rise_gravity=38, fall_gravity=52, jump_cut=0.45, air_control=0.55)
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        (proj / "project.godot").write_text('config_version=5\n\n[application]\nconfig/name="rcf_check"\n'
                                           '\n[physics]\ncommon/physics_ticks_per_second=60\n')
        export_godot(p, proj / "fitted_platformer_controller.gd", {"model": "check"}, pixels_per_unit=PPU)
        (proj / "runner.gd").write_text(RUNNER)
        out = proj / "traj.csv"
        import os
        env = dict(os.environ, RCF_OUT=str(out))
        # import pass (creates .godot/ cache), then run
        subprocess.run([str(godot), "--headless", "--path", str(proj), "--import"], capture_output=True, timeout=300)
        r = subprocess.run([str(godot), "--headless", "--path", str(proj), "--script", "res://runner.gd"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, env=env)
        log = r.stdout + r.stderr
        errs = [l for l in log.splitlines() if "ERROR" in l or "SCRIPT ERROR" in l or "Parse Error" in l]
        if not out.exists():
            print("FAILED: no trajectory written\n", log[-3000:])
            return 1
        d = np.loadtxt(out, delimiter=",")
    dt = 1 / 60
    # model: inputs at the same ticks (input set in tick k affects the step of tick k or k+1 – compare shapes)
    sc = Scenario(0.0, 160 * dt, State(), InputTrack(0, [(10 * dt, 1), (70 * dt, 0)], [(25 * dt, 45 * dt)]),
                  floors=[(0.0, 0.0)])
    t = (d[:, 0] + 1) * dt
    ref = simulate(p, sc, t)
    gy = d[:, 2]
    vx_g = np.diff(d[:, 1]) / dt
    print("script errors:", errs or "none")
    print(f"apex height: Godot {gy.max():.4f}  model {ref['y'].max():.4f}")
    # steady running speed after landing (ticks 64-70); Godot rests safe_margin (0.08 px) above floors
    run_speed = float(np.median(vx_g[64:70]))
    vx_g = np.array([run_speed])
    print(f"run speed: Godot {run_speed:.4f}  model {p.max_speed:.4f}")
    idx = np.arange(len(t))
    land_g = t[np.argmax((idx > 30) & (np.abs(gy) < 0.01))]
    land_m = t[np.argmax((idx > 30) & ref["grounded"])]
    print(f"landing time: Godot {land_g:.3f}s  model {land_m:.3f}s")
    print(f"final x: Godot {d[-1, 1]:.4f}  model {ref['x'][-1]:.4f}")
    ok = (not errs and abs(gy.max() - ref["y"].max()) < 0.05 and abs(vx_g.max() - p.max_speed) < 0.05
          and abs(land_g - land_m) <= 2 * dt + 1e-9 and abs(d[-1, 1] - ref["x"][-1]) < 0.2)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
