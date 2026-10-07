"""Timeline: input annotation lanes + reference/prototype curves, with editing.

Lanes: Input (horizontal axis segments), Jump (press..release bars), Y(t), X(t).
Click = seek. Drag an axis-change marker or a jump bar edge = retime it.
Right-click = context menu to add/remove input events (manual input annotation).
"""
from __future__ import annotations

import copy

import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QBrush, QPainterPath, QAction
from PySide6.QtWidgets import QWidget, QMenu

from ..sim import Scenario, InputTrack
from .views import REF_COLOR, PROTO_COLOR

LANE_INPUT, LANE_JUMP, LANE_Y, LANE_X = range(4)
LABELS = ["Input", "Jump", "Y", "X"]
LEFT = 64


class TimelineWidget(QWidget):
    seek = Signal(float)
    scenarioEdited = Signal(object)   # Scenario

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(190)
        self.setMouseTracking(True)
        self.t = None
        self.ref = None          # (t, x, y, valid)
        self.proto = None        # (t, x, y)
        self.scenario: Scenario | None = None
        self.phases = []
        self.now = 0.0
        self._drag = None        # ("axis", idx) | ("press", idx) | ("release", idx)
        self.editable = True

    def set_data(self, t, ref, proto, scenario, phases):
        self.t, self.ref, self.proto, self.scenario, self.phases = t, ref, proto, scenario, phases
        self.update()

    def set_proto(self, proto):
        self.proto = proto
        self.update()

    def set_now(self, t):
        self.now = t
        self.update()

    # ---- geometry
    def _lanes(self):
        h = self.height()
        top = 4
        small = 22
        rest = max(40, (h - top - 2 * small - 40) / 2)
        return [(top, small), (top + small + 2, small), (top + 2 * small + 6, rest), (top + 2 * small + 10 + rest, rest)]

    def _t0t1(self):
        if self.t is None or len(self.t) < 2:
            return 0.0, 1.0
        return float(self.t[0]), float(self.t[-1])

    def tx(self, t):
        t0, t1 = self._t0t1()
        return LEFT + (t - t0) / max(t1 - t0, 1e-9) * (self.width() - LEFT - 8)

    def xt(self, x):
        t0, t1 = self._t0t1()
        return t0 + (x - LEFT) / max(self.width() - LEFT - 8, 1) * (t1 - t0)

    # ---- painting
    def paintEvent(self, ev):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.fillRect(self.rect(), QColor(24, 26, 31))
        lanes = self._lanes()
        qp.setPen(QColor(170, 175, 185))
        for (y, h), lab in zip(lanes, LABELS):
            qp.fillRect(QRectF(LEFT, y, self.width() - LEFT - 8, h), QColor(33, 36, 43))
            qp.drawText(QRectF(4, y, LEFT - 8, h), Qt.AlignVCenter | Qt.AlignLeft, lab)
        if self.t is None:
            qp.drawText(self.rect(), Qt.AlignCenter, "Add and track a clip to see its timeline")
            return
        t0, t1 = self._t0t1()
        sc = self.scenario
        # phases (airborne shading)
        for ph in self.phases:
            te = ph.t_land if ph.t_land is not None else t1
            qp.fillRect(QRectF(self.tx(ph.t_off), lanes[LANE_Y][0], self.tx(te) - self.tx(ph.t_off), lanes[LANE_Y][1]),
                        QColor(50, 56, 70))
            if ph.t_apex is not None:
                qp.setPen(QPen(QColor(120, 120, 140), 1, Qt.DotLine))
                x = self.tx(ph.t_apex)
                qp.drawLine(QPointF(x, lanes[LANE_Y][0]), QPointF(x, lanes[LANE_Y][0] + lanes[LANE_Y][1]))
        if sc is not None:
            # input lane
            y, h = lanes[LANE_INPUT]
            ev = [(sc.t_start, sc.inputs.axis0)] + list(sc.inputs.axis_events) + [(t1, None)]
            for (ta, a), (tb, _) in zip(ev[:-1], ev[1:]):
                if a:
                    col = QColor(90, 200, 120) if a > 0 else QColor(110, 150, 255)
                    qp.fillRect(QRectF(self.tx(ta), y + 3, self.tx(tb) - self.tx(ta), h - 6), col)
                    qp.setPen(QColor(10, 10, 10))
                    qp.drawText(QRectF(self.tx(ta) + 2, y, 60, h), Qt.AlignVCenter, "Right" if a > 0 else "Left")
            qp.setPen(QPen(QColor(240, 240, 240), 2))
            for te, a in sc.inputs.axis_events:
                x = self.tx(te)
                qp.drawLine(QPointF(x, y), QPointF(x, y + h))
            # jump lane
            y, h = lanes[LANE_JUMP]
            for p, r in sc.inputs.jumps:
                qp.fillRect(QRectF(self.tx(p), y + 3, max(2.0, self.tx(r) - self.tx(p)), h - 6), QColor(255, 160, 60))
                qp.setPen(QPen(QColor(255, 255, 255), 2))
                qp.drawLine(QPointF(self.tx(p), y), QPointF(self.tx(p), y + h))
        # curves
        for lane, idx in ((LANE_Y, 2), (LANE_X, 1)):
            y, h = lanes[lane]
            vals = [self.ref[idx][self.ref[3]]] if self.ref is not None else []
            if self.proto is not None:
                vals.append(self.proto[idx])
            if not vals:
                continue
            allv = np.concatenate(vals)
            lo, hi = float(np.min(allv)), float(np.max(allv))
            if hi - lo < 1e-6:
                hi = lo + 1.0
            pad = 0.08 * (hi - lo)
            lo -= pad; hi += pad

            def ty(v):
                return y + h - (v - lo) / (hi - lo) * h
            if self.ref is not None:
                qp.setPen(Qt.NoPen); qp.setBrush(QBrush(REF_COLOR))
                tt, vv, valid = self.ref[0], self.ref[idx], self.ref[3]
                for a, b, ok in zip(tt, vv, valid):
                    if ok:
                        qp.drawEllipse(QPointF(self.tx(a), ty(b)), 1.8, 1.8)
            if self.proto is not None:
                path = QPainterPath()
                for i, (a, b) in enumerate(zip(self.proto[0], self.proto[idx])):
                    p = QPointF(self.tx(a), ty(b))
                    path.moveTo(p) if i == 0 else path.lineTo(p)
                qp.setPen(QPen(PROTO_COLOR, 1.6)); qp.setBrush(Qt.NoBrush); qp.drawPath(path)
        # time ticks
        span = t1 - t0
        step = next((s for s in (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0) if span / s <= 12), 20.0)
        qp.setPen(QColor(120, 125, 135))
        tt = np.ceil(t0 / step) * step
        ybot = lanes[LANE_X][0] + lanes[LANE_X][1]
        while tt <= t1 + 1e-9:
            x = self.tx(tt)
            qp.drawLine(QPointF(x, ybot), QPointF(x, ybot + 4))
            qp.drawText(QRectF(x - 25, ybot + 3, 50, 14), Qt.AlignHCenter | Qt.AlignTop, f"{tt:g}s")
            tt += step
        # playhead
        x = self.tx(self.now)
        qp.setPen(QPen(QColor(255, 255, 255), 1))
        qp.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        qp.drawText(QRectF(4, self.height() - 18, LEFT, 16), Qt.AlignLeft, f"{self.now:5.2f}s")

    # ---- interaction
    def _hit(self, pos):
        if self.scenario is None:
            return None
        lanes = self._lanes()
        y = pos.y()
        sc = self.scenario
        if lanes[LANE_INPUT][0] <= y <= lanes[LANE_INPUT][0] + lanes[LANE_INPUT][1]:
            for i, (te, _) in enumerate(sc.inputs.axis_events):
                if abs(self.tx(te) - pos.x()) < 6:
                    return ("axis", i)
        if lanes[LANE_JUMP][0] <= y <= lanes[LANE_JUMP][0] + lanes[LANE_JUMP][1]:
            for i, (p, r) in enumerate(sc.inputs.jumps):
                if abs(self.tx(r) - pos.x()) < 6:
                    return ("release", i)
                if abs(self.tx(p) - pos.x()) < 6:
                    return ("press", i)
        return None

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            hit = self._hit(ev.position()) if self.editable else None
            if hit:
                self._drag = hit
            else:
                self.seek.emit(float(np.clip(self.xt(ev.position().x()), *self._t0t1())))

    def mouseMoveEvent(self, ev):
        hit = self._hit(ev.position())
        self.setCursor(Qt.SizeHorCursor if (hit or self._drag) else Qt.ArrowCursor)
        if self._drag is None:
            if ev.buttons() & Qt.LeftButton:
                self.seek.emit(float(np.clip(self.xt(ev.position().x()), *self._t0t1())))
            return
        t = float(np.clip(self.xt(ev.position().x()), *self._t0t1()))
        sc = self._editable_copy()
        kind, i = self._drag
        if kind == "axis":
            sc.inputs.axis_events[i] = (t, sc.inputs.axis_events[i][1])
        elif kind == "press":
            p, r = sc.inputs.jumps[i]
            sc.inputs.jumps[i] = (min(t, r - 0.005), r)
        elif kind == "release":
            p, r = sc.inputs.jumps[i]
            sc.inputs.jumps[i] = (p, max(t, p + 0.005))
        self.scenario = sc
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag is not None:
            self._drag = None
            sc = self._editable_copy()
            sc.inputs.axis_events.sort()
            self.scenarioEdited.emit(sc)

    def _editable_copy(self) -> Scenario:
        return copy.deepcopy(self.scenario)

    def contextMenuEvent(self, ev):
        if self.scenario is None or not self.editable:
            return
        t = float(np.clip(self.xt(ev.pos().x()), *self._t0t1()))
        hit = self._hit(QPointF(ev.pos()))
        m = QMenu(self)

        def add_axis(v):
            sc = self._editable_copy()
            sc.inputs.axis_events.append((t, v))
            sc.inputs.axis_events.sort()
            self.scenarioEdited.emit(sc)

        def add_jump(hold):
            sc = self._editable_copy()
            sc.inputs.jumps.append((t, t + hold))
            sc.inputs.jumps.sort()
            self.scenarioEdited.emit(sc)

        def delete(h):
            sc = self._editable_copy()
            kind, i = h
            if kind == "axis":
                del sc.inputs.axis_events[i]
            else:
                del sc.inputs.jumps[i]
            self.scenarioEdited.emit(sc)

        for label, v in (("Right press from here", 1), ("Left press from here", -1), ("Release (no direction) from here", 0)):
            a = QAction(label, m); a.triggered.connect(lambda _=False, v=v: add_axis(v)); m.addAction(a)
        m.addSeparator()
        for label, hold in (("Jump press here (short, 0.1 s)", 0.1), ("Jump press here (long, 0.5 s)", 0.5)):
            a = QAction(label, m); a.triggered.connect(lambda _=False, hold=hold: add_jump(hold)); m.addAction(a)
        if hit:
            m.addSeparator()
            a = QAction("Delete this event", m); a.triggered.connect(lambda _=False: delete(hit)); m.addAction(a)
        m.exec(ev.globalPos())
