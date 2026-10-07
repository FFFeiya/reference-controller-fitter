"""Record the Godot part of the project video.

Usage: python tools/video/record_godot.py WORK_DIR STAGE_DIR GODOT_EXE
STAGE_DIR must contain fitted_platformer_controller.gd. Produces WORK_DIR/godot_shots/*.png
(editor window, captured in the background) and WORK_DIR/godot_frames/*.png (Movie Maker render).
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wincap  # noqa: E402

PPU = 48.0          # Godot pixels per unit (character = 48 px tall)
ZOOM = 1.875        # camera zoom -> 90 screen px per unit, same framing as the Unity demo


def tscn(level, names):
    out = ['[gd_scene format=3]', '',
           '[ext_resource type="Script" path="res://fitted_platformer_controller.gd" id="1_ctrl"]', '']
    for i, (x0, y0, x1, y1) in enumerate(level):
        out += [f'[sub_resource type="RectangleShape2D" id="shape_{i}"]',
                f'size = Vector2({(x1 - x0) * PPU:.1f}, {(y1 - y0) * PPU:.1f})', '']
    out += ['[sub_resource type="RectangleShape2D" id="shape_player"]', f'size = Vector2({0.8 * PPU:.1f}, {PPU:.1f})', '']
    out += ['[node name="Main" type="Node2D"]', '', '[node name="Level" type="Node2D" parent="."]', '']
    for i, ((x0, y0, x1, y1), n) in enumerate(zip(level, names)):
        cx, cy = (x0 + x1) / 2 * PPU, -(y0 + y1) / 2 * PPU
        w, h = (x1 - x0) * PPU, (y1 - y0) * PPU
        out += [f'[node name="{n}" type="StaticBody2D" parent="Level"]', f'position = Vector2({cx:.1f}, {cy:.1f})', '',
                f'[node name="CollisionShape2D" type="CollisionShape2D" parent="Level/{n}"]', f'shape = SubResource("shape_{i}")', '',
                f'[node name="Visual" type="ColorRect" parent="Level/{n}"]',
                f'offset_left = {-w / 2:.1f}', f'offset_top = {-h / 2:.1f}', f'offset_right = {w / 2:.1f}', f'offset_bottom = {h / 2:.1f}',
                'color = Color(0.30, 0.34, 0.44, 1)', '']
    out += ['[node name="Player" type="CharacterBody2D" parent="."]', f'position = Vector2(0, {-0.5 * PPU:.1f})',
            'script = ExtResource("1_ctrl")', f'pixels_per_unit = {PPU:.1f}', '',
            '[node name="CollisionShape2D" type="CollisionShape2D" parent="Player"]', 'shape = SubResource("shape_player")', '',
            '[node name="Visual" type="ColorRect" parent="Player"]',
            f'offset_left = {-0.4 * PPU:.1f}', f'offset_top = {-0.5 * PPU:.1f}', f'offset_right = {0.4 * PPU:.1f}',
            f'offset_bottom = {0.5 * PPU:.1f}', 'color = Color(1, 1, 1, 1)', '',
            '[node name="Camera2D" type="Camera2D" parent="Player"]', f'position = Vector2({3 * PPU:.1f}, {-2 * PPU:.1f})',
            f'zoom = Vector2({ZOOM}, {ZOOM})', 'position_smoothing_enabled = true', 'position_smoothing_speed = 6.0', '']
    return "\n".join(out)


PROJECT = """config_version=5

[application]
config/name="RCF Godot Demo"
run/main_scene="res://main.tscn"

[autoload]
DemoInput="*res://demo/demo_input.gd"

[display]
window/size/viewport_width=1920
window/size/viewport_height=1080

[editor_plugins]
enabled=PackedStringArray("res://addons/rcf_video/plugin.cfg")

[physics]
common/physics_ticks_per_second=60

