"""Video reading helpers."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class VideoInfo:
    path: str
    fps: float
    frame_count: int
    width: int
    height: int

    @property
    def duration(self) -> float:
        return self.frame_count / self.fps if self.fps else 0.0


def probe(path: str | Path) -> VideoInfo:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise IOError(f"Cannot open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if fps <= 0 or fps > 1000:
        fps = 30.0
    return VideoInfo(str(path), float(fps), n, w, h)


def iter_frames(path: str | Path, start: int = 0, end: int | None = None):
    """Yield (index, BGR frame) sequentially from start (inclusive) to end (exclusive)."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise IOError(f"Cannot open video: {path}")
    if start > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    i = start
    try:
        while end is None or i < end:
            ok, frame = cap.read()
            if not ok:
                break
            yield i, frame
            i += 1
    finally:
        cap.release()


class FrameCache:
    """Random access to frames with a small LRU cache (used by the GUI)."""

    def __init__(self, path: str | Path, capacity: int = 240):
        self.path = str(path)
        self.info = probe(path)
        self.cap = cv2.VideoCapture(self.path)
        self.capacity = capacity
        self.cache: OrderedDict[int, np.ndarray] = OrderedDict()
        self._next = 0

    def get(self, idx: int) -> np.ndarray | None:
        idx = max(0, min(self.info.frame_count - 1, idx))
        if idx in self.cache:
            self.cache.move_to_end(idx)
            return self.cache[idx]
        if idx != self._next:
            # seeking is slow; only seek when not reading sequentially nearby
            if not (0 < idx - self._next < 30):
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                self._next = idx
        frame = None
        while self._next <= idx:
            ok, f = self.cap.read()
            if not ok:
                break
            self._put(self._next, f)
            frame = f
            self._next += 1
        return frame if frame is not None else self.cache.get(idx)

    def _put(self, idx, f):
        self.cache[idx] = f
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)

    def close(self):
        self.cap.release()
