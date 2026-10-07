"""Add/edit a reference clip: pick range, mark the character, track, correct."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel, QSpinBox,
                               QDoubleSpinBox, QComboBox, QCheckBox, QProgressBar, QMessageBox, QLineEdit,
                               QGridLayout, QGroupBox)

from ..project import Clip
from ..tracking.video import FrameCache
from ..tracking.tracker import TRACKER_METHODS
from ..tracking.pipeline import splice
from .views import FrameCanvas
from .workers import TrackThread


class ClipDialog(QDialog):
    def __init__(self, video: str, clip: Clip | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reference clip")
        self.resize(1100, 760)
        self.cache = FrameCache(video)
        info = self.cache.info
        self.video = video
        self.clip = clip
        self.track = clip.track if clip else None
        self.thread = None
        self._retrack_from = None

        lay = QVBoxLayout(self)
        self.canvas = FrameCanvas()
        self.canvas.draw_enabled = True
        self.canvas.boxDrawn.connect(self._box_drawn)
        self.canvas.overlay_fn = self._overlay
        lay.addWidget(self.canvas, 1)

        row = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, max(0, info.frame_count - 1))
        self.slider.valueChanged.connect(self._show)
        self.frame_lbl = QLabel()
        row.addWidget(self.slider, 1)
        row.addWidget(self.frame_lbl)
        lay.addLayout(row)

        grid = QGridLayout()
        self.name = QLineEdit(clip.name if clip else Path(video).stem)
        self.start = QSpinBox(); self.start.setRange(0, info.frame_count - 1)
        self.end = QSpinBox(); self.end.setRange(1, info.frame_count)
        self.start.setValue(clip.start_frame if clip else 0)
        # default: the first ~5 s (short clips with one clear action fit best)
        self.end.setValue(clip.end_frame if clip else min(info.frame_count, int(round(5 * info.fps))))
        self.start.valueChanged.connect(self._range_changed)
        self.end.valueChanged.connect(self._range_changed)
        b_in = QPushButton("Set In = current"); b_in.clicked.connect(lambda: self.start.setValue(self.slider.value()))
        b_out = QPushButton("Set Out = current"); b_out.clicked.connect(lambda: self.end.setValue(self.slider.value() + 1))
        grid.addWidget(QLabel("Name"), 0, 0); grid.addWidget(self.name, 0, 1, 1, 3)
        grid.addWidget(QLabel("In frame"), 1, 0); grid.addWidget(self.start, 1, 1); grid.addWidget(b_in, 1, 2)
        grid.addWidget(QLabel("Out frame (excl.)"), 2, 0); grid.addWidget(self.end, 2, 1); grid.addWidget(b_out, 2, 2)
        grid.addWidget(QLabel(f"{info.width}×{info.height}, {info.fps:.2f} fps, {info.frame_count} frames"), 1, 3)

        self.method = QComboBox(); self.method.addItems(TRACKER_METHODS)
        self.method.setToolTip("template (recommended): learns the sprite's animation frames, follows squash & "
                               "stretch, re-detects after occlusion. csrt/kcf/mil: OpenCV trackers. "
                               "flow: optical flow inside the box. hybrid: template + colour extents.")
        if clip:
            self.method.setCurrentText(clip.method)
        self.cam = QCheckBox("Camera compensation"); self.cam.setChecked(clip.use_camera if clip else True)
        self.unit_lbl = QLabel("Units: 1 unit = the character's height (set your engine's scale at Export).")
        self.roi_btn = QPushButton("Draw camera background region…")
        self.roi_btn.setCheckable(True)
        self.roi_btn.setToolTip("Optional: restrict camera estimation to a region of the gameplay layer "
                                "(e.g. the ground strip) when parallax layers confuse it.")
        self.roi_btn.toggled.connect(self._roi_mode)
        self.roi = clip.camera_roi if clip else None
        grid.addWidget(QLabel("Tracker"), 3, 0); grid.addWidget(self.method, 3, 1)
        grid.addWidget(self.cam, 3, 2); grid.addWidget(self.roi_btn, 3, 3)
        grid.addWidget(self.unit_lbl, 4, 0, 1, 4)
        lay.addLayout(grid)

        row = QHBoxLayout()
        self.detect_btn = QPushButton("Auto-detect character"); self.detect_btn.clicked.connect(self._detect)
        self.track_btn = QPushButton("Track"); self.track_btn.clicked.connect(lambda: self._track(False))
        self.fix_btn = QPushButton("Use box at this frame && re-track from here")
        self.fix_btn.clicked.connect(lambda: self._track(True))
        self.fix_btn.setEnabled(self.track is not None)
        self.next_lost = QPushButton("Next lost frame ▶"); self.next_lost.clicked.connect(self._goto_lost)
        self.progress = QProgressBar(); self.progress.setRange(0, 100)
        for w in (self.detect_btn, self.track_btn, self.fix_btn, self.next_lost):
            row.addWidget(w)
        row.addWidget(self.progress, 1)
        lay.addLayout(row)
        self.status = QLabel("Draw a box around the player character in the In frame (drag with the mouse), "
                             "or try Auto-detect. Then press Track.")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        ok = QPushButton("Add clip" if clip is None else "Update clip"); ok.clicked.connect(self._accept)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        row.addStretch(1); row.addWidget(cancel); row.addWidget(ok)
        lay.addLayout(row)

        self.box = clip.init_bbox if clip else None
        self.slider.setValue(self.start.value())
        self._show(self.start.value())
        if self.track is not None:
            self._summarize()

    # ---- display
    def _show(self, idx):
        f = self.cache.get(idx)
        self.canvas.set_frame(f)
        self.frame_lbl.setText(f"frame {idx}  t={idx / self.cache.info.fps:.2f}s")
        tb = self._track_box(idx)
        if tb is not None:
            self.canvas.box = tuple(tb[0])
            self.canvas.box_color = Qt.red if tb[1] else Qt.green
        elif idx == self.start.value() and self.box is not None:
            self.canvas.box = self.box
            self.canvas.box_color = Qt.green
        else:
            self.canvas.box = None
        self.canvas.roi = self.roi
        self.canvas.update()

    def _track_box(self, idx):
        if self.track is None:
            return None
        k = np.where(self.track.frames == idx)[0]
        if not len(k):
            return None
        return self.track.bbox[k[0]], bool(self.track.lost[k[0]])

    def _overlay(self, qp, tw):
        if self.track is None:
            return
        from PySide6.QtGui import QPen, QColor, QPainterPath
        idx = self.slider.value()
        k = np.where(self.track.frames == idx)[0]
        if not len(k):
            return
        k = k[0]
        foot = self.track.foot_screen()
        cam = np.stack([self.track.camera.cam_x, self.track.camera.cam_y], 1)
        pts = foot + cam - cam[k] if self.track.camera_enabled else foot
        path = QPainterPath()
        for i, (x, y) in enumerate(pts):
            p = tw(x, y)
            path.moveTo(p) if i == 0 else path.lineTo(p)
        qp.setPen(QPen(QColor(255, 170, 40), 2))
        qp.drawPath(path)

    def _box_drawn(self, b):
        if self.roi_btn.isChecked():
            self.roi = tuple(int(v) for v in b)
            self.roi_btn.setChecked(False)
            self.canvas.box = None
            self.status.setText(f"Camera region set to {self.roi}. Re-track to apply.")
            self._show(self.slider.value())
            return
        idx = self.slider.value()
        if self.track is None or idx == self.start.value():
            self.box = b
            if idx != self.start.value():
                self.start.setValue(idx)
            self.status.setText(f"Character box {b} at frame {idx}. Press Track.")
        else:
            self.status.setText(f"Box drawn at frame {idx}. Press 'Use box at this frame & re-track from here' "
                                f"to correct the track from this frame on.")
            self._fix_box = b

    def _roi_mode(self, on):
        self.status.setText("Drag a rectangle over a part of the static gameplay layer (e.g. the ground)." if on
                            else self.status.text())

    # ---- actions
    def _range_changed(self, _=None):
        if self.track is not None and self.thread is None and (
                self.start.value() != int(self.track.frames[0]) or self.end.value() != int(self.track.frames[-1]) + 1):
            self.track = None
            self.fix_btn.setEnabled(False)
            self.progress.setValue(0)
            self.status.setText("Clip range changed – the previous tracking was discarded. Check the box in the "
                                "In frame and press Track again.")
            self._show(self.slider.value())

    def _detect(self):
        from PySide6.QtWidgets import QApplication
        from ..tracking.detect import detect_player
        self.status.setText("Detecting…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.repaint()
        try:
            d = detect_player(self.video, self.start.value(), self.end.value())
        except Exception as e:
            QMessageBox.warning(self, "Detection failed", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        if d.bbox is None:
            self.status.setText(d.message)
            return
        self.box = tuple(float(round(v)) for v in d.bbox)
        self.slider.setValue(self.start.value())
        self._show(self.start.value())
        self.status.setText(f"{d.message} (score {d.confidence:.2f})")

    def _track(self, from_here: bool):
        if self.thread is not None:
            return
        if from_here:
            box = getattr(self, "_fix_box", None)
            if box is None:
                QMessageBox.information(self, "Correct tracking", "Scrub to a frame, draw the correct box, then press this button.")
                return
            s = self.slider.value()
            self._retrack_from = s
        else:
            box, s = self.box, self.start.value()
            self._retrack_from = None
        if box is None:
            QMessageBox.information(self, "Track", "Draw a box around the character first.")
            return
        if self.end.value() - s < 3:
            QMessageBox.information(self, "Track", "The clip range is too short.")
            return
        self.thread = TrackThread(self.video, s, self.end.value(), box, self.method.currentText(),
                                  self.cam.isChecked(), self.roi, self)
        self.thread.progress.connect(lambda f: self.progress.setValue(int(100 * f)))
        self.thread.done.connect(self._tracked)
        self.thread.failed.connect(self._failed)
        self.track_btn.setEnabled(False); self.fix_btn.setEnabled(False)
        self.status.setText("Tracking…")
        self.thread.start()

    def _tracked(self, res):
        self.thread = None
        self.track_btn.setEnabled(True)
        if self._retrack_from is not None and self.track is not None:
            res = splice(self.track, res)
        self.track = res
        self.fix_btn.setEnabled(True)
        self.progress.setValue(100)
        self._summarize()
        self._show(self.slider.value())

    def _summarize(self):
        tr = self.track
        n_lost = int(tr.lost.sum())
        msg = (f"Tracked {len(tr.frames)} frames with '{tr.method}'. Lost: {n_lost} "
               f"({100 * tr.lost_fraction:.1f}%). Camera compensation: "
               f"{'off' if not tr.camera_enabled else tr.camera.level + f' ({tr.camera.overall_confidence:.2f})'}.")
        if tr.camera_enabled and tr.camera.level == "LOW":
            msg += " Camera estimate unreliable – draw a background region or disable compensation."
        if n_lost:
            msg += " Red boxes = lost frames (ignored by the fitter). Use 'Next lost frame' to inspect and correct."
        self.status.setText(msg + ("\n" + " ".join(tr.notes) if tr.notes else ""))

    def _failed(self, msg):
        self.thread = None
        self.track_btn.setEnabled(True)
        self.fix_btn.setEnabled(self.track is not None)
        QMessageBox.warning(self, "Tracking failed", msg)

    def _goto_lost(self):
        if self.track is None:
            return
        cur = self.slider.value()
        lost = self.track.frames[self.track.lost]
        nxt = lost[lost > cur]
        if len(nxt):
            self.slider.setValue(int(nxt[0]))
        elif len(lost):
            self.slider.setValue(int(lost[0]))

    def _accept(self):
        if self.track is None:
            QMessageBox.information(self, "Clip", "Track the clip first.")
            return
        c = self.clip or Clip(self.name.text(), self.video, self.start.value(), self.end.value())
        c.name = self.name.text()
        c.video = self.video
        c.start_frame = int(self.track.frames[0])
        c.end_frame = int(self.track.frames[-1]) + 1
        c.init_bbox = tuple(self.track.bbox[0])
        c.method = self.method.currentText()
        c.use_camera = self.cam.isChecked()
        c.camera_roi = self.roi
        c.track = self.track
        c.manual_scenario = None if self.clip is None else self.clip.manual_scenario
        c.invalidate()
        self.result_clip = c
        self.cache.close()
        self.accept()

    def reject(self):
        if self.thread is not None:
            self.thread.cancel()
            self.thread.wait(30000)
            self.thread = None
        self.cache.close()
        super().reject()