[rendering]
environment/defaults/default_clear_color=Color(0.12, 0.13, 0.17, 1)
"""

PLUGIN_CFG = """[plugin]
name="RCF Video"
description="Drives the editor for the project video"
author="rcf"
version="1.0"
script="plugin.gd"
"""


def build(proj: Path, stage: Path):
    ch = json.loads((HERE / "choreo.json").read_text())
    if proj.exists():
        shutil.rmtree('\\\\?\\' + str(proj.resolve()))  # shader cache paths exceed MAX_PATH
    (proj / "demo").mkdir(parents=True)
    (proj / "addons" / "rcf_video").mkdir(parents=True)
    (proj / "project.godot").write_text(PROJECT)
    shutil.copy(stage / "fitted_platformer_controller.gd", proj / "fitted_platformer_controller.gd")
    (proj / "main.tscn").write_text(tscn(ch["level"], ch["names"]))
    (proj / "demo" / "events.txt").write_text("\n".join(f"{e['t']} {e['axis']} {int(e['jump'])}" for e in ch["events"]))
    shutil.copy(HERE / "godot" / "demo_input.gd", proj / "demo" / "demo_input.gd")
    shutil.copy(HERE / "godot" / "plugin.gd", proj / "addons" / "rcf_video" / "plugin.gd")
    (proj / "addons" / "rcf_video" / "plugin.cfg").write_text(PLUGIN_CFG)
    return ch["duration"]


def main():
    work = Path(sys.argv[1]); stage = Path(sys.argv[2]); godot = Path(sys.argv[3])
    proj = work / "RCF_Godot_Demo"
    signal, frames, shots = work / "godot_signal", work / "godot_frames", work / "godot_shots"
    for d in (signal, frames, shots):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    duration = build(proj, stage)
    print("importing…", flush=True)
    subprocess.run([str(godot), "--headless", "--path", str(proj), "--import"], capture_output=True, timeout=600)
    # 1) gameplay with Movie Maker (deterministic 30 fps), window parked off-screen
    env = dict(os.environ, RCF_DURATION=str(duration), RCF_LOG=str(frames / "frames.csv"))
    print("movie maker…", flush=True)
    r = subprocess.run([str(godot), "--path", str(proj), "--write-movie", str(frames / "f.png"), "--fixed-fps", "30",
                        "--resolution", "1920x1080", "--position", "-6000,0", "res://main.tscn"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200, env=env)
    errs = [l for l in (r.stdout + r.stderr).splitlines() if "ERROR" in l]
    print("frames:", len(list(frames.glob("f*.png"))), "errors:", errs[:5], flush=True)
    # 2) editor walkthrough, captured in the background
    env = dict(os.environ, RCF_SIGNAL=str(signal), RCF_DURATION=str(duration))
    print("editor…", flush=True)
    proc = subprocess.Popen([str(godot), "-e", "--path", str(proj), "--resolution", "1920x1080", "--position", "0,0"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hwnd, done, t0 = None, set(), time.time()
    while proc.poll() is None and time.time() - t0 < 600:
        if hwnd is None:
            wins = [w for w in wincap.find_windows("RCF Godot Demo") if wincap.client_size(w[0])[0] > 800]
            if wins:
                hwnd = wins[0][0]
                print("editor window:", wins[0][1], flush=True)
        if hwnd is not None:
            wincap.park_behind(hwnd, 0, 0, 1920, 1080)
        for f in sorted(signal.glob("ready_*.txt")):
            if f.name in done:
                continue
            name = f.read_text().strip()
            img = wincap.grab(hwnd) if hwnd else None
            if img is not None:
                cv2.imwrite(str(shots / f"{f.stem.split('_')[1]}_{name}.png"), img)
            print("shot", f.stem, name, None if img is None else img.shape, flush=True)
            (signal / f.name.replace("ready_", "shot_")).write_text("ok")
            done.add(f.name)
        time.sleep(0.25)
    if proc.poll() is None:
        proc.kill()
    print("finished:", (signal / "finished.txt").exists())


if __name__ == "__main__":
    main()
