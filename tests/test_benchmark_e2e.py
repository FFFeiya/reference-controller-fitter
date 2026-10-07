"""End-to-end synthetic benchmark: rendered video -> tracking -> camera compensation -> multi-clip
fit -> model selection, compared against the ground-truth controller. (~2 min)"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.slow
def test_synthetic_benchmark_recovers_controller(tmp_path):
    from rcf.benchmark import run
    rep = run(ROOT / "benchmark" / "synthetic", log=lambda *a: None)
    assert rep["chosen"] == "D"
    # behaviour: fitted vs ground-truth controller under the true inputs (the acceptance metric)
    for name, err in rep["behaviour"].items():
        assert err <= 0.05, (name, err)
    for name, err in rep["generalization"].items():
        assert err <= 0.05, (name, err)
    # the fit explains every tracked reference clip
    for name, m in rep["clips"].items():
        assert m["trajectory"] <= 0.05, (name, m)
    for n in ("max_speed", "jump_velocity", "rise_gravity", "fall_gravity"):
        assert rep["params"][n]["rel_error"] < 0.1, (n, rep["params"][n])
