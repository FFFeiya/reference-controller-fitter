"""Compare tracker methods and camera estimation on the synthetic benchmark (ground-truth boxes).

Usage: python tools/eval_trackers.py [benchmark/synthetic] [methods...]
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rcf.tracking.pipeline import track_clip  # noqa: E402
from rcf.tracking.tracker import TRACKER_METHODS  # noqa: E402


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("benchmark/synthetic")
    methods = sys.argv[2:] or TRACKER_METHODS
    rows = []
    for truth_path in sorted(root.glob("*.truth.json")):
        gt = json.loads(truth_path.read_text())
        video = str(truth_path).replace(".truth.json", ".mp4")
        tb = np.array(gt["bbox"], float)
        tfoot = np.stack([tb[:, 0] + tb[:, 2] / 2, tb[:, 1] + tb[:, 3]], 1)
        tcam = np.array(gt["camera_x_px"], float)
        for m in methods:
            t0 = time.time()
            r = track_clip(video, 0, len(tb), tb[0], method=m, camera=True)
            dt = time.time() - t0
            n = min(len(r.bbox), len(tb))
            foot = r.foot_screen()[:n]
            # motion error: remove the constant offset (box convention) using the median offset
            off = np.median(foot - tfoot[:n], axis=0)
            err_screen = np.linalg.norm(foot - off - tfoot[:n], axis=1)
            cam_err = np.abs((r.camera.cam_x[:n] - r.camera.cam_x[0]) - (tcam[:n] - tcam[0]))
            # world-space foot error (what the fitter actually sees)
            wx = foot[:, 0] + r.camera.cam_x[:n]
            twx = tfoot[:n, 0] + tcam[:n]
            werr = np.abs((wx - wx[0]) - (twx - twx[0]))
            rows.append((truth_path.stem.replace(".truth", ""), m, err_screen.mean(), err_screen.max(),
                         cam_err.max(), werr.max(), r.lost.mean(), dt))
            print(f"{rows[-1][0]:32s} {m:9s} foot mean {rows[-1][2]:6.2f}px max {rows[-1][3]:7.2f}px "
                  f"| cam max err {rows[-1][4]:6.2f}px | world-x max err {rows[-1][5]:6.2f}px "
                  f"| lost {rows[-1][6]*100:4.1f}% | {dt:5.1f}s", flush=True)
    print("\nSummary (mean over clips):")
    for m in methods:
        rs = [r for r in rows if r[1] == m]
        if rs:
            a = np.array([r[2:] for r in rs], float)
            print(f"  {m:9s} foot mean {a[:,0].mean():6.2f}px  foot max {a[:,1].max():7.2f}px  "
                  f"cam max {a[:,2].max():6.2f}px  lost {a[:,4].mean()*100:4.1f}%  time {a[:,5].mean():5.1f}s")


if __name__ == "__main__":
    main()
