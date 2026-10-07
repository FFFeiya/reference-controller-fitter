"""Synthetic ground-truth benchmark: simulate a known controller and render gameplay video.

The rendered videos deliberately include things real footage has: a scrolling camera with
smoothing, a parallax background layer, a static HUD with a changing counter, a sprite
with squash/stretch animation (bbox changes size) and H.264 compression. The ground-truth
JSON is written next to each video and is only ever read by the benchmark evaluation,
never by the tracker or fitter.
"""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..model import ControllerParams, State
from ..sim import Scenario, InputTrack, simulate

# The benchmark controller (from the project brief).
GROUND_TRUTH = ControllerParams(max_speed=8.0, ground_accel=40.0, ground_decel=55.0, turn_accel=55.0,
                                jump_velocity=12.0, rise_gravity=38.0, fall_gravity=52.0,
                                jump_cut=0.45, air_control=0.55)

W, H = 640, 360
PPU = 32.0               # pixels per world unit
GROUND_SCREEN_Y = 300    # screen y of world y=0 when camera y = 0
FPS = 60
CHAR_W, CHAR_H = 0.8, 1.2  # world units


def benchmark_scenarios() -> dict[str, Scenario]:
    def sc(t_end, axis_events=(), jumps=()):
        return Scenario(0.0, t_end, State(), InputTrack(0, list(axis_events), list(jumps)), floors=[(0.0, 0.0)])
    return {
        "long_jump": sc(2.2, jumps=[(0.4, 1.6)]),
        "short_jump": sc(1.6, jumps=[(0.4, 0.52)]),
        "medium_jump": sc(1.8, jumps=[(0.4, 0.62)]),
        "running_jump": sc(3.4, axis_events=[(0.3, 1), (2.4, 0)], jumps=[(1.25, 1.95)]),
        "run_stop": sc(2.9, axis_events=[(0.3, 1), (1.8, 0)]),
        "turn": sc(3.6, axis_events=[(0.3, 1), (1.4, -1), (2.6, 0)]),
        "air_turn": sc(3.2, axis_events=[(0.3, 1), (1.3, -1), (2.1, 0)], jumps=[(1.0, 1.6)]),
    }


@dataclass
class World:
    fg: np.ndarray          # foreground (gameplay) layer, BGR
    fg_origin_x: int        # pixel column of world x = 0 in fg
    bg: np.ndarray          # parallax layer
    parallax: float = 0.5


