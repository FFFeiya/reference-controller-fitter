"""Single streaming pass: camera estimation + player tracking for one clip."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .camera import CameraEstimator, CameraTrack, static_mask
from .tracker import make_tracker, appearance_score, _clip_box, TemplateTracker, ColorModel
from .video import probe, iter_frames


@dataclass
class TrackResult:
    video: str
    fps: float
    start_frame: int
    frames: np.ndarray          # absolute frame indices
    bbox: np.ndarray            # (n, 4) screen boxes x, y, w, h
    confidence: np.ndarray      # (n,) appearance score 0..1
    lost: np.ndarray            # (n,) bool
    camera: CameraTrack
    method: str
    camera_enabled: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def times(self) -> np.ndarray:
        return self.frames / self.fps

    @property
    def lost_fraction(self) -> float:
        return float(self.lost.mean()) if len(self.lost) else 1.0

    def slice(self, f0: int, f1: int) -> "TrackResult":
        """Sub-range [f0, f1) in absolute frame numbers (one long track -> several clips)."""
        m = (self.frames >= f0) & (self.frames < f1)
        if m.sum() < 2:
            raise ValueError("Slice contains fewer than 2 tracked frames.")
        cam = CameraTrack(self.camera.cam_x[m], self.camera.cam_y[m], self.camera.confidence[m],
                          [s for s, k in zip(self.camera.method, m) if k] if self.camera.method else [])
        return TrackResult(self.video, self.fps, int(self.frames[m][0]), self.frames[m], self.bbox[m],
                           self.confidence[m], self.lost[m], cam, self.method, self.camera_enabled, list(self.notes))

    def foot_screen(self) -> np.ndarray:
        """(n, 2) bottom-centre of the boxes in screen pixels."""
        return np.stack([self.bbox[:, 0] + self.bbox[:, 2] / 2, self.bbox[:, 1] + self.bbox[:, 3]], axis=1)

    def to_dict(self) -> dict:
        return {"video": self.video, "fps": self.fps, "start_frame": self.start_frame,
                "frames": self.frames.tolist(), "bbox": self.bbox.tolist(),
                "confidence": self.confidence.tolist(), "lost": self.lost.astype(int).tolist(),
                "camera": {"x": self.camera.cam_x.tolist(), "y": self.camera.cam_y.tolist(),
                           "confidence": self.camera.confidence.tolist(), "method": self.camera.method},
                "method": self.method, "camera_enabled": self.camera_enabled, "notes": self.notes}

    @classmethod
    def from_dict(cls, d: dict) -> "TrackResult":
        c = d["camera"]
        return cls(d["video"], float(d["fps"]), int(d["start_frame"]), np.array(d["frames"], dtype=int),
                   np.array(d["bbox"], dtype=float).reshape(-1, 4), np.array(d["confidence"], dtype=float),
                   np.array(d["lost"], dtype=bool),
                   CameraTrack(np.array(c["x"], float), np.array(c["y"], float), np.array(c["confidence"], float),
                               list(c.get("method", []))),
                   d.get("method", "hybrid"), bool(d.get("camera_enabled", True)), list(d.get("notes", [])))


def _hud_mask(path, start, end, n_samples=24):
    info = probe(path)
    end = min(end, info.frame_count) if info.frame_count > 0 else end
    idx = set(np.linspace(start, max(start, end - 1), n_samples).astype(int).tolist())
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for i, f in iter_frames(path, start, end) if i in idx]
    return static_mask(grays)


def splice(old: TrackResult, new: TrackResult) -> TrackResult:
    """Manual correction: keep `old` before new.start_frame, use the re-tracked `new` from there.
    The camera path of `new` (which starts at 0) is offset to continue the old one."""
    f = new.start_frame
    keep = old.frames < f
    if not keep.any():
        return new
    k_old = np.where(old.frames == f)[0]
    if len(k_old):
        ox, oy = old.camera.cam_x[k_old[0]], old.camera.cam_y[k_old[0]]
    else:
        ox, oy = old.camera.cam_x[keep][-1], old.camera.cam_y[keep][-1]
    cam = CameraTrack(np.concatenate([old.camera.cam_x[keep], new.camera.cam_x + ox]),
                      np.concatenate([old.camera.cam_y[keep], new.camera.cam_y + oy]),
                      np.concatenate([old.camera.confidence[keep], new.camera.confidence]),
                      [m for m, k in zip(old.camera.method, keep) if k] + list(new.camera.method))
    notes = list(dict.fromkeys(old.notes + new.notes + [f"Box corrected manually at frame {f}."]))
    return TrackResult(old.video, old.fps, old.start_frame, np.concatenate([old.frames[keep], new.frames]),
                       np.vstack([old.bbox[keep], new.bbox]), np.concatenate([old.confidence[keep], new.confidence]),
                       np.concatenate([old.lost[keep], new.lost]), cam, new.method, old.camera_enabled, notes)


def _inside(box, shape) -> bool:
    h, w = shape[:2]
    x, y, bw, bh = box
    return x > -bw and y > -bh and x < w and y < h


def track_clip(video: str | Path, start_frame: int, end_frame: int, init_bbox, method: str = "template",
               camera: bool = True, camera_roi=None, progress: Callable[[float], None] | None = None,
               lost_threshold: float = 0.35) -> TrackResult:
    info = probe(video)
    end_frame = min(end_frame, info.frame_count) if info.frame_count > 0 else end_frame
    notes = []
    static = _hud_mask(video, start_frame, end_frame) if camera else None
    if static is not None:
        notes.append("Static screen regions (HUD) excluded from camera estimation.")
    cam = CameraEstimator(static, camera_roi)
    # search radius (in box sizes) must cover the per-frame motion: low-fps footage moves far
    search = max(1.5, 1.5 * 30.0 / max(info.fps, 1.0))
    tracker = make_tracker(method, search=search)
    redetect = TemplateTracker(search=max(4.0, 2 * search), update_rate=0.0, scale_search=False)
    global_redetect = TemplateTracker(search=1000.0, update_rate=0.0, scale_search=False)
    frames, boxes, confs, lost = [], [], [], []
    template = None
    prev, prev2 = None, None
    recent: list[float] = []
    coast = 0
    recovered = False
    color = None
    total = max(1, end_frame - start_frame)
    for i, frame in iter_frames(video, start_frame, end_frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if camera:
            cam.update(gray, prev if prev is not None else init_bbox)
        if template is None:
            box = tuple(float(v) for v in init_bbox)
            x, y, w, h = _clip_box(box, frame.shape)
            template = frame[y:y + h, x:x + w].copy()
            tracker.init(frame, box)
            redetect.init(frame, box)
            global_redetect.init(frame, box)
            color = ColorModel(frame, box)
            if color.separability < 0.6:
                color = None   # character colours also common in the background
            score, is_lost = 1.0, False
        else:
            if prev2 is not None:
                hint = (2 * prev[0] - prev2[0], 2 * prev[1] - prev2[1], prev[2], prev[3])
            else:
                hint = prev
            box, s = tracker.update(frame, hint)
            if box is None:
                score = 0.0
            elif hasattr(tracker, "commit"):
                score = s   # best NCC over the tracker's appearance bank (animation-aware)
            else:
                score = appearance_score(frame, box, template)
            # gates: (1) appearance clearly below its recent level, (2) a jump that the
            # recent motion cannot explain (occlusion makes trackers latch onto scenery)
            med = float(np.median(recent)) if len(recent) >= 5 else 1.0
            # relative threshold capped at 0.6: a standing sprite scores ~1.0 and the first
            # animation frames legitimately drop to ~0.7
            low = score < max(lost_threshold, min(0.82 * med, 0.6))
            far = False
            if box is not None and len(recent) >= 3:
                size = max(hint[2], hint[3])
                dcx = (box[0] + box[2] / 2) - (hint[0] + hint[2] / 2)
                dcy = (box[1] + box[3]) - (hint[1] + hint[3])
                far = np.hypot(dcx, dcy) > 0.6 * size * (1 + coast) * max(1.0, 30.0 / info.fps)
                # a big unexplained jump is only suspicious if the match is not clearly good
                # (take-off legitimately changes the velocity abruptly)
                far = far and score < 0.9 * med
            is_lost = box is None or low or far
            if is_lost:
                # wide re-detection with the original template, accepted only near the prediction
                rb, rs = redetect.update(frame, hint)
                accept = False
                if rb is not None and rs > max(0.6, 0.85 * med):
                    d = np.hypot(rb[0] - hint[0], rb[1] + rb[3] - hint[1] - hint[3])
                    accept = d < max(hint[2], hint[3]) * (1 + coast)
                if not accept and color is not None:
                    # pose-invariant colour fallback (new animation frames the bank hasn't seen)
                    cb, cf = color.locate(frame, hint, radius=search)
                    if cb is not None and cf > 0.3:
                        d = np.hypot(cb[0] - hint[0], cb[1] + cb[3] - hint[1] - hint[3])
                        if d < max(hint[2], hint[3]) * (1 + coast) * max(1.0, 30.0 / info.fps):
                            rb, rs, accept = cb, 0.6, True
                if not accept and coast >= 2:
                    # lost for a while: search the whole frame, but demand a strong match
                    rb, rs = global_redetect.update(frame, hint)
                    accept = rb is not None and rs > max(0.7, 0.92 * med)
                if accept:
                    box, score, is_lost = rb, rs, False
                    recovered = True
                    try:
                        tracker.init(frame, rb)
                    except Exception:
                        pass
            if is_lost:
                box = hint          # coast on the motion prediction; frame is excluded from fitting
                coast += 1
                if coast > int(info.fps) or not _inside(box, frame.shape):  # stop extrapolating
                    box = prev
                    prev2 = None
            else:
                coast = 0
                recent.append(score)
                recent = recent[-15:]
                if hasattr(tracker, "commit"):
                    tracker.commit(frame, box, score)
        frames.append(i); boxes.append(box); confs.append(score); lost.append(is_lost)
        if recovered:
            prev2, prev = None, box   # no velocity across a re-detection jump
            recovered = False
        else:
            prev2, prev = prev, box
        if progress is not None and (i - start_frame) % 5 == 0:
            progress((i - start_frame) / total)
    if not frames:
        raise IOError("No frames could be read from the clip range.")
    cam_track = cam.result() if camera else CameraTrack(np.zeros(len(frames)), np.zeros(len(frames)),
                                                       np.ones(len(frames)), ["off"] * len(frames))
    lost_arr = np.array(lost, bool)
    if lost_arr.mean() > 0.05:
        notes.append(f"Tracking lost on {lost_arr.sum()} of {len(lost_arr)} frames – correct the box and re-track.")
    return TrackResult(str(video), info.fps, start_frame, np.array(frames), np.array(boxes, float),
                       np.array(confs, float), lost_arr, cam_track, method, camera, notes)
