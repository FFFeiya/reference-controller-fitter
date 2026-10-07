"""Project model: reference clips (video + tracking + annotations) and fit results.

Everything is JSON-serializable so a session can be saved and reopened (.rcf.json).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .model import ControllerParams
from .reference import ReferenceTrajectory, Inference, trajectory_from_track, infer
from .sim import Scenario
from .tracking.pipeline import TrackResult, track_clip


@dataclass
class Clip:
    name: str
    video: str
    start_frame: int
    end_frame: int
    init_bbox: tuple[float, float, float, float] | None = None
    method: str = "template"
    use_camera: bool = True
    camera_roi: tuple[int, int, int, int] | None = None
    char_height_units: float = 1.0   # character height in fit units (1 = fit in character heights)
    ppu: float | None = None         # explicit pixels per unit; None = shared per-video scale (Project)
    weight: float = 1.0
    enabled: bool = True
    track: TrackResult | None = None
    manual_scenario: Scenario | None = None  # user-edited input timeline (overrides inference)
    refine_inputs: bool = True
    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    # derived (not serialized)
    _ref: ReferenceTrajectory | None = field(default=None, repr=False)
    _inf: Inference | None = field(default=None, repr=False)
    _auto_ppu: float | None = field(default=None, repr=False)

    def run_tracking(self, progress=None) -> TrackResult:
        if self.init_bbox is None:
            raise ValueError("No character box set for this clip.")
        self.track = track_clip(self.video, self.start_frame, self.end_frame, self.init_bbox, self.method,
                                camera=self.use_camera, camera_roi=self.camera_roi, progress=progress)
        self.invalidate()
        return self.track

    def invalidate(self):
        self._ref = None
        self._inf = None

    @property
    def reference(self) -> ReferenceTrajectory:
        if self._ref is None:
            if self.track is None:
                raise ValueError(f"Clip '{self.name}' has not been tracked yet.")
            ppu = self.ppu if self.ppu else self._auto_ppu
            self._ref, ppu = trajectory_from_track(self.track, ppu, self.char_height_units, self.use_camera)
            self._ppu_used = ppu
        return self._ref

    @property
    def ppu_used(self) -> float:
        _ = self.reference
        return self._ppu_used

    @property
    def inference(self) -> Inference:
        if self._inf is None:
            self._inf = infer(self.reference)
        return self._inf

    @property
    def scenario(self) -> Scenario:
        return self.manual_scenario if self.manual_scenario is not None else self.inference.scenario

    def to_dict(self) -> dict:
        return {"name": self.name, "video": self.video, "start_frame": self.start_frame, "end_frame": self.end_frame,
                "init_bbox": list(self.init_bbox) if self.init_bbox else None, "method": self.method,
                "use_camera": self.use_camera, "camera_roi": list(self.camera_roi) if self.camera_roi else None,
                "char_height_units": self.char_height_units, "ppu": self.ppu, "weight": self.weight,
                "enabled": self.enabled, "track": self.track.to_dict() if self.track else None,
                "manual_scenario": self.manual_scenario.to_dict() if self.manual_scenario else None,
                "refine_inputs": self.refine_inputs, "uid": self.uid}

    @classmethod
    def from_dict(cls, d: dict, base_dir: Path | None = None) -> "Clip":
        video = d["video"]
        if base_dir is not None and not Path(video).is_absolute():
            video = str((base_dir / video).resolve())
        c = cls(d["name"], video, int(d["start_frame"]), int(d["end_frame"]),
                tuple(d["init_bbox"]) if d.get("init_bbox") else None, d.get("method", "template"),
                bool(d.get("use_camera", True)), tuple(d["camera_roi"]) if d.get("camera_roi") else None,
                float(d.get("char_height_units", 1.0)), d.get("ppu"), float(d.get("weight", 1.0)),
                bool(d.get("enabled", True)))
        if d.get("track"):
            c.track = TrackResult.from_dict(d["track"])
            c.track.video = video
        if d.get("manual_scenario"):
            c.manual_scenario = Scenario.from_dict(d["manual_scenario"])
        c.refine_inputs = bool(d.get("refine_inputs", True))
        if d.get("uid"):
            c.uid = str(d["uid"])
        return c


def _rescale(sc: Scenario, f: float) -> Scenario:
    """Express a scenario's lengths in a new unit (times unchanged)."""
    s = sc.start
    from .model import State
    return Scenario(sc.t_start, sc.t_end, State(s.x * f, s.y * f, s.vx * f, s.vy * f, s.grounded, s.jump_active),
                    sc.inputs, [(t, y * f) for t, y in sc.floors], list(sc.ledges), sc.floor0 * f)


@dataclass
class Project:
    clips: list[Clip] = field(default_factory=list)
    params: ControllerParams = field(default_factory=ControllerParams)
    model_key: str = "D"
    selection: dict | None = None     # last selection summary (JSON)
    path: str | None = None

    def update_scales(self) -> None:
        """One unit scale per video: pixels per character height = median box height over all
        tracked (non-lost) frames of every clip cut from that video. Using each clip's own box
        height would give clips of the same game slightly different units (e.g. 42 vs 46 px)."""
        groups: dict[str, list[np.ndarray]] = {}
        for c in self.clips:
            if c.track is not None:
                h = c.track.bbox[~c.track.lost, 3] if (~c.track.lost).any() else c.track.bbox[:, 3]
                groups.setdefault(str(Path(c.video).resolve()).lower(), []).append(h)
        for c in self.clips:
            if c.track is None:
                continue
            hs = np.concatenate(groups[str(Path(c.video).resolve()).lower()])
            ppu = float(np.median(hs)) / max(c.char_height_units, 1e-6)
            if c._auto_ppu is None or abs(ppu - c._auto_ppu) > 1e-9:
                if c._auto_ppu is not None and c.manual_scenario is not None and not c.ppu:
                    c.manual_scenario = _rescale(c.manual_scenario, c._auto_ppu / ppu)
                c._auto_ppu = ppu
                c.invalidate()

    def fit_clips(self):
        from .fitting.fit import FitClip
        self.update_scales()
        out = []
        for c in self.clips:
            if not c.enabled or c.track is None:
                continue
            out.append(FitClip(c.name, c.reference, c.inference, c.scenario, c.weight, c.refine_inputs, c.uid))
        return out

    def to_dict(self, base_dir: Path | None = None) -> dict:
        clips = []
        for c in self.clips:
            d = c.to_dict()
            if base_dir is not None:
                try:
                    d["video"] = str(Path(c.video).resolve().relative_to(base_dir.resolve()))
                except ValueError:
                    pass
            clips.append(d)
        return {"format": "rcf-project", "version": 1, "clips": clips, "params": self.params.to_dict(),
                "model_key": self.model_key, "selection": self.selection}

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(path.parent), indent=1))
        self.path = str(path)

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        path = Path(path)
        d = json.loads(path.read_text())
        if d.get("format") != "rcf-project":
            raise ValueError("Not a Reference Controller Fitter project file.")
        p = cls([Clip.from_dict(c, path.parent) for c in d["clips"]], ControllerParams.from_dict(d["params"]),
                d.get("model_key", "D"), d.get("selection"), str(path))
        p.update_scales()
        return p