def make_world(seed: int = 7, x_min: float = -30.0, x_max: float = 50.0) -> World:
    rng = np.random.default_rng(seed)
    width = int((x_max - x_min) * PPU)
    origin = int(-x_min * PPU)
    fg = np.zeros((H, width, 3), np.uint8)
    fg[:] = (0, 0, 0)
    # ground tiles (32 px), dirt with speckles and a grass edge
    gy = GROUND_SCREEN_Y
    fg[gy:, :] = (40, 90, 140)
    for _ in range(width * 2):
        x = rng.integers(0, width); y = rng.integers(gy + 6, H)
        c = int(rng.integers(20, 80))
        cv2.circle(fg, (int(x), int(y)), int(rng.integers(1, 3)), (c, c + 40, c + 80), -1)
    for tx in range(0, width, 32):
        cv2.line(fg, (tx, gy), (tx, H), (30, 70, 110), 1)
    fg[gy:gy + 6, :] = (40, 170, 60)
    # foreground decorations (bushes, posts, rocks) give the camera estimator features
    mask = np.zeros((H, width), np.uint8)
    mask[gy:, :] = 255
    for x in range(0, width, 90):
        x = int(x + rng.integers(-30, 30))
        kind = rng.integers(0, 3)
        if kind == 0:
            r = int(rng.integers(10, 22))
            cv2.circle(fg, (x, gy - r // 2), r, (30, 130, 40), -1); cv2.circle(mask, (x, gy - r // 2), r, 255, -1)
            cv2.circle(fg, (x - 4, gy - r // 2 - 4), r // 3, (60, 170, 70), -1)
        elif kind == 1:
            h = int(rng.integers(20, 50))
            cv2.rectangle(fg, (x, gy - h), (x + 6, gy), (60, 80, 110), -1); cv2.rectangle(mask, (x, gy - h), (x + 6, gy), 255, -1)
            cv2.rectangle(fg, (x - 10, gy - h - 12), (x + 16, gy - h), (90, 120, 160), -1)
            cv2.rectangle(mask, (x - 10, gy - h - 12), (x + 16, gy - h), 255, -1)
        else:
            cv2.ellipse(fg, (x, gy), (14, 8), 0, 180, 360, (110, 110, 120), -1)
            cv2.ellipse(mask, (x, gy), (14, 8), 0, 180, 360, 255, -1)
    # parallax layer: sky gradient, hills, clouds
    bw = int(width * 0.6) + W
    bg = np.zeros((H, bw, 3), np.uint8)
    for y in range(H):
        t = y / H
        bg[y, :] = (int(235 - 60 * t), int(190 - 40 * t), int(120 - 30 * t))
    for x in range(-100, bw, 160):
        cx = int(x + rng.integers(0, 80)); r = int(rng.integers(60, 130))
        cv2.circle(bg, (cx, gy + r // 3), r, (110, 150, 90), -1)
    for x in range(0, bw, 120):
        cx = int(x + rng.integers(0, 60)); cy = int(rng.integers(30, 140))
        for k in range(3):
            cv2.circle(bg, (cx + k * 18, cy + (k % 2) * 6), 16, (250, 250, 250), -1)
    # composite fg over transparent sky by keeping the mask; store mask in the alpha channel
    fg4 = np.dstack([fg, mask])
    return World(fg4, origin, bg)


def render_frame(world: World, cam_x_px: int, char_x: float, char_y: float, vy: float, grounded: bool,
                 frame_idx: int, rng: np.random.Generator) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    # background with parallax
    bx = int(round(cam_x_px * world.parallax)) + 200
    bx = max(0, min(world.bg.shape[1] - W, bx))
    frame = world.bg[:, bx:bx + W].copy()
    fx = world.fg_origin_x + cam_x_px
    fg = world.fg[:, fx:fx + W]
    m = fg[:, :, 3:4].astype(np.float32) / 255.0
    frame = (frame * (1 - m) + fg[:, :, :3] * m).astype(np.uint8)
    # character with squash & stretch, feet anchored
    stretch = 1.0 + float(np.clip(vy / 40.0, -0.18, 0.18)) if not grounded else 1.0
    cw = CHAR_W * PPU / math.sqrt(stretch)
    ch = CHAR_H * PPU * stretch
    sx = char_x * PPU - cam_x_px
    sy = GROUND_SCREEN_Y - char_y * PPU
    x0 = int(round(sx - cw / 2)); x1 = int(round(sx + cw / 2))
    y0 = int(round(sy - ch)); y1 = int(round(sy))
    cv2.rectangle(frame, (x0, y0), (x1 - 1, y1 - 1), (30, 60, 220), -1)
    cv2.rectangle(frame, (x0, y0), (x1 - 1, y1 - 1), (20, 30, 90), 2)
    eye_y = y0 + int(0.3 * (y1 - y0))
    cv2.circle(frame, (int((x0 + x1) / 2 + 4), eye_y), 4, (255, 255, 255), -1)
    cv2.circle(frame, (int((x0 + x1) / 2 + 5), eye_y), 2, (0, 0, 0), -1)
    cv2.rectangle(frame, (x0 + 3, y0 + int(0.65 * (y1 - y0))), (x1 - 4, y0 + int(0.72 * (y1 - y0))), (240, 240, 250), -1)
    # static HUD
    cv2.rectangle(frame, (8, 8), (180, 34), (0, 0, 0), -1)
    cv2.putText(frame, f"SCORE {frame_idx * 10:06d}", (14, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    # mild sensor-ish noise
    noise = rng.normal(0, 2.0, frame.shape)
    frame = np.clip(frame.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    return frame, (x0, y0, x1 - x0, y1 - y0)


def render_scenario(name: str, sc: Scenario, out_dir: Path, params: ControllerParams = GROUND_TRUTH,
                    fps: int = FPS, follow_camera: bool = True, seed: int = 0) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    world = make_world()
    n = int(round((sc.t_end - sc.t_start) * fps)) + 1
    times = sc.t_start + np.arange(n) / fps
    traj = simulate(params, sc, times)
    rng = np.random.default_rng(seed)
    video_path = out_dir / f"synthetic_{name}.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(video_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    cam = 0.0
    cams, boxes = [], []
    for i in range(n):
        px = traj["x"][i] * PPU
        if follow_camera:
            # camera keeps the player near x = 0.4*W with a dead zone and smoothing
            desired = px - 0.4 * W
            if abs(desired - cam) > 40:
                cam += (desired - cam - math.copysign(40, desired - cam)) * 0.15
        cam_px = int(round(cam))
        frame, box = render_frame(world, cam_px, traj["x"][i], traj["y"][i], traj["vy"][i],
                                  bool(traj["grounded"][i]), i, rng)
        proc.stdin.write(frame.tobytes())
        cams.append(cam_px); boxes.append(box)
    proc.stdin.close(); proc.wait()
    gt = {"name": name, "fps": fps, "ppu": PPU, "ground_screen_y": GROUND_SCREEN_Y,
          "params": params.to_dict(), "scenario": sc.to_dict(),
          "world_x": traj["x"].tolist(), "world_y": traj["y"].tolist(),
          "camera_x_px": cams, "bbox": boxes, "char_size": [CHAR_W, CHAR_H]}
    (out_dir / f"synthetic_{name}.truth.json").write_text(json.dumps(gt))
    return video_path


def generate_all(out_dir: Path) -> list[Path]:
    return [render_scenario(k, sc, out_dir, seed=i) for i, (k, sc) in enumerate(benchmark_scenarios().items())]


if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "benchmark/synthetic")
    for p in generate_all(out):
        print(p)
