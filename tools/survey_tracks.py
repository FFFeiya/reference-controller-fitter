"""Track whole segments, cache the tracks (JSON) and plot world trajectories for choosing clip ranges.

Usage: python tools/survey_tracks.py OUT_DIR  VIDEO:X,Y,W,H [VIDEO:X,Y,W,H ...]
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rcf.tracking.pipeline import track_clip, TrackResult  # noqa: E402
from rcf.tracking.video import probe  # noqa: E402
from rcf.reference import trajectory_from_track, segment_grounded  # noqa: E402


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    for spec in sys.argv[2:]:
        video, box = spec.rsplit(":", 1)
        box = tuple(float(v) for v in box.split(","))
        name = Path(video).stem
        cache = out / f"{name}.track.json"
        if cache.exists():
            tr = TrackResult.from_dict(json.loads(cache.read_text()))
        else:
            info = probe(video)
            tr = track_clip(video, 0, info.frame_count, box)
            cache.write_text(json.dumps(tr.to_dict()))
        ref, ppu = trajectory_from_track(tr)
        g = segment_grounded(ref)
        fig, ax = plt.subplots(2, 1, figsize=(18, 8))
        ax[0].plot(tr.frames, ref.x, "b.", ms=3); ax[0].set_ylabel("x units")
        ax[1].plot(tr.frames, ref.y, "g.", ms=3); ax[1].set_ylabel("y units")
        ax[1].fill_between(tr.frames, ref.y.min(), ref.y.max(), where=g, color="0.85", step="mid")
        lost = tr.lost
        ax[1].plot(tr.frames[lost], ref.y[lost], "rx", ms=5)
        for a in ax:
            a.set_xticks(np.arange(tr.frames[0], tr.frames[-1], 10), minor=True)
            a.grid(True, which="both", alpha=0.3)
        fig.suptitle(f"{name}: camera {tr.camera.level}, lost {tr.lost_fraction*100:.1f}%, ppu {ppu:.1f}")
        fig.tight_layout(); fig.savefig(out / f"{name}.png", dpi=70)
        print(name, "frames", len(tr.frames), "camera", tr.camera.level, "lost %.1f%%" % (100 * tr.lost_fraction))


if __name__ == "__main__":
    main()
