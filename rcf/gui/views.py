"""Reference video view (with overlays) and the prototype world view (playback + play mode)."""
from __future__ import annotations

import math

import cv2
import numpy as np
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QTimer
from PySide6.QtGui import QImage, QPainter, QPen, QColor, QBrush, QPainterPath, QFont
from PySide6.QtWidgets import QWidget

from ..playground import Playground, Rect, demo_level

REF_COLOR = QColor(255, 170, 40)      # reference trajectory (orange)
PROTO_COLOR = QColor(80, 220, 255)    # prototype trajectory (cyan)
BG = QColor(28, 30, 34)


def bgr_to_qimage(frame: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


class FrameCanvas(QWidget):
    """Shows a video frame scaled to fit; optional rubber-band box drawing (frame pixel coords)."""
    boxDrawn = Signal(tuple)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 200)
        self.image: QImage | None = None
        self.draw_enabled = False
        self.box = None          # (x, y, w, h) frame px
        self.box_color = QColor(0, 255, 0)
        self.roi = None
        self._drag = None
        self.overlay_fn = None   # callable(painter, to_widget) for extra overlays
        self.message = ""

    def set_frame(self, frame: np.ndarray | None):
        self.image = bgr_to_qimage(frame) if frame is not None else None
        self.update()

    # mapping
    def _geom(self):
        if self.image is None:
            return 1.0, 0.0, 0.0
        s = min(self.width() / self.image.width(), self.height() / self.image.height())
        ox = (self.width() - self.image.width() * s) / 2
        oy = (self.height() - self.image.height() * s) / 2
        return s, ox, oy

    def to_widget(self, x, y):
        s, ox, oy = self._geom()
        return QPointF(ox + x * s, oy + y * s)

    def to_frame(self, p: QPointF):
        s, ox, oy = self._geom()
        return (p.x() - ox) / s, (p.y() - oy) / s

    def paintEvent(self, ev):
        qp = QPainter(self)
        qp.fillRect(self.rect(), BG)
        if self.image is not None:
            s, ox, oy = self._geom()
            qp.drawImage(QRectF(ox, oy, self.image.width() * s, self.image.height() * s), self.image)
            qp.setRenderHint(QPainter.Antialiasing)
            if self.overlay_fn is not None:
                self.overlay_fn(qp, self.to_widget)
            if self.roi is not None:
                x, y, w, h = self.roi
                qp.setPen(QPen(QColor(255, 255, 0), 1, Qt.DashLine))
                qp.drawRect(QRectF(self.to_widget(x, y), self.to_widget(x + w, y + h)))
            if self.box is not None:
                x, y, w, h = self.box
                qp.setPen(QPen(self.box_color, 2))
                qp.setBrush(Qt.NoBrush)
                qp.drawRect(QRectF(self.to_widget(x, y), self.to_widget(x + w, y + h)))
        if self.message:
            qp.setPen(QColor(255, 255, 255))
            qp.setFont(QFont("Segoe UI", 10))
            qp.drawText(self.rect().adjusted(8, 6, -8, -6), Qt.AlignTop | Qt.AlignLeft | Qt.TextWordWrap, self.message)

    def mousePressEvent(self, ev):
        if self.draw_enabled and ev.button() == Qt.LeftButton and self.image is not None:
            self._drag = self.to_frame(ev.position())

    def mouseMoveEvent(self, ev):
        if self._drag is not None:
            x0, y0 = self._drag
            x1, y1 = self.to_frame(ev.position())
            self.box = (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag is not None:
            self._drag = None
            if self.box is not None and self.box[2] >= 4 and self.box[3] >= 4:
                b = tuple(float(round(v)) for v in self.box)
                self.box = b
                self.boxDrawn.emit(b)
            self.update()


class ReferenceView(FrameCanvas):
    """Reference video at the current time with the tracked box, the reference path and
    the prototype's path projected back into the video (camera-compensated)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.track = None
        self.ppu = 1.0
        self.k = 0
        self.proto_xy = None      # (n, 2) world units, same sampling as the reference
        self.proto_now = None     # (x, y) world units at the current time
        self.char_size = (0.8, 1.0)
        self.compare = True
        self.overlay_fn = self._overlay

    def set_context(self, track, ppu, char_h):
        self.track = track
        self.ppu = ppu
        self.char_size = (0.75 * char_h, char_h)

    def _world_to_screen(self, wx, wy, k):
        tr = self.track
        foot0 = tr.foot_screen()[0]
        X0 = foot0[0] + tr.camera.cam_x[0]
        Y0 = foot0[1] + tr.camera.cam_y[0]
        cx = tr.camera.cam_x[k] if tr.camera_enabled else 0.0
        cy = tr.camera.cam_y[k] if tr.camera_enabled else 0.0
        if not tr.camera_enabled:
            X0, Y0 = foot0
        return wx * self.ppu + X0 - cx, -wy * self.ppu + Y0 - cy

    def _overlay(self, qp: QPainter, tw):
        tr = self.track
        if tr is None or len(tr.frames) == 0:
            return
        k = int(np.clip(self.k, 0, len(tr.frames) - 1))
        # reference foot path in the current frame's screen coordinates
        foot = tr.foot_screen()
        if tr.camera_enabled:
            pts = foot + np.stack([tr.camera.cam_x, tr.camera.cam_y], 1)[:len(foot)] - [tr.camera.cam_x[k], tr.camera.cam_y[k]]
        else:
            pts = foot
        path = QPainterPath()
        for i, (x, y) in enumerate(pts):
            p = tw(x, y)
            path.moveTo(p) if i == 0 else path.lineTo(p)
        qp.setPen(QPen(REF_COLOR, 2))
        qp.setBrush(Qt.NoBrush)
        qp.drawPath(path)
        # tracked box (red when the frame is flagged as lost)
        x, y, w, h = tr.bbox[k]
        qp.setPen(QPen(QColor(255, 60, 60) if tr.lost[k] else QColor(60, 255, 90), 2))
        qp.drawRect(QRectF(tw(x, y), tw(x + w, y + h)))
        if self.compare and self.proto_xy is not None:
            path = QPainterPath()
            for i, (wx, wy) in enumerate(self.proto_xy):
                sx, sy = self._world_to_screen(wx, wy, k)
                p = tw(sx, sy)
                path.moveTo(p) if i == 0 else path.lineTo(p)
            qp.setPen(QPen(PROTO_COLOR, 2, Qt.DashLine))
            qp.drawPath(path)
            if self.proto_now is not None:
                sx, sy = self._world_to_screen(self.proto_now[0], self.proto_now[1], k)
                bw, bh = self.char_size[0] * self.ppu, self.char_size[1] * self.ppu
                qp.setPen(QPen(PROTO_COLOR, 2))
                qp.drawRect(QRectF(tw(sx - bw / 2, sy - bh), tw(sx + bw / 2, sy)))


class PrototypeView(QWidget):
    """World-space view of the prototype. Playback mode shows the reference vs prototype for
    the selected clip; play mode lets the user drive the fitted controller with A/D + Space."""
    playStateChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 200)
        self.setFocusPolicy(Qt.StrongFocus)
        self.ref_xy = None
        self.proto_xy = None
        self.ref_now = None
        self.proto_now = None
        self.floors: list[tuple[float, float, float]] = []   # (x0, x1, y) observed ground levels
        self.char_h = 1.0
        self.view = None   # (cx, cy, scale)
        # play mode
        self.playing = False
        self.pg: Playground | None = None
        self.keys = set()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.dt = 1 / 60
        self.info = ""

    # ---- playback data
    def set_playback(self, ref_xy, proto_xy, floors, char_h):
        self.ref_xy, self.proto_xy, self.floors, self.char_h = ref_xy, proto_xy, floors, char_h
        self.update()

    def set_now(self, ref_now, proto_now):
        self.ref_now, self.proto_now = ref_now, proto_now
        self.update()

    # ---- play mode
    def start_play(self, params):
        # the playground is in units where the character is char_h tall
        level = demo_level(params)
        self.pg = Playground(params, level, width=0.75 * self.char_h, height=self.char_h)
        self.pg.reset(0.0, 0.0)
        self.playing = True
        self.keys.clear()
        self.timer.start(int(self.dt * 1000))
        self.setFocus()
        self.playStateChanged.emit(True)
        self.update()

    def update_params(self, params):
        if self.pg is not None:
            self.pg.params = params

    def stop_play(self):
        self.playing = False
        self.timer.stop()
        self.playStateChanged.emit(False)
        self.update()

    def keyPressEvent(self, ev):
        if self.playing and not ev.isAutoRepeat():
            self.keys.add(ev.key())
            if ev.key() == Qt.Key_R:
                self.pg.reset(0.0, 0.0)
            if ev.key() == Qt.Key_Escape:
                self.stop_play()
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):
        if self.playing and not ev.isAutoRepeat():
            self.keys.discard(ev.key())
        super().keyReleaseEvent(ev)

    def focusOutEvent(self, ev):
        self.keys.clear()
        super().focusOutEvent(ev)

    def _tick(self):
        if not self.playing or self.pg is None:
            return
        left = Qt.Key_A in self.keys or Qt.Key_Left in self.keys
        right = Qt.Key_D in self.keys or Qt.Key_Right in self.keys
        jump = Qt.Key_Space in self.keys or Qt.Key_W in self.keys or Qt.Key_Up in self.keys
        self.pg.step(int(right) - int(left), jump, self.dt)
        s = self.pg.player.state
        if s.y < -30:
            self.pg.reset(0.0, 0.0)
        self.info = (f"x {s.x:6.2f} ch   y {s.y:5.2f} ch   vx {s.vx:6.2f} ch/s   vy {s.vy:6.2f} ch/s   "
                     f"{'grounded' if s.grounded else 'air'}   (ch = character heights)")
        self.update()

    # ---- painting
    def _auto_view(self):
        W, H = max(1, self.width()), max(1, self.height())
        if self.playing and self.pg is not None:
            s = self.pg.player.state
            span = max(12.0 * self.char_h, 1.0)
            scale = min(W / span, H / (span * 0.6))
            return s.x, s.y + 0.25 * span * 0.6, scale
        pts = [p for p in (self.ref_xy, self.proto_xy) if p is not None and len(p)]
        if not pts:
            return 0.0, 0.0, 40.0
        a = np.vstack(pts)
        x0, x1 = a[:, 0].min(), a[:, 0].max()
        y0, y1 = a[:, 1].min(), a[:, 1].max()
        y0 -= self.char_h * 0.5
        y1 += self.char_h * 1.5
        wspan = max(x1 - x0, 4 * self.char_h) * 1.15
        hspan = max(y1 - y0, 3 * self.char_h) * 1.25
        scale = min(W / wspan, H / hspan)
        return (x0 + x1) / 2, (y0 + y1) / 2, scale

    def paintEvent(self, ev):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.fillRect(self.rect(), QColor(18, 20, 26))
        cx, cy, sc = self._auto_view()
        W, H = self.width(), self.height()

        def tw(x, y):
            return QPointF(W / 2 + (x - cx) * sc, H / 2 - (y - cy) * sc)

        # grid
        qp.setPen(QPen(QColor(40, 44, 54), 1))
        step = self.char_h
        gx0 = math.floor((cx - W / 2 / sc) / step) * step
        x = gx0
        while x < cx + W / 2 / sc:
            p = tw(x, 0); qp.drawLine(QPointF(p.x(), 0), QPointF(p.x(), H)); x += step
        y = math.floor((cy - H / 2 / sc) / step) * step
        while y < cy + H / 2 / sc:
            p = tw(0, y); qp.drawLine(QPointF(0, p.y()), QPointF(W, p.y())); y += step
        bw, bh = 0.75 * self.char_h, self.char_h
        if self.playing and self.pg is not None:
            qp.setPen(Qt.NoPen)
            qp.setBrush(QBrush(QColor(70, 78, 96)))
            for r in self.pg.level:
                qp.drawRect(QRectF(tw(r.x0, r.y1), tw(r.x1, r.y0)))
            if len(self.pg.trail) > 1:
                path = QPainterPath(); path.moveTo(tw(*self.pg.trail[0]))
                for p in self.pg.trail[1:]:
                    path.lineTo(tw(*p))
                qp.setPen(QPen(QColor(80, 220, 255, 120), 1.5)); qp.setBrush(Qt.NoBrush); qp.drawPath(path)
            s = self.pg.player.state
            bw, bh = self.pg.player.width, self.pg.player.height
            qp.setPen(Qt.NoPen); qp.setBrush(QBrush(QColor(245, 245, 245)))
            qp.drawRect(QRectF(tw(s.x - bw / 2, s.y + bh), tw(s.x + bw / 2, s.y)))
            qp.setPen(QColor(220, 220, 220))
            qp.drawText(10, 20, "PLAY MODE — A/D or ←/→ move, Space jump, R reset, Esc stop")
            qp.drawText(10, 40, self.info)
            return
        # observed floors
        qp.setPen(QPen(QColor(110, 120, 140), 2))
        for x0, x1, y in self.floors:
            qp.drawLine(tw(x0, y), tw(x1, y))
        for data, color, style in ((self.ref_xy, REF_COLOR, Qt.SolidLine), (self.proto_xy, PROTO_COLOR, Qt.SolidLine)):
            if data is None or len(data) < 2:
                continue
            path = QPainterPath(); path.moveTo(tw(*data[0]))
            for p in data[1:]:
                path.lineTo(tw(*p))
            qp.setPen(QPen(color, 2, style)); qp.setBrush(Qt.NoBrush); qp.drawPath(path)
        if self.ref_now is not None:
            x, y = self.ref_now
            qp.setPen(QPen(REF_COLOR, 2, Qt.DashLine)); qp.setBrush(Qt.NoBrush)
            qp.drawRect(QRectF(tw(x - bw / 2, y + bh), tw(x + bw / 2, y)))
        if self.proto_now is not None:
            x, y = self.proto_now
            qp.setPen(Qt.NoPen); qp.setBrush(QBrush(QColor(245, 245, 245)))
            qp.drawRect(QRectF(tw(x - bw / 2, y + bh), tw(x + bw / 2, y)))
        qp.setPen(REF_COLOR); qp.drawText(10, 20, "— reference")
        qp.setPen(PROTO_COLOR); qp.drawText(100, 20, "— prototype")
