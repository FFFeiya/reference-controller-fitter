"""Developer diagnostics: track one clip and write a PNG with the track overlay,
world trajectory, inferred phases/inputs, camera path and (optionally) a fit.

Usage:
  python tools/diagnose_clip.py VIDEO START END X Y W H OUT.png [--fit MODEL] [--char-height 1.0] [--no-camera]
"""
import argparse
import sys
from pathlib import Path

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rcf.project import Clip  # noqa: E402
from rcf.tracking.video import iter_frames  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("start", type=int); ap.add_argument("end", type=int)
    ap.add_argument("x", type=float); ap.add_argument("y", type=float); ap.add_argument("w", type=float); ap.add_argument("h", type=float)
    ap.add_argument("out")
    ap.add_argument("--fit", default=None)
    ap.add_argument("--method", default="template")
    ap.add_argument("--char-height", type=float, default=1.0)
    ap.add_argument("--no-camera", action="store_true")
    a = ap.parse_args()
    clip = Clip("diag", a.video, a.start, a.end, (a.x, a.y, a.w, a.h), a.method, not a.no_camera,
                char_height_units=a.char_height)
    clip.run_tracking()
    tr = clip.track
    ref = clip.reference
    inf = clip.inference
    print("camera", tr.camera.level, round(tr.camera.overall_confidence, 2), "lost", tr.lost_fraction, tr.notes)
    print("phases:", [(round(p.t_off, 2), p.t_land and round(p.t_land, 2), p.kind, round(p.y_apex - p.y_off, 2)) for p in inf.phases])
    sc = inf.scenario
    print("axis0", sc.inputs.axis0, "axis", [(round(t, 2), v) for t, v in sc.inputs.axis_events])
    print("jumps", [(round(p, 2), round(r, 2)) for p, r in sc.inputs.jumps], "ledges", sc.ledges)
    print("notes", inf.notes, inf.warnings)
    fit = None
    if a.fit:
        from rcf.fitting.fit import fit_model
        from rcf.model import MODELS_BY_KEY
        from rcf.sim import simulate
        fc = clip.project_fit_clip() if hasattr(clip, "project_fit_clip") else None
        from rcf.fitting.fit import FitClip
        fc = FitClip("diag", ref, inf, sc)
        r = fit_model([fc], MODELS_BY_KEY[a.fit])
        print("fit", a.fit, "traj", round(r.trajectory_error * 100, 2), "%", {k: round(v, 3) for k, v in r.params.to_dict().items() if v is not None})
        print("metrics", {k: (None if v is None else round(v * 100, 2)) for k, v in r.overall.values.items()})
        fit = simulate(r.params, r.scenarios[0], ref.t)
    # figure
    frames = {i: f for i, f in iter_frames(a.video, a.start, a.end) if i in (a.start, (a.start + a.end) // 2)}
    fig = plt.figure(figsize=(16, 10))
    ax0 = fig.add_subplot(2, 2, 1)
    f0 = cv2.cvtColor(frames[a.start], cv2.COLOR_BGR2RGB)
    ax0.imshow(f0)
    foot = tr.foot_screen()
    ax0.plot(foot[:, 0], foot[:, 1], "y-", lw=1)
    x, y, w, h = tr.bbox[0]
    ax0.add_patch(plt.Rectangle((x, y), w, h, fill=False, ec="r"))
    ax0.set_title("first frame + screen-space foot path")
    ax1 = fig.add_subplot(2, 2, 2)
    ax1.plot(ref.x, ref.y, "k.", ms=3, label="reference")
    if fit is not None:
        ax1.plot(fit["x"], fit["y"], "r-", label="fit")
    ax1.set_aspect("equal"); ax1.legend(); ax1.set_title("world trajectory (units)")
    ax2 = fig.add_subplot(2, 2, 3)
    ax2.plot(ref.t, ref.x, "b.", ms=3, label="x")
    ax2.plot(ref.t, ref.y, "g.", ms=3, label="y")
    if fit is not None:
        ax2.plot(ref.t, fit["x"], "b-", lw=1); ax2.plot(ref.t, fit["y"], "g-", lw=1)
    g = inf.grounded
    ax2.fill_between(ref.t, ref.y.min(), ref.y.max(), where=g, color="0.85", step="mid", label="grounded")
    for t, v in sc.inputs.axis_events:
        ax2.axvline(t, color="b", ls=":", lw=0.8)
    for p, r_ in sc.inputs.jumps:
        ax2.axvspan(p, r_, color="orange", alpha=0.3)
    ax2.legend(); ax2.set_title("x/y vs t; orange=jump held, dotted=axis events")
    ax3 = fig.add_subplot(2, 2, 4)
    ax3.plot(ref.t, tr.camera.cam_x[:len(ref.t)], label="cam x px")
    ax3.plot(ref.t, tr.camera.cam_y[:len(ref.t)], label="cam y px")
    ax3b = ax3.twinx(); ax3b.plot(ref.t, tr.camera.confidence[:len(ref.t)], "k:", lw=0.8); ax3b.set_ylim(0, 1.05)
    ax3b.plot(ref.t, tr.confidence, "r:", lw=0.8)
    ax3.legend(); ax3.set_title("camera (px) + confidence (black: camera, red: track)")
    fig.tight_layout(); fig.savefig(a.out, dpi=80)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
