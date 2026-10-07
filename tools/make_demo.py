"""Record a demo video of the real GUI (offscreen): fit from a generic controller with the error
dropping while reference and prototype play side by side, then keyboard play mode, then export.

Usage: python tools/make_demo.py [project.rcf.json] [out.mp4]
"""
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt, QEventLoop, QTimer  # noqa: E402
from PySide6.QtGui import QKeyEvent, QImage, QPainter, QColor, QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rcf.gui.app import MainWindow, _dark  # noqa: E402
from rcf.model import ControllerParams  # noqa: E402

W, H = 1600, 960
FPS = 20


class Recorder:
    def __init__(self, w, out):
        self.w = w
        self.proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgra",
                                      "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "20",
                                      "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
        self.caption = ""

    def frame(self):
        img = self.w.grab().toImage().convertToFormat(QImage.Format_ARGB32).scaled(W, H)
        if self.caption:
            p = QPainter(img)
            p.fillRect(0, H - 46, W, 46, QColor(0, 0, 0, 200))
            p.setPen(QColor(255, 255, 255)); p.setFont(QFont("Segoe UI", 16, QFont.Bold))
            p.drawText(16, H - 14, self.caption)
            p.end()
        self.proc.stdin.write(bytes(img.constBits()))

    def close(self):
        self.proc.stdin.close(); self.proc.wait()


def pump(ms):
    loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec()


def main():
    proj = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "examples" / "supertux.rcf.json")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else ROOT / "docs" / "demo.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    _dark(app)
    w = MainWindow(); w.resize(W, H); w.show()
    pump(200)
    w.open_project(proj)
    rec = Recorder(w, out)

    def hold(seconds, step_ms=None):
        n = int(seconds * FPS)
        for _ in range(n):
            pump(step_ms or int(1000 / FPS)); rec.frame()

    # 1) generic controller vs reference
    w.params_panel.set_params(ControllerParams()); w._params_edited(ControllerParams())
    w.update_metrics()
    w.toggle_play()
    rec.caption = "Reference footage (left) vs. a generic controller (right) – before fitting"
    hold(3)
    # 2) fit: all clips, one shared controller, model search A–E
    rec.caption = "Fit Controller: one controller for all clips, candidate models A–E"
    w.fit()
    t0 = time.time()
    while w.fit_thread is not None and time.time() - t0 < 600:
        pump(300); rec.frame()        # time-lapse: 0.3 s of fitting per video frame
    rec.caption = f"Fitted: {w.statusBar().currentMessage()}"
    hold(4)
    # 3) other clips
    for i in (1, 3):
        if i < len(w.project.clips):
            w.clip_list.setCurrentRow(i)
            rec.caption = f"Same controller, clip {i + 1}: {w.project.clips[i].name}"
            hold(3)
    if w.timer.isActive():
        w.toggle_play()
    # 4) play it
    w.toggle_try()
    pv = w.proto_view
    rec.caption = "Try it: A/D to run, Space to jump (hold = long jump, tap = short jump)"

    def key(k, press=True):
        app.sendEvent(pv, QKeyEvent(QKeyEvent.KeyPress if press else QKeyEvent.KeyRelease, k, Qt.NoModifier))
    script = [(Qt.Key_D, True, 0.8), (Qt.Key_Space, True, 0.45), (Qt.Key_Space, False, 0.9),
              (Qt.Key_Space, True, 0.08), (Qt.Key_Space, False, 1.0), (Qt.Key_D, False, 0.5),
              (Qt.Key_A, True, 0.6), (Qt.Key_Space, True, 0.5), (Qt.Key_Space, False, 1.0), (Qt.Key_A, False, 0.8)]
    for k, down, dur in script:
        key(k, down)
        hold(dur, step_ms=int(1000 / FPS))
    w.toggle_try()
    # 5) export
    from rcf.export.exporters import export_unity, export_json
    d = ROOT / "exports" / "demo"
    cs = export_unity(w.params_panel.params, d / "FittedPlatformerController.cs", w.fit_meta)
    export_json(w.params_panel.params, d / "fitted_controller.json", w.fit_meta)
    rec.caption = f"Export ▸ Unity C# controller written: {cs.relative_to(ROOT)}"
    hold(3)
    rec.close()
    w.close()
    print("wrote", out)


if __name__ == "__main__":
    main()
