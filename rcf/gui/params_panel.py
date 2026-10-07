"""Model panel: candidate model choice, live-editable parameters, fit error and confidence."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGridLayout, QLabel, QDoubleSpinBox, QSlider, QComboBox,
                               QGroupBox, QTextBrowser, QHBoxLayout, QCheckBox)

from ..model import ControllerParams, CANDIDATE_MODELS, MODELS_BY_KEY
from ..fitting.metrics import METRIC_NAMES, METRIC_LABELS

# name, label, unit, min, max, step.  "ch" = character heights (the fit's length unit)
FIELDS = [
    ("max_speed", "Max Speed", "ch/s", 0.1, 200, 0.05),
    ("ground_accel", "Ground Accel", "ch/s²", 0.1, 3000, 0.5),
    ("ground_decel", "Deceleration", "ch/s²", 0.1, 3000, 0.5),
    ("turn_accel", "Turn Accel", "ch/s²", 0.1, 3000, 0.5),
    ("jump_velocity", "Jump Velocity", "ch/s", 0.1, 300, 0.05),
    ("rise_gravity", "Rise Gravity", "ch/s²", 0.1, 3000, 0.5),
    ("fall_gravity", "Fall Gravity", "ch/s²", 0.1, 3000, 0.5),
    ("jump_cut", "Jump Cut", "×vy", 0.0, 1.0, 0.01),
    ("release_gravity_mult", "Release Grav ×", "×", 1.0, 8.0, 0.05),
    ("air_control", "Air Control", "×", 0.0, 1.5, 0.01),
    ("apex_gravity_mult", "Apex Gravity ×", "×", 0.1, 1.0, 0.01),
    ("max_fall_speed", "Max Fall (0=∞)", "ch/s", 0.0, 500, 0.1),
]

TIPS = {
    "max_speed": "Top running speed.",
    "ground_accel": "How fast the character speeds up towards Max Speed on the ground.",
    "ground_decel": "How fast it slows down when no direction is held (and when above Max Speed).",
    "turn_accel": "Braking rate when the opposite direction is pressed (until speed crosses zero).",
    "jump_velocity": "Upward speed at the moment of the jump.",
    "rise_gravity": "Gravity while moving up.",
    "fall_gravity": "Gravity while moving down. Higher than Rise Gravity = snappier fall.",
    "jump_cut": "Variable jump (model D): upward speed is multiplied by this when jump is released early. 1 = off.",
    "release_gravity_mult": "Variable jump (model D2): rise gravity × this while jump is not held. 1 = off.",
    "air_control": "Horizontal acceleration/braking in the air as a fraction of the ground values.",
    "apex_gravity_mult": "Model E: gravity × this near the top of the jump while jump is held. 1 = off.",
    "max_fall_speed": "Terminal fall speed. 0 = unlimited.",
}

# which params each model actually uses (shown bold)
def _model_fields(key: str) -> set[str]:
    m = MODELS_BY_KEY[key]
    s = set(m.free)
    if "gravity" in s:
        s |= {"rise_gravity", "fall_gravity"}
    if "ground_accel" in s:
        s.add("turn_accel")
    return s


class ParamsPanel(QWidget):
    paramsChanged = Signal(object)    # ControllerParams
    modelChanged = Signal(str)        # "auto" or key

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        top = QHBoxLayout()
        top.addWidget(QLabel("Model"))
        self.model = QComboBox()
        self.model.addItem("Auto (search A–E, pick simplest adequate)", "auto")
        for m in CANDIDATE_MODELS:
            self.model.addItem(m.title, m.key)
        self.model.currentIndexChanged.connect(lambda _: self._model_changed())
        top.addWidget(self.model, 1)
        lay.addLayout(top)

        box = QGroupBox("Controller parameters (live – edit to try)")
        g = QGridLayout(box)
        g.setVerticalSpacing(2)
        self.spins: dict[str, QDoubleSpinBox] = {}
        self.sliders: dict[str, QSlider] = {}
        self.labels: dict[str, QLabel] = {}
        self._updating = False
        for row, (n, label, unit, lo, hi, step) in enumerate(FIELDS):
            lab = QLabel(label)
            lab.setToolTip(TIPS.get(n, "") + "  (ch = character heights)")
            self.labels[n] = lab
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi); sp.setSingleStep(step); sp.setDecimals(3 if hi <= 8 else 2)
            sp.setKeyboardTracking(False)
            sl = QSlider(Qt.Horizontal)
            sl.setRange(0, 1000)
            sp.valueChanged.connect(lambda v, n=n: self._spin_changed(n, v))
            sl.valueChanged.connect(lambda v, n=n: self._slider_changed(n, v))
            g.addWidget(lab, row, 0)
            g.addWidget(sp, row, 1)
            g.addWidget(QLabel(unit), row, 2)
            g.addWidget(sl, row, 3)
            self.spins[n], self.sliders[n] = sp, sl
        g.setColumnStretch(3, 1)
        lay.addWidget(box)

        self.err = QLabel("Fit error: –")
        f = QFont(); f.setPointSize(13); f.setBold(True)
        self.err.setFont(f)
        self.err.setToolTip("Trajectory error: RMS distance between reference and prototype positions, as a "
                            "percentage of each clip's extent of motion, averaged over clips. ≤5 % looks very "
                            "close; >8 % means the model does not explain the clip.")
        lay.addWidget(self.err)
        self.metrics = QLabel("")
        self.metrics.setTextFormat(Qt.RichText)
        self.metrics.setToolTip("Velocity: RMS speed difference / peak speed. Apex & landing timing: time "
                                "difference / air time. Max height: / jump height. Horizontal: net "
                                "displacement difference / horizontal extent. Timing metrics are coarse at "
                                "low frame rates.")
        lay.addWidget(self.metrics)
        self.report = QTextBrowser()
        self.report.setMinimumHeight(120)
        lay.addWidget(self.report, 1)
        self.params = ControllerParams()
        self.set_params(self.params)
        self._model_changed()

    # ---- params
    def set_params(self, p: ControllerParams):
        self.params = p.copy()
        self._updating = True
        for n, *_ in FIELDS:
            v = getattr(p, n)
            if n == "max_fall_speed" and not math.isfinite(v):
                v = 0.0
            self.spins[n].setValue(v)
            self._set_slider(n, v)
        self._updating = False

    def _set_slider(self, n, v):
        sp = self.spins[n]
        lo, hi = sp.minimum(), sp.maximum()
        # log scale for wide ranges
        if hi / max(lo, 1e-3) > 50 and lo > 0:
            f = math.log(max(v, lo) / lo) / math.log(hi / lo)
        else:
            f = (v - lo) / (hi - lo)
        self.sliders[n].blockSignals(True)
        self.sliders[n].setValue(int(round(1000 * min(max(f, 0), 1))))
        self.sliders[n].blockSignals(False)

    def _slider_changed(self, n, iv):
        if self._updating:
            return
        sp = self.spins[n]
        lo, hi = sp.minimum(), sp.maximum()
        f = iv / 1000
        v = lo * (hi / lo) ** f if (hi / max(lo, 1e-3) > 50 and lo > 0) else lo + f * (hi - lo)
        sp.setValue(v)

    def _spin_changed(self, n, v):
        if self._updating:
            return
        self._set_slider(n, v)
        if n == "max_fall_speed":
            v = math.inf if v <= 0 else v
        setattr(self.params, n, v)
        if n == "apex_gravity_mult" and v != 1.0 and self.params.apex_threshold <= 0:
            self.params.apex_threshold = 0.35 * self.params.jump_velocity
        self.paramsChanged.emit(self.params.copy())

    # ---- model
    def model_key(self) -> str:
        return self.model.currentData()

    def set_model_key(self, key: str):
        i = self.model.findData(key)
        if i >= 0:
            self.model.blockSignals(True); self.model.setCurrentIndex(i); self.model.blockSignals(False)
            self._model_changed(emit=False)

    def _model_changed(self, emit=True):
        key = self.model_key()
        used = _model_fields(key) if key != "auto" else {n for n, *_ in FIELDS}
        for n, lab in self.labels.items():
            f = lab.font(); f.setBold(n in used); lab.setFont(f)
            lab.setStyleSheet("" if n in used else "color: gray")
        if emit:
            self.modelChanged.emit(key)

    # ---- results
    def show_error(self, overall: float | None, values: dict | None = None, live: bool = False, confidence=None):
        if overall is None or not math.isfinite(overall):
            self.err.setText("Fit error: –")
        else:
            tag = " (fitting…)" if live else ""
            conf = f"   Confidence: {confidence}" if confidence else ""
            self.err.setText(f"Fit error: {100 * overall:.1f}%{tag}{conf}")
        if values:
            rows = "".join(f"<tr><td>{METRIC_LABELS[k]}</td><td align='right'>&nbsp;&nbsp;"
                           f"{'n/a' if values.get(k) is None else f'{100 * values[k]:.1f}%'}</td></tr>"
                           for k in METRIC_NAMES)
            self.metrics.setText(f"<table>{rows}</table>")
        else:
            self.metrics.setText("")
