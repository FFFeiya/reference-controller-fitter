"""Real gameplay cases: track Wikimedia Commons footage, cut several clips, fit ONE controller per game.

Usage:  python examples/real_cases.py [case ...]      (cases: supertux, smc; default all)

Writes examples/<case>.rcf.json (open it in the GUI) and examples/<case>_report.json.
Clip ranges were chosen by looking at the tracked trajectories (tools/survey_tracks.py):
they avoid head-bumps into blocks and enemy stomps, which a movement controller without
level geometry cannot explain. The character box is given only for the first frame of
each source segment (what a user draws in the GUI); clips inside a segment reuse the
segment's track.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rcf.project import Clip, Project  # noqa: E402
from rcf.tracking.pipeline import track_clip, TrackResult  # noqa: E402
from rcf.tracking.video import probe  # noqa: E402
from rcf.fitting.selection import select_model  # noqa: E402

REAL = ROOT / "references" / "real"
CACHE = ROOT / "examples" / "tracks"

# source segment -> first-frame box
SOURCES = {
    "supertux_l1_seg01": (REAL / "supertux_l1" / "supertux_l1_seg01.mp4", (94, 426, 36, 38)),
    "supertux_l1_seg02": (REAL / "supertux_l1" / "supertux_l1_seg02.mp4", (515, 253, 38, 38)),
    "supertux_ff_seg01": (REAL / "supertux_ff" / "supertux_ff_seg01.mp4", (132, 518, 46, 46)),
    "smc_seg01": (REAL / "smc" / "smc_seg01.mp4", (155, 352, 42, 56)),
}

CASES = {
    # SuperTux (two different recordings by different people, same game)
    "supertux": [
        ("run + running jump", "supertux_l1_seg01", 0, 100),
        ("jump off bridge, drop two levels", "supertux_l1_seg02", 25, 100),
        ("jump onto step, stop", "supertux_l1_seg02", 158, 200),
        # (frames 30-70 of supertux_ff look like a small hop but are Tux running over a smooth
        #  slope bump – slopes are out of scope; the tool flagged it, see DEVLOG. Frames 116-162 of
        #  supertux_l1_seg02 were also rejected: 9.5 % error, the jump clips a coin row/block.)
        ("running jump", "supertux_ff_seg01", 136, 172),
    ],
    # Secret Maryo Chronicles (15 fps recording)
    "smc": [
        ("walk off pipe, run, high jump", "smc_seg01", 8, 58),
        ("run, two jumps, land on pipe", "smc_seg01", 30, 80),
    ],
}


def source_track(name: str) -> TrackResult:
    video, box = SOURCES[name]
    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / f"{name}.track.json"
    if cache.exists():
        tr = TrackResult.from_dict(json.loads(cache.read_text()))
        tr.video = str(video)
        return tr
    info = probe(video)
    tr = track_clip(str(video), 0, info.frame_count, box)
    cache.write_text(json.dumps(tr.to_dict()))
    return tr


def build_project(case: str) -> Project:
    proj = Project()
    for title, src, f0, f1 in CASES[case]:
        tr = source_track(src).slice(f0, f1)
        video, _ = SOURCES[src]
        clip = Clip(f"{src} {f0}-{f1}: {title}", str(video), f0, f1, tuple(tr.bbox[0]), "template", True)
        clip.track = tr
        proj.clips.append(clip)
    return proj


def run(case: str, log=print) -> dict:
    proj = build_project(case)
    for c in proj.clips:
        inf = c.inference
        log(f"[{case}] {c.name}: {len(c.track.frames)} frames, camera {c.track.camera.level}, "
            f"lost {100 * c.track.lost_fraction:.1f}%, phases {[p.kind for p in inf.phases]}")
    sel = select_model(proj.fit_clips(), progress=lambda m, cost, p, f: log(m) if p is None else None)
    best = sel.best
    proj.params = best.params
    proj.model_key = sel.chosen
    proj.selection = sel.to_dict()
    proj.save(ROOT / "examples" / f"{case}.rcf.json")
    log("")
    for line in sel.explanation:
        log("  " + line)
    log(f"Chosen {sel.chosen}, confidence {sel.confidence}")
    for r in sel.reasons:
        log("   - " + r)
    for c, m in zip(proj.clips, best.clip_metrics):
        log(f"  {c.name:60s} trajectory {100 * m.values['trajectory']:5.2f}%  overall {100 * m.overall:5.2f}%")
    log("  params: " + ", ".join(f"{k}={v:.3g}" for k, v in best.params.to_dict().items() if v is not None))
    rep = {"case": case, "chosen": sel.chosen, "confidence": sel.confidence, "reasons": sel.reasons,
           "explanation": sel.explanation, "params": best.params.to_dict(),
           "clips": {c.name: m.to_dict() for c, m in zip(proj.clips, best.clip_metrics)},
           "overall": best.overall.to_dict()}
    (ROOT / "examples" / f"{case}_report.json").write_text(json.dumps(rep, indent=1))
    return rep


if __name__ == "__main__":
    for case in sys.argv[1:] or list(CASES):
        run(case)
