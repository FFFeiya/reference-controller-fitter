"""Plan and verify the scripted input for the engine demos with the Python prototype.

The same level is built in Unity and Godot. The prototype uses the same integrator and
collision order as the exported controllers, so a timeline that works here works there.
For each goal ("land on step 1 with a short tap", ...) the planner searches the jump time
and picks the middle of the window of times that achieve it (most robust to tiny engine
differences). Writes choreo.json with the input timeline and the level.
"""
import copy
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from rcf.playground import Playground, Rect  # noqa: E402
from rcf.project import Project  # noqa: E402

NAMES = ["Ground", "Ground (after gap)", "Step 1", "Step 2", "Platform", "Wall", "Left wall", "Pit floor"]
LEVEL = [Rect(-20, -5, 31, 0), Rect(35, -5, 90, 0), Rect(7, 0, 11, 1.2), Rect(14.5, 0, 19.5, 2.4),
         Rect(23, 3.0, 30, 3.4), Rect(42, 0, 43, 7), Rect(-21, -5, -20, 10), Rect(30.9, -12, 35.1, -11)]
DT = 1 / 60


def run(p, events, until):
    pg = Playground(p, LEVEL, width=0.8, height=1.0)
    pg.reset(0.0, 0.0)
    t, k, axis, jump = 0.0, 0, 0, False
    log = []
    ev = sorted(events)
    while t < until - 1e-9:
        while k < len(ev) and ev[k][0] <= t + 1e-9:
            _, axis, jump = ev[k]; k += 1
        pg.step(axis, jump, DT)
        t += DT
        s = pg.player.state
        log.append((t, s.x, s.y, s.grounded, s.vx))
    return log


def landings(log, after):
    out, prev = [], True
    for t, x, y, g, vx in log:
        if t > after and g and not prev:
            out.append((t, x, y))
        prev = g
    return out


def plan_jump(p, events, axis, t_from, t_to, hold, goal, horizon=2.5, from_y=None):
    """Find jump press times in [t_from, t_to] whose next landing satisfies goal(x, y),
    optionally only while standing on ground at height from_y."""
    ok = []
    base = run(p, events, t_to + DT) if from_y is not None else None
    for tj in np.arange(t_from, t_to, DT):
        if base is not None:
            i = min(len(base) - 1, max(0, int(round(tj / DT)) - 1))
            if not (base[i][3] and abs(base[i][2] - from_y) < 1e-6):
                continue
        ev = events + [(tj, axis, True), (tj + hold, axis, False)]
        log = run(p, ev, tj + horizon)
        L = landings(log, tj)
        if L and goal(L[0][1], L[0][2]):
            ok.append(tj)
    if not ok:
        raise RuntimeError(f"no jump time in [{t_from:.2f},{t_to:.2f}] reaches the goal")
    # longest consecutive run of feasible times, take its middle
    runs, cur = [], [ok[0]]
    for a, b in zip(ok[:-1], ok[1:]):
        if b - a < DT * 1.5:
            cur.append(b)
        else:
            runs.append(cur); cur = [b]
    runs.append(cur)
    best = max(runs, key=len)
    tj = float(best[len(best) // 2])
    return tj, len(best) * DT


def main():
    p = Project.load(ROOT / "examples" / "supertux.rcf.json").params
    ev = [(0.0, 0, False), (0.6, 1, False)]
    notes = []

    def add_jump(axis, t0, t1, hold, goal, label, from_y=None):
        tj, win = plan_jump(p, ev, axis, t0, t1, hold, goal, from_y=from_y)
        ev.extend([(tj, axis, True), (tj + hold, axis, False)])
        notes.append(f"{label}: press {tj:.2f}s hold {hold:.2f}s (window {win:.2f}s)")
        return tj

    tj = add_jump(1, 0.7, 2.5, 0.12, lambda x, y: abs(y - 1.2) < 1e-6, "short tap onto step 1")
    tj = add_jump(1, tj + 0.4, tj + 2.0, 0.45, lambda x, y: abs(y - 2.4) < 1e-6, "long jump onto step 2", from_y=1.2)
    tj = add_jump(1, tj + 0.4, tj + 2.0, 0.6, lambda x, y: abs(y - 3.4) < 1e-6 and x < 27.5, "long jump onto platform", from_y=2.4)
    # stop on the platform, look back left, then go right again
    land = landings(run(p, ev, tj + 3), tj)[0][0]
    ev += [(land + 0.05, 0, False), (land + 0.75, -1, False), (land + 1.05, 0, False), (land + 1.5, 1, False)]
    tj = add_jump(1, land + 1.55, land + 3.0, 0.5, lambda x, y: y == 0.0 and 35.5 < x < 40.5,
                  "jump from the platform over the gap", from_y=3.4)
    land = landings(run(p, ev, tj + 3), tj)[0][0]
    ev += [(land + 0.15, 0, False)]
    t = land + 1.3
    ev += [(t, 0, True), (t + 0.08, 0, False)]          # short hop in place
    ev += [(t + 1.1, 0, True), (t + 1.7, 0, False)]     # long hop in place
    duration = t + 3.2
    log = run(p, ev, duration)
    for n in notes:
        print(n)
    for L in landings(log, 0):
        print(f"  land t={L[0]:5.2f} x={L[1]:6.2f} y={L[2]:5.2f}")
    print(f"  final x={log[-1][1]:.2f} y={log[-1][2]:.2f} grounded={log[-1][3]}  duration {duration:.2f}s")
    ev.sort()
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tools" / "video" / "choreo.json"
    out.write_text(json.dumps({"duration": round(duration, 3),
                               "events": [{"t": round(a, 4), "axis": b, "jump": c} for a, b, c in ev],
                               "level": [[r.x0, r.y0, r.x1, r.y1] for r in LEVEL], "names": NAMES,
                               "trace": [[round(a, 3), round(b, 3), round(c, 3)] for a, b, c, *_ in log[::2]]}, indent=0))
    print("wrote", out)


if __name__ == "__main__":
    main()
