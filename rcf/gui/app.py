"""Reference Controller Fitter – main window."""
from __future__ import annotations

import math
import sys
import time
import traceback
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QAction, QKeySequence, QColor, QPalette
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QListWidget,
                               QListWidgetItem, QPushButton, QFileDialog, QMessageBox, QDockWidget, QLabel,
                               QDoubleSpinBox, QDialog, QFormLayout, QDialogButtonBox, QComboBox, QCheckBox,
                               QProgressBar, QToolBar)

from .. import __version__
from ..model import ControllerParams, MODELS_BY_KEY
from ..project import Project, Clip
from ..sim import simulate
from ..fitting.metrics import compute_metrics, aggregate
from ..fitting.selection import Selection
from ..fitting.fit import FitResult
from ..tracking.video import FrameCache
from ..export.exporters import export_json, export_unity, export_godot, scaled
from .views import ReferenceView, PrototypeView
from .timeline import TimelineWidget
from .params_panel import ParamsPanel
from .clip_dialog import ClipDialog
from .workers import FitThread

ROOT = Path(__file__).resolve().parents[2]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Reference Controller Fitter {__version__}")
        self.resize(1500, 950)
        self.project = Project()
        self.selection: Selection | None = None
        self.fit_meta: dict = {}
        self.fitted_scen: dict[str, object] = {}
        self.cur = -1
        self.t = 0.0
        self.caches: dict[str, FrameCache] = {}
        self.fit_thread: FitThread | None = None
        self._gen = 0
        self._confidence: str | None = None
        self._pre_fit = None
        self._sim_cache = None

        # ---- central: views + timeline
        self.ref_view = ReferenceView()
        self.proto_view = PrototypeView()
        top = QSplitter(Qt.Horizontal)
        lw = QWidget(); ll = QVBoxLayout(lw); ll.setContentsMargins(0, 0, 0, 0)
        lt = QLabel("  Reference video"); lt.setStyleSheet("font-weight: bold")
        ll.addWidget(lt); ll.addWidget(self.ref_view, 1)
        rw = QWidget(); rl = QVBoxLayout(rw); rl.setContentsMargins(0, 0, 0, 0)
        rt = QLabel("  Playable prototype"); rt.setStyleSheet("font-weight: bold")
        rl.addWidget(rt); rl.addWidget(self.proto_view, 1)
        top.addWidget(lw); top.addWidget(rw)
        top.setSizes([750, 750])
        self.timeline = TimelineWidget()
        self.timeline.seek.connect(self.seek)
        self.timeline.scenarioEdited.connect(self._scenario_edited)
        vs = QSplitter(Qt.Vertical)
        vs.addWidget(top); vs.addWidget(self.timeline)
        vs.setSizes([620, 260])
        self.setCentralWidget(vs)

        # ---- left dock: clips
        dock = QDockWidget("Reference clips", self)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        w = QWidget(); l = QVBoxLayout(w)
        self.clip_list = QListWidget()
        self.clip_list.currentRowChanged.connect(self.select_clip)
        self.clip_list.itemChanged.connect(self._clip_check)
        l.addWidget(self.clip_list, 1)
        b_add = QPushButton("Add clip from video…"); b_add.clicked.connect(self.add_clip)
        b_edit = QPushButton("Edit / re-track…"); b_edit.clicked.connect(self.edit_clip)
        b_del = QPushButton("Remove clip"); b_del.clicked.connect(self.remove_clip)
        b_reinf = QPushButton("Reset inputs to auto-inferred"); b_reinf.clicked.connect(self.reset_inputs)
        self.lock_inputs = QCheckBox("Lock this clip's input timings (fitter won't retime them)")
        self.lock_inputs.toggled.connect(self._lock_inputs)
        for b in (b_add, b_edit, b_del, b_reinf):
            l.addWidget(b)
        self._clip_buttons = (b_add, b_edit, b_del, b_reinf, self.lock_inputs)
        l.addWidget(self.lock_inputs)
        self.clip_info = QLabel("")
        self.clip_info.setWordWrap(True)
        l.addWidget(self.clip_info)
        dock.setWidget(w)
        dock.setMinimumWidth(270)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

        # ---- right dock: model
        dock2 = QDockWidget("Model", self)
        dock2.setFeatures(QDockWidget.DockWidgetMovable)
        w2 = QWidget(); l2 = QVBoxLayout(w2); l2.setContentsMargins(2, 2, 2, 2)
        self.params_panel = ParamsPanel()
        self.params_panel.paramsChanged.connect(self._params_edited)
        l2.addWidget(self.params_panel, 1)
        row = QHBoxLayout()
        self.fit_btn = QPushButton("Fit Controller"); self.fit_btn.clicked.connect(self.fit)
        self.fit_btn.setStyleSheet("font-weight: bold; padding: 6px")
        self.play_btn = QPushButton("Play ▶"); self.play_btn.clicked.connect(self.toggle_play)
        self.compare_btn = QPushButton("Compare"); self.compare_btn.setCheckable(True); self.compare_btn.setChecked(True)
        self.compare_btn.toggled.connect(self._compare)
        self.try_btn = QPushButton("Try it (A/D, Space)"); self.try_btn.clicked.connect(self.toggle_try)
        self.export_btn = QPushButton("Export…"); self.export_btn.clicked.connect(self.export)
        for b in (self.fit_btn, self.play_btn, self.compare_btn, self.try_btn, self.export_btn):
            row.addWidget(b)
        l2.addLayout(row)
        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.hide()
        l2.addWidget(self.progress)
        dock2.setWidget(w2)
        dock2.setMinimumWidth(430)
        self.addDockWidget(Qt.RightDockWidgetArea, dock2)
        self.proto_view.playStateChanged.connect(lambda on: self.try_btn.setText("Stop trying" if on else "Try it (A/D, Space)"))

        # ---- menus
        mb = self.menuBar()
        fm = mb.addMenu("&File")
        self._act(fm, "Add clip from video…", self.add_clip, QKeySequence("Ctrl+I"))
        self._act(fm, "Open project…", self.open_project, QKeySequence.Open)
        self._act(fm, "Save project", self.save_project, QKeySequence.Save)
        self._act(fm, "Save project as…", lambda: self.save_project(True))
        fm.addSeparator()
        em = fm.addMenu("Open example")
        for f in sorted((ROOT / "examples").glob("*.rcf.json")):
            self._act(em, f.stem.replace(".rcf", ""), lambda _=False, f=f: self.open_project(str(f)))
        fm.addSeparator()
        self._act(fm, "Export…", self.export, QKeySequence("Ctrl+E"))
        self._act(fm, "Load controller JSON…", self.load_controller_json)
        fm.addSeparator()
        self._act(fm, "Quit", self.close, QKeySequence.Quit)
        hm = mb.addMenu("&Help")
        self._act(hm, "Quick start", self.help)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.metric_timer = QTimer(self); self.metric_timer.setSingleShot(True)
        self.metric_timer.timeout.connect(self.update_metrics)
        self.statusBar().showMessage("Add a gameplay clip (File ▸ Add clip) or open an example project.")
        self.params_panel.report.setHtml(self._welcome())

    def _act(self, menu, text, fn, shortcut=None):
        a = QAction(text, self)
        a.triggered.connect(fn)
        if shortcut is not None:
            a.setShortcut(shortcut)
        menu.addAction(a)
        return a

    def _welcome(self):
        return ("<b>Workflow</b><ol><li>Add one or more short clips of the reference game (File ▸ Add clip). "
                "Draw a box around the character in the first frame and press Track.</li>"
                "<li>Check the timeline: inferred inputs (Right/Left, Jump). Right-click to add/remove events, "
                "drag markers to retime.</li><li>Press <b>Fit Controller</b>. All clips are fitted with ONE shared "
                "controller; models A–E are compared and the simplest adequate one is chosen.</li>"
                "<li>Play, compare, edit parameters live, press <b>Try it</b> to play the controller with "
                "A/D + Space, then <b>Export</b> (JSON / Unity C# / Godot).</li></ol>")

    # ------------------------------------------------------------------ clips
    def refresh_clip_list(self, select: int | None = None):
        self.clip_list.blockSignals(True)
        self.clip_list.clear()
        for i, c in enumerate(self.project.clips):
            it = QListWidgetItem(self._clip_label(c, i))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if c.enabled else Qt.Unchecked)
            self.clip_list.addItem(it)
        self.clip_list.blockSignals(False)
        if self.project.clips:
            idx = select if select is not None else max(0, min(self.cur, len(self.project.clips) - 1))
            self.clip_list.setCurrentRow(idx)
            self.select_clip(idx)
        else:
            self.cur = -1

    def _clip_label(self, c: Clip, i: int) -> str:
        s = c.name
        if c.track is not None:
            cam = c.track.camera.level if c.track.camera_enabled else "off"
            s += f"\n   {len(c.track.frames)} fr · cam {cam} · lost {100 * c.track.lost_fraction:.0f}%"
        if self.selection is not None:
            best = self.selection.best
            names = [fc.uid for fc in self._fit_clip_names]
            if c.uid in names:
                m = best.clip_metrics[names.index(c.uid)]
                s += f" · err {100 * m.values['trajectory']:.1f}%"
        if c.manual_scenario is not None:
            s += " · inputs edited"
        return s

    def _clip_check(self, item):
        i = self.clip_list.row(item)
        if 0 <= i < len(self.project.clips):
            self.project.clips[i].enabled = item.checkState() == Qt.Checked
            self.metric_timer.start(100)

    def add_clip(self):
        path, _ = QFileDialog.getOpenFileName(self, "Gameplay video", str(ROOT / "references"),
                                              "Video (*.mp4 *.webm *.mkv *.avi *.mov *.ogv *.gif);;All files (*)")
        if not path:
            return
        try:
            dlg = ClipDialog(path, None, self)
        except Exception as e:
            QMessageBox.warning(self, "Cannot open video", str(e))
            return
        if dlg.exec() == QDialog.Accepted:
            clip = dlg.result_clip
            names = {c.name for c in self.project.clips}
            base, k = clip.name, 2
            while clip.name in names:            # keep names unique for the user
                clip.name = f"{base} ({k})"; k += 1
            self.project.clips.append(clip)
            self.project.update_scales()
            self.refresh_clip_list(len(self.project.clips) - 1)
            self.update_metrics()

    def edit_clip(self):
        if self.cur < 0:
            return
        c = self.project.clips[self.cur]
        if not Path(c.video).exists():
            QMessageBox.warning(self, "Video missing", f"Video file not found:\n{c.video}")
            return
        dlg = ClipDialog(c.video, c, self)
        if dlg.exec() == QDialog.Accepted:
            self.caches.pop(c.video, None)
            self.fitted_scen.pop(c.uid, None)
            self.project.update_scales()
            self.refresh_clip_list(self.cur)
            self.update_metrics()

    def remove_clip(self):
        if self.cur < 0:
            return
        del self.project.clips[self.cur]
        self.refresh_clip_list(max(0, self.cur - 1))
        self.update_metrics()

    def reset_inputs(self):
        if self.cur < 0:
            return
        c = self.project.clips[self.cur]
        c.manual_scenario = None
        self.fitted_scen.pop(c.uid, None)
        self.select_clip(self.cur)

    def _lock_inputs(self, on):
        if self.cur >= 0:
            self.project.clips[self.cur].refine_inputs = not on

    def select_clip(self, i: int):
        if i < 0 or i >= len(self.project.clips):
            return
        self.cur = i
        c = self.project.clips[i]
        self.lock_inputs.blockSignals(True); self.lock_inputs.setChecked(not c.refine_inputs); self.lock_inputs.blockSignals(False)
        if c.track is None:
            self.clip_info.setText("Not tracked yet – press Edit / re-track.")
            return
        try:
            ref, inf = c.reference, c.inference
        except Exception as e:
            self.clip_info.setText(f"Cannot build trajectory: {e}")
            return
        tr = c.track
        notes = inf.notes + inf.warnings + tr.notes
        jumps = sum(1 for p in inf.phases if p.kind == "jump")
        self.clip_info.setText(
            f"<b>{c.name}</b><br>{tr.fps:.1f} fps, {len(tr.frames)} frames, 1 unit = character height = {c.ppu_used:.1f} px<br>"
            f"camera compensation: {'off' if not tr.camera_enabled else f'{tr.camera.level} ({tr.camera.overall_confidence:.2f})'}"
            f"<br>tracking lost: {100 * tr.lost_fraction:.1f}% · jumps found: {jumps}"
            + ("<br><span style='color:#e0a040'>" + "<br>".join(notes) + "</span>" if notes else ""))
        self.ref_view.set_context(tr, c.ppu_used, ref.char_height)
        if c.video not in self.caches:
            try:
                self.caches[c.video] = FrameCache(c.video) if Path(c.video).exists() else None
            except Exception:
                self.caches[c.video] = None
        self._recompute_sim()
        self.t = float(ref.t[0])
        self.seek(self.t)

    def _scenario_for(self, c: Clip):
        return self.fitted_scen.get(c.uid) or c.scenario

    def _scenario_edited(self, sc):
        if self.cur < 0:
            return
        c = self.project.clips[self.cur]
        c.manual_scenario = sc
        self.fitted_scen.pop(c.uid, None)   # user edit overrides the fitter's refined timing
        self._recompute_sim()
        self.seek(self.t)
        self.refresh_label(self.cur)
        self.metric_timer.start(150)
        self.statusBar().showMessage("Input annotation edited – press Fit Controller to refit with it.")

    def refresh_label(self, i):
        it = self.clip_list.item(i)
        if it is not None:
            self.clip_list.blockSignals(True)
            it.setText(self._clip_label(self.project.clips[i], i))
            self.clip_list.blockSignals(False)

    # ------------------------------------------------------------------ simulation / display
    def _recompute_sim(self):
        if self.cur < 0:
            return
        c = self.project.clips[self.cur]
        if c.track is None:
            return
        ref, inf = c.reference, c.inference
        sc = self._scenario_for(c)
        p = self.params_panel.params
        sim = simulate(p, sc, ref.t)
        self._sim_cache = (ref.t, sim)
        proto_xy = np.stack([sim["x"], sim["y"]], 1)
        floors = []
        g = inf.grounded
        i = 0
        while i < len(g):
            if g[i]:
                j = i
                while j + 1 < len(g) and g[j + 1]:
                    j += 1
                floors.append((float(ref.x[i]) - ref.char_height, float(ref.x[j]) + ref.char_height,
                               float(np.median(ref.y[i:j + 1]))))
                i = j + 1
            else:
                i += 1
        ref_xy = np.stack([ref.x, ref.y], 1)[ref.valid]
        self.proto_view.set_playback(ref_xy, proto_xy, floors, ref.char_height)
        self.ref_view.proto_xy = proto_xy
        self.timeline.set_data(ref.t, (ref.t, ref.x, ref.y, ref.valid), (ref.t, sim["x"], sim["y"]), sc, inf.phases)

    def seek(self, t: float):
        if self.cur < 0:
            return
        c = self.project.clips[self.cur]
        if c.track is None:
            return
        ref = c.reference
        self.t = float(np.clip(t, ref.t[0], ref.t[-1]))
        k = int(np.clip(np.searchsorted(ref.t, self.t), 0, len(ref.t) - 1))
        self.ref_view.k = k
        cache = self.caches.get(c.video)
        if cache is not None:
            self.ref_view.set_frame(cache.get(int(c.track.frames[k])))
            self.ref_view.message = ""
        else:
            self.ref_view.set_frame(None)
            self.ref_view.message = f"Video not found: {c.video}\n(trajectory data is still available)"
        if self._sim_cache is not None:
            ts, sim = self._sim_cache
            px = float(np.interp(self.t, ts, sim["x"])); py = float(np.interp(self.t, ts, sim["y"]))
            self.ref_view.proto_now = (px, py)
            self.proto_view.set_now((float(ref.x[k]), float(ref.y[k])) if ref.valid[k] else None, (px, py))
        self.timeline.set_now(self.t)
        self.ref_view.update()

    def toggle_play(self):
        if self.timer.isActive():
            self.timer.stop(); self.play_btn.setText("Play ▶")
        else:
            if self.proto_view.playing:
                self.proto_view.stop_play()
            self.timer.start(16); self.play_btn.setText("Pause ❚❚")

    def _tick(self):
        if self.cur < 0:
            return
        ref = self.project.clips[self.cur].reference
        t = self.t + 0.016
        if t > ref.t[-1]:
            t = ref.t[0]
        self.seek(t)

    def _compare(self, on):
        self.ref_view.compare = on
        self.ref_view.update()

    def toggle_try(self):
        if self.proto_view.playing:
            self.proto_view.stop_play()
        else:
            if self.timer.isActive():
                self.toggle_play()
            if self.cur >= 0 and self.project.clips[self.cur].track is not None:
                self.proto_view.char_h = self.project.clips[self.cur].reference.char_height
            self.proto_view.start_play(self.params_panel.params.copy())

    # ------------------------------------------------------------------ params & metrics
    def _params_edited(self, p: ControllerParams):
        self.project.params = p
        if self._confidence and not self._confidence.startswith("n/a"):
            self._confidence = "n/a (parameters edited – Fit to re-assess)"
        self.proto_view.update_params(p)
        self._recompute_sim()
        self.seek(self.t)
        self.metric_timer.start(120)

    def update_metrics(self):
        fcs = self.project.fit_clips()
        if not fcs:
            self.params_panel.show_error(None)
            return
        p = self.params_panel.params
        ms = []
        for fc in fcs:
            sc = self.fitted_scen.get(fc.uid) or fc.scenario
            try:
                ms.append(compute_metrics(p, sc, fc.ref, fc.inference))
            except Exception:
                pass
        agg = aggregate(ms, [fc.weight for fc in fcs])
        self.params_panel.show_error(agg.values.get("trajectory"), agg.values, confidence=self._confidence)

    # ------------------------------------------------------------------ fitting
    def fit(self):
        if self.fit_thread is not None:
            self.fit_thread.cancel()
            self.statusBar().showMessage("Cancelling fit…")
            return
        fcs = self.project.fit_clips()
        if not fcs:
            QMessageBox.information(self, "Fit", "Add and track at least one clip first.")
            return
        key = self.params_panel.model_key()
        self.fit_thread = FitThread(fcs, None if key == "auto" else key, init=None, parent=self)
        # bound slots -> queued into the GUI thread; stale threads are ignored via _current_fit()
        self.fit_thread.progress.connect(self._fit_progress)
        self.fit_thread.done.connect(self._fit_done)
        self.fit_thread.failed.connect(self._fit_failed)
        self.fit_thread.cancelled.connect(self._fit_cancelled)
        self._fit_clip_names = fcs
        self._pre_fit = (self.params_panel.params.copy(), self.params_panel.report.toHtml(), dict(self.fit_meta),
                         self._confidence)
        self._fit_t0 = time.time()
        self._set_busy(True)
        self.fit_btn.setText("Cancel fit")
        self.progress.show()
        self._fit_log = []
        self._best_live = float("inf")
        self.params_panel.report.setHtml("<b>Fitting…</b> all enabled clips share one controller.")
        self.fit_thread.start()
        self.statusBar().showMessage("Fitting… (all clips share one controller)")

    def _current_fit(self) -> bool:
        """Signals from a fit that was abandoned (project switched) must not touch the UI."""
        s = self.sender()
        return s is None or s is self.fit_thread

    def _fit_progress(self, msg, err, params):
        if not self._current_fit():
            return
        if params is None:
            self.statusBar().showMessage(msg)
            self._fit_log.append(msg)
            self.params_panel.report.setHtml("<b>Fitting…</b><br>" + "<br>".join(self._fit_log[-12:]))
            return
        # show the best controller found so far (other candidate models are being fitted in
        # between, and their intermediate states would make the display jump around)
        if not (err < self._best_live):
            return
        self._best_live = err
        self.params_panel.set_params(params)
        self.project.params = params
        self._recompute_sim()
        self.seek(self.t)
        self.params_panel.show_error(err, None, live=True)

    def _set_busy(self, busy: bool):
        """While fitting: parameters, export and clip edits are locked (they would be overwritten)."""
        for w in (self.params_panel, self.export_btn, self.try_btn, *self._clip_buttons):
            w.setEnabled(not busy)
        self.fit_btn.setText("Cancel fit" if busy else "Fit Controller")
        self.progress.setVisible(busy)

    def _fit_cancelled(self):
        if not self._current_fit():
            return
        self.fit_thread = None
        self._set_busy(False)
        p, html, meta, conf = self._pre_fit
        self.params_panel.set_params(p)
        self.project.params = p
        self.params_panel.report.setHtml(html)
        self.fit_meta, self._confidence = meta, conf
        self._recompute_sim()
        self.seek(self.t)
        self.update_metrics()
        self.statusBar().showMessage("Fit cancelled – previous controller restored.")

    def _fit_done(self, res):
        if not self._current_fit():
            return
        self.fit_thread = None
        self._set_busy(False)
        wall = time.time() - self._fit_t0
        if isinstance(res, Selection):
            self.selection = res
            best = res.best
            self.params_panel.set_model_key("auto")
            report = (f"<b>Chosen model: {best.model.title}</b> — confidence <b>{res.confidence}</b><br>"
                      + "<br>".join(res.explanation)
                      + ("<br><br><b>Notes</b><ul>" + "".join(f"<li>{r}</li>" for r in res.reasons) + "</ul>"
                         if res.reasons else ""))
        else:
            best = res
            self.selection = Selection({best.model.key: best}, best.model.key, {best.model.key: float('nan')}, 0.0)
            from ..fitting.selection import _explain
            _explain(self.selection, self._fit_clip_names)
            report = (f"<b>Fitted model: {best.model.title}</b> — confidence <b>{self.selection.confidence}</b>"
                      + ("<ul>" + "".join(f"<li>{r}</li>" for r in self.selection.reasons) + "</ul>"
                         if self.selection.reasons else ""))
        self.fitted_scen = {fc.uid: sc for fc, sc in zip(self._fit_clip_names, best.scenarios)}
        self.project.params = best.params
        self.project.model_key = best.model.key
        self.project.selection = self.selection.to_dict()
        self.fit_meta = {"model": best.model.key, "fit_error": best.trajectory_error, "confidence": self.selection.confidence}
        self.params_panel.set_params(best.params)
        self.params_panel.report.setHtml(report)
        self._recompute_sim()
        self.seek(self.t)
        self.update_metrics()
        for i in range(len(self.project.clips)):
            self.refresh_label(i)
        self._confidence = self.selection.confidence
        self.update_metrics()
        self.statusBar().showMessage(f"Fit done in {wall:.0f}s – model {best.model.key}, "
                                     f"trajectory error {100 * best.trajectory_error:.1f}%")

    def _fit_failed(self, msg):
        if not self._current_fit():
            return
        self.fit_thread = None
        self._set_busy(False)
        QMessageBox.warning(self, "Fit failed", "The optimizer could not fit the clips.\n\n" + msg)

    # ------------------------------------------------------------------ files
    def open_project(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open project", str(ROOT / "examples"), "RCF project (*.rcf.json)")
        if not path:
            return
        if self.fit_thread is not None:
            self.fit_thread.cancel()
            self.fit_thread.wait(15000)
            self.fit_thread = None
            self._set_busy(False)
        self._gen += 1
        try:
            self.project = Project.load(path)
        except Exception as e:
            QMessageBox.warning(self, "Open project", f"{e}")
            return
        self.selection = None
        self.fitted_scen = {}
        sel = self.project.selection
        if sel:
            # restore fitted input timings and the report of the last fit
            best = sel["results"].get(sel["chosen"], {})
            for c, scd in zip([c for c in self.project.clips if c.enabled and c.track is not None], best.get("scenarios", [])):
                from ..sim import Scenario
                self.fitted_scen[c.uid] = Scenario.from_dict(scd)
            self.fit_meta = {"model": sel["chosen"], "fit_error": best.get("overall", {}).get("values", {}).get("trajectory"),
                             "confidence": sel.get("confidence")}
            self._confidence = sel.get("confidence")
            self.params_panel.report.setHtml(f"<b>Loaded fit: model {sel['chosen']}</b> — confidence "
                                             f"<b>{sel.get('confidence')}</b><br>" + "<br>".join(sel.get("explanation", []))
                                             + "<ul>" + "".join(f"<li>{r}</li>" for r in sel.get("reasons", [])) + "</ul>")
        self.params_panel.set_params(self.project.params)
        self.caches.clear()
        self.cur = 0
        self.refresh_clip_list(0)
        self.update_metrics()
        self.setWindowTitle(f"Reference Controller Fitter {__version__} — {Path(path).name}")
        self.statusBar().showMessage(f"Opened {Path(path).name}: {len(self.project.clips)} clips. "
                                     "Press Play, Fit Controller, or Try it.")

    def save_project(self, ask=False):
        path = self.project.path
        if ask or not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save project", str(ROOT / "my_project.rcf.json"), "RCF project (*.rcf.json)")
        if not path:
            return
        if not path.endswith(".rcf.json"):
            path += ".rcf.json"
        self.project.params = self.params_panel.params
        self.project.save(path)
        self.statusBar().showMessage(f"Saved {path}")

    def export(self):
        dlg = QDialog(self); dlg.setWindowTitle("Export controller")
        f = QFormLayout(dlg)
        kind = QComboBox(); kind.addItems(["Unity C# controller (+ JSON)", "JSON parameters", "Godot 4 GDScript"])
        scale = QDoubleSpinBox(); scale.setRange(0.001, 10000); scale.setValue(1.0); scale.setDecimals(3)
        scale.setToolTip("All fitted lengths are in character heights. Enter how tall YOUR character is in "
                         "your engine's units (e.g. 1.0 for a 1-unit Unity sprite, 0.32 for a 32 px sprite at "
                         "100 px/unit); speeds and gravities are scaled accordingly.")
        ppu = QDoubleSpinBox(); ppu.setRange(1, 1000); ppu.setValue(32)
        ppu.setToolTip("Godot works in pixels: pixels per engine unit.")
        f.addRow("Format", kind)
        f.addRow("Your character's height (engine units)", scale)
        f.addRow("Godot pixels per engine unit", ppu)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        p = scaled(self.params_panel.params, scale.value())
        meta = dict(self.fit_meta)
        meta.setdefault("model", self.project.model_key)
        meta["unit_note"] = (f"{1 / scale.value():.4g} × the reference character's height (the character is "
                             f"{scale.value():g} units tall)" if scale.value() != 1 else "the reference character's height")
        k = kind.currentIndex()
        try:
            if k == 0:
                d = QFileDialog.getExistingDirectory(self, "Folder for FittedPlatformerController.cs", str(ROOT))
                if not d:
                    return
                cs = export_unity(p, Path(d) / "FittedPlatformerController.cs", meta)
                js = export_json(p, Path(d) / "fitted_controller.json", meta)
                msg = f"Wrote\n{cs}\n{js}\n\nAdd the script to a GameObject with a BoxCollider2D. " \
                      f"Optionally drag the JSON into 'Fitted Params Json'."
            elif k == 1:
                path, _ = QFileDialog.getSaveFileName(self, "JSON", str(ROOT / "fitted_controller.json"), "JSON (*.json)")
                if not path:
                    return
                msg = f"Wrote {export_json(p, path, meta)}"
            else:
                d = QFileDialog.getExistingDirectory(self, "Folder for fitted_platformer_controller.gd", str(ROOT))
                if not d:
                    return
                msg = f"Wrote {export_godot(p, Path(d) / 'fitted_platformer_controller.gd', meta, ppu.value())}"
        except Exception as e:
            QMessageBox.warning(self, "Export failed", str(e))
            return
        QMessageBox.information(self, "Export", msg)

    def load_controller_json(self):
        from ..export.exporters import load_json
        path, _ = QFileDialog.getOpenFileName(self, "Controller JSON", str(ROOT), "JSON (*.json)")
        if not path:
            return
        try:
            p, meta = load_json(path)
        except Exception as e:
            QMessageBox.warning(self, "Load", str(e))
            return
        self.params_panel.set_params(p)
        self._params_edited(p)

    def help(self):
        QMessageBox.information(self, "Quick start", self._welcome().replace("<b>", "").replace("</b>", ""))

    def closeEvent(self, ev):
        if self.fit_thread is not None:
            self.fit_thread.cancel()
            self.fit_thread.wait(3000)
        super().closeEvent(ev)


def _dark(app: QApplication):
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(37, 40, 46))
    pal.setColor(QPalette.WindowText, QColor(225, 225, 225))
    pal.setColor(QPalette.Base, QColor(28, 30, 35))
    pal.setColor(QPalette.AlternateBase, QColor(45, 48, 55))
    pal.setColor(QPalette.Text, QColor(225, 225, 225))
    pal.setColor(QPalette.Button, QColor(50, 54, 62))
    pal.setColor(QPalette.ButtonText, QColor(230, 230, 230))
    pal.setColor(QPalette.Highlight, QColor(60, 120, 200))
    pal.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.ToolTipBase, QColor(50, 54, 62))
    pal.setColor(QPalette.ToolTipText, QColor(230, 230, 230))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(120, 120, 120))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(120, 120, 120))
    app.setPalette(pal)


def main(argv=None):
    argv = argv if argv is not None else sys.argv
    app = QApplication(argv)
    _dark(app)
    w = MainWindow()
    w.show()
    if len(argv) > 1 and argv[1].endswith(".rcf.json"):
        w.open_project(argv[1])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
