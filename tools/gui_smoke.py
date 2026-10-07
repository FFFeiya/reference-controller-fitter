"""Drive the real GUI headlessly (offscreen) and save screenshots of each stage.

Usage: python tools/gui_smoke.py OUT_DIR [project.rcf.json] [--fit]
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt, QEventLoop, QTimer  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rcf.gui.app import MainWindow, _dark  # noqa: E402


def pump(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    proj = next((a for a in sys.argv[2:] if a.endswith(".rcf.json")), str(ROOT / "examples" / "supertux.rcf.json"))
    do_fit = "--fit" in sys.argv
    app = QApplication([])
    _dark(app)
    w = MainWindow()
    w.resize(1600, 980)
    w.show()
    pump(200)
    w.open_project(proj)
    pump(300)
    w.seek(1.0 if w.cur >= 0 else 0.0)
    pump(200)
    w.grab().save(str(out / "01_opened.png"))
    print("opened", proj, "clips", len(w.project.clips), "error label:", w.params_panel.err.text())
    if do_fit:
        # start from a generic controller so the fit has something to do
        from rcf.model import ControllerParams
        w.params_panel.set_params(ControllerParams())
        w._params_edited(ControllerParams())
        pump(300)
        w.grab().save(str(out / "02_before_fit.png"))
        print("before fit:", w.params_panel.err.text())
        w.fit()
        t0 = time.time()
        shots = 0
        while w.fit_thread is not None and time.time() - t0 < 900:
            pump(500)
            if shots < 3 and time.time() - t0 > 2 + shots * 4:
                w.grab().save(str(out / f"03_fitting_{shots}.png")); shots += 1
                print("fitting:", w.params_panel.err.text())
        pump(300)
        w.grab().save(str(out / "04_after_fit.png"))
        print("after fit:", w.params_panel.err.text(), "| status:", w.statusBar().currentMessage())
    # play mode: hold D, jump
    w.toggle_try()
    pv = w.proto_view
    pump(100)
    def key(k, press=True):
        ev = QKeyEvent(QKeyEvent.KeyPress if press else QKeyEvent.KeyRelease, k, Qt.NoModifier)
        app.sendEvent(pv, ev)
    key(Qt.Key_D); pump(800)
    key(Qt.Key_Space); pump(250)
    w.grab().save(str(out / "05_play_jump.png"))
    print("play:", pv.info)
    key(Qt.Key_Space, False); key(Qt.Key_D, False); pump(800)
    print("play after release:", pv.info)
    w.toggle_try()
    w.toggle_play(); pump(700); w.toggle_play()
    w.grab().save(str(out / "06_playback.png"))
    w.close()
    print("ok")


if __name__ == "__main__":
    main()
