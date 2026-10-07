"""GUI smoke test (offscreen Qt): open an example project, play back, edit a parameter, try play mode."""
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "synthetic.rcf.json"


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.mark.skipif(not EXAMPLE.exists(), reason="examples/synthetic.rcf.json not generated")
def test_main_window_workflow(app):
    from rcf.gui.app import MainWindow
    from rcf.model import ControllerParams
    w = MainWindow()
    w.show()
    w.open_project(str(EXAMPLE))
    assert len(w.project.clips) >= 4 and w.cur == 0
    assert "Fit error" in w.params_panel.err.text()
    w.seek(1.0)
    w._tick()
    # live parameter edit changes the prototype and the error
    before = w.params_panel.err.text()
    p = w.params_panel.params.copy(jump_velocity=w.params_panel.params.jump_velocity * 1.5)
    w.params_panel.set_params(p)
    w._params_edited(p)
    w.update_metrics()
    assert w.params_panel.err.text() != before
    # play mode
    w.toggle_try()
    assert w.proto_view.playing
    for _ in range(30):
        w.proto_view._tick()
    w.toggle_try()
    assert not w.proto_view.playing
    # timeline edit -> manual scenario
    sc = w.timeline.scenario
    from copy import deepcopy
    sc2 = deepcopy(sc); sc2.inputs.jumps.append((0.05, 0.1))
    w._scenario_edited(sc2)
    assert w.project.clips[0].manual_scenario is not None
    w.close()
