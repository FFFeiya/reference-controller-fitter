"""Record the tool part of the project video, rendered offscreen (nothing is shown on screen).

Usage: python tools/video/record_tool.py OUT_DIR
Writes OUT_DIR/<segment>/fNNNNN.png at 30 fps plus OUT_DIR/segments.json with, per segment,
the frame count and regions of interest (widget rectangles) for highlights in the edit.
"""
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt, QEventLoop, QTimer, QRect, QPoint  # noqa: E402
from PySide6.QtGui import QImage, QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from rcf.gui.app import MainWindow, _dark  # noqa: E402
from rcf.gui.clip_dialog import ClipDialog  # noqa: E402
from rcf.model import ControllerParams  # noqa: E402

FPS = 30
W, H = 1920, 1080


def pump(ms):
    loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec()


class Seg:
    def __init__(self, out: Path, name: str, widget: QWidget):
        self.dir = out / name
        self.dir.mkdir(parents=True, exist_ok=True)
        for f in self.dir.glob("*.png"):
            f.unlink()
        self.w = widget
        self.n = 0
        self.rois = {}
        self.marks = {}

    def shot(self, count=1):
        img = self.w.grab().toImage().convertToFormat(QImage.Format_RGB888)
        for _ in range(count):
            img.save(str(self.dir / f"f{self.n:05d}.png"))
            self.n += 1

    def hold(self, seconds, step_ms=None):
        """Real-time capture: pump the event loop between frames."""
        for _ in range(int(seconds * FPS)):
            pump(step_ms if step_ms is not None else int(1000 / FPS))
            self.shot()

    def roi(self, name, widget):
        tl = widget.mapTo(self.w, QPoint(0, 0))
        self.rois[name] = [tl.x(), tl.y(), widget.width(), widget.height()]

    def mark(self, name):
        self.marks[name] = self.n

    def meta(self):
        return {"frames": self.n, "size": [self.w.width(), self.w.height()], "rois": self.rois, "marks": self.marks}


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    _dark(app)
    f = app.font(); f.setFamily("Segoe UI"); f.setPointSize(10); app.setFont(f)
    meta = {}
    video = str(ROOT / "references" / "real" / "supertux_l1" / "supertux_l1_seg01.mp4")

    # ---------------- 1) clip dialog: open video, range, box, track
    dlg = ClipDialog(video, None)
    dlg.resize(1600, 960)
    dlg.show()
    pump(300)
    s = Seg(out, "t1_clip", dlg)
    dlg.end.setValue(100)
    dlg.slider.setValue(0)
    pump(100)
    s.roi("canvas", dlg.canvas); s.roi("range", dlg.start); s.roi("track", dlg.track_btn)
    s.roi("status", dlg.status)
    s.hold(2.5)
    s.mark("draw")
    # animate drawing the box around Tux (frame px)
    x, y, w, h = 94, 426, 36, 38
    for i in range(1, 21):
        k = i / 20
        dlg.canvas.box = (x - 6, y - 6, (w + 12) * k, (h + 12) * k)
        dlg.canvas.update(); pump(10); s.shot()
    dlg.box = (x, y, w, h)
    dlg.canvas.box = dlg.box
    dlg.status.setText(f"Character box {dlg.box} at frame 0. Press Track.")
    s.hold(1.5)
    s.mark("track")
    dlg._track(False)
    while dlg.thread is not None:
        pump(150); s.shot()          # time-lapse of tracking progress (~0.15 s real per frame)
    s.hold(0.8)
    s.mark("tracked")
    # scrub through the tracked clip: box + orange feet path
    for k in range(0, 100):
        dlg.slider.setValue(k); pump(5); s.shot()
    s.hold(1.5)
    meta["t1_clip"] = s.meta()
    dlg.reject()

    # ---------------- 2) main window with several clips, playback, timeline
    w = MainWindow()
    w.resize(W, H)
    w.show()
    pump(300)
    w.open_project(str(ROOT / "examples" / "supertux.rcf.json"))
    pump(300)
    s = Seg(out, "t2_main", w)

    def main_rois(seg):
        # measured after the docks have settled (positions taken earlier were off)
        seg.roi("clips", w.clip_list); seg.roi("ref", w.ref_view); seg.roi("proto", w.proto_view)
        seg.roi("timeline", w.timeline); seg.roi("params", w.params_panel); seg.roi("report", w.params_panel.report)
        seg.roi("fit", w.fit_btn); seg.roi("try", w.try_btn); seg.roi("export", w.export_btn)
        seg.roi("error", w.params_panel.err)
    w.toggle_play()
    s.hold(4.0)
    s.mark("timeline")
    s.hold(4.0)
    w.toggle_play()
    main_rois(s)
    meta["t2_main"] = s.meta()

    # ---------------- 3) fit from a generic controller
    s = Seg(out, "t3_fit", w)
    w.params_panel.set_params(ControllerParams()); w._params_edited(ControllerParams()); w.update_metrics()
    w.toggle_play()
    s.hold(3.0)
    s.mark("fit")
    w.fit()
    t0 = time.time()
    while w.fit_thread is not None and time.time() - t0 < 900:
        pump(400); s.shot()          # time-lapse
    s.mark("done")
    s.hold(6.0)
    w.toggle_play()
    main_rois(s)
    meta["t3_fit"] = s.meta()

    # ---------------- 4) try it with the keyboard (scripted key events)
    s = Seg(out, "t4_try", w)
    w.toggle_try()
    pv = w.proto_view
    keylog = []

    def key(k, down):
        app.sendEvent(pv, QKeyEvent(QKeyEvent.KeyPress if down else QKeyEvent.KeyRelease, k, Qt.NoModifier))
    held = set()
    script = [(Qt.Key_D, True, 0.9), (Qt.Key_Space, True, 0.45), (Qt.Key_Space, False, 1.0),
              (Qt.Key_Space, True, 0.1), (Qt.Key_Space, False, 1.0), (Qt.Key_D, False, 0.6),
              (Qt.Key_A, True, 0.7), (Qt.Key_Space, True, 0.5), (Qt.Key_Space, False, 1.0), (Qt.Key_A, False, 0.8)]
    for k, down, dur in script:
        key(k, down)
        (held.add if down else held.discard)(k)
        for _ in range(int(dur * FPS)):
            pump(int(1000 / FPS)); s.shot()
            keylog.append([int(Qt.Key_A in held), int(Qt.Key_D in held), int(Qt.Key_Space in held)])
    main_rois(s)
    w.toggle_try()
    meta["t4_try"] = s.meta()
    meta["t4_try"]["keys"] = keylog

    # ---------------- 5) export dialog (built like File > Export, grabbed without exec)
    from PySide6.QtWidgets import QDialog, QFormLayout, QComboBox, QDoubleSpinBox, QDialogButtonBox
    d = QDialog(w); d.setWindowTitle("Export controller")
    fl = QFormLayout(d)
    kind = QComboBox(); kind.addItems(["Unity C# controller (+ JSON)", "JSON parameters", "Godot 4 GDScript"])
    sc = QDoubleSpinBox(); sc.setRange(0.001, 10000); sc.setValue(1.0); sc.setDecimals(3)
    ppu = QDoubleSpinBox(); ppu.setRange(1, 1000); ppu.setValue(48)
    fl.addRow("Format", kind); fl.addRow("Your character's height (engine units)", sc); fl.addRow("Godot pixels per engine unit", ppu)
    fl.addRow(QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel))
    d.resize(640, 200)
    d.show(); pump(200)
    s = Seg(out, "t5_export", d)
    s.hold(3.0)
    kind.setCurrentIndex(2); pump(50)
    s.hold(1.5)
    meta["t5_export"] = s.meta()
    d.close()
    w.close()
    (out / "segments.json").write_text(json.dumps(meta, indent=1))
    print({k: v["frames"] for k, v in meta.items()})


if __name__ == "__main__":
    main()
