"""Background threads for tracking and fitting (keep the UI responsive)."""
from __future__ import annotations

import time
import traceback

from PySide6.QtCore import QThread, Signal

from ..tracking.pipeline import track_clip


class TrackThread(QThread):
    progress = Signal(float)
    done = Signal(object)        # TrackResult
    failed = Signal(str)

    def __init__(self, video, start, end, bbox, method, camera, roi, parent=None):
        super().__init__(parent)
        self.args = (video, start, end, bbox, method, camera, roi)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _progress(self, f):
        if self._cancel:
            raise _Cancelled()
        self.progress.emit(f)

    def run(self):
        try:
            v, s, e, b, m, c, r = self.args
            res = track_clip(v, s, e, b, m, camera=c, camera_roi=r, progress=self._progress)
            self.done.emit(res)
        except _Cancelled:
            pass
        except Exception as ex:  # surfaced in the UI
            self.failed.emit(f"{ex}\n\n{traceback.format_exc(limit=3)}")


class _Cancelled(Exception):
    pass


class FitThread(QThread):
    """Runs model search (key=None) or a single model fit."""
    progress = Signal(str, float, object)   # message, rms error estimate (nan if n/a), ControllerParams|None
    done = Signal(object)                   # Selection or FitResult
    cancelled = Signal()                    # user cancelled: the partial result is discarded
    failed = Signal(str)

    def __init__(self, clips, model_key: str | None, init=None, parent=None):
        super().__init__(parent)
        self.clips = clips
        self.model_key = model_key
        self.init = init
        self._cancel = False
        self._last = 0.0

    def cancel(self):
        self._cancel = True

    def _cb(self, msg, cost, params, frac):
        now = time.time()
        if params is None:
            self.progress.emit(msg, float("nan"), None)
            return
        if now - self._last < 0.08:
            return
        self._last = now
        wsum = sum(c.weight for c in self.clips) or 1.0
        self.progress.emit(msg, (max(cost, 0.0) / wsum) ** 0.5, params)

    def run(self):
        try:
            if self.model_key is None:
                from ..fitting.selection import select_model
                res = select_model(self.clips, progress=self._cb, cancel=lambda: self._cancel)
            else:
                from ..fitting.fit import fit_model
                from ..model import MODELS_BY_KEY
                res = fit_model(self.clips, MODELS_BY_KEY[self.model_key], init=self.init, progress=self._cb,
                                cancel=lambda: self._cancel)
            if self._cancel:
                self.cancelled.emit()
            else:
                self.done.emit(res)
        except Exception as ex:
            if self._cancel:
                self.cancelled.emit()
            else:
                self.failed.emit(f"{ex}\n\n{traceback.format_exc(limit=4)}")
