"""Serialization round trips and exporters (Unity C#, Godot, JSON)."""
import json
import math
import re
from pathlib import Path

import numpy as np
import pytest

from rcf.model import ControllerParams, State
from rcf.sim import Scenario, InputTrack
from rcf.project import Project, Clip
from rcf.tracking.camera import CameraTrack
from rcf.tracking.pipeline import TrackResult, splice
from rcf.export.exporters import export_json, load_json, export_unity, export_godot, scaled
from rcf.export import compile_check

P = ControllerParams(max_speed=7.5, ground_accel=33, ground_decel=41, turn_accel=41, jump_velocity=15.3,
                     rise_gravity=26.4, fall_gravity=23.3, jump_cut=0.4, release_gravity_mult=1.0,
                     air_control=0.6)


def fake_track(n=10, start=0):
    frames = np.arange(start, start + n)
    bbox = np.tile([10.0, 20.0, 8.0, 16.0], (n, 1))
    cam = CameraTrack(np.arange(n, dtype=float), np.zeros(n), np.ones(n), ["lk-mode"] * n)
    return TrackResult("video.mp4", 30.0, start, frames, bbox, np.ones(n), np.zeros(n, bool), cam, "template")


def test_scenario_and_track_roundtrip():
    sc = Scenario(0.0, 2.0, State(1, 2, 3, 4, False, True), InputTrack(1, [(0.5, -1)], [(0.1, 0.3)]),
                  [(0.0, 0.0)], [1.5], 0.0)
    assert Scenario.from_dict(json.loads(json.dumps(sc.to_dict()))).to_dict() == sc.to_dict()
    tr = fake_track()
    tr2 = TrackResult.from_dict(json.loads(json.dumps(tr.to_dict())))
    np.testing.assert_allclose(tr2.bbox, tr.bbox)
    np.testing.assert_allclose(tr2.camera.cam_x, tr.camera.cam_x)


def test_project_save_load(tmp_path: Path):
    proj = Project()
    c = Clip("c1", str(tmp_path / "video.mp4"), 0, 10, (10, 20, 8, 16))
    c.track = fake_track()
    c.manual_scenario = Scenario(0.0, 0.3, State(), InputTrack(0, [(0.1, 1)], []))
    proj.clips.append(c)
    proj.params = P
    proj.model_key = "D2"
    f = tmp_path / "p.rcf.json"
    proj.save(f)
    d = json.loads(f.read_text())
    assert d["clips"][0]["video"] == "video.mp4"        # stored relative to the project
    q = Project.load(f)
    assert q.params == P and q.model_key == "D2"
    assert q.clips[0].manual_scenario.inputs.axis_events == [(0.1, 1)]
    assert len(q.clips[0].reference) == 10


def test_track_slice_and_splice():
    tr = fake_track(20)
    s = tr.slice(5, 12)
    assert list(s.frames) == list(range(5, 12)) and s.start_frame == 5
    new = fake_track(10, start=10)        # re-tracked from frame 10, camera restarts at 0
    sp = splice(tr, new)
    assert list(sp.frames) == list(range(0, 20))
    # camera continuity at the splice point
    assert sp.camera.cam_x[10] == pytest.approx(tr.camera.cam_x[10])


def test_json_export_roundtrip(tmp_path):
    f = export_json(P, tmp_path / "c.json", {"model": "D"})
    p2, meta = load_json(f)
    assert p2 == P and meta["model"] == "D"
    assert json.loads(f.read_text())["parameters"]["max_fall_speed"] == 0.0   # 0 = unlimited for engines


def test_scaled_units():
    q = scaled(P, 2.0)
    assert q.max_speed == pytest.approx(15.0) and q.rise_gravity == pytest.approx(52.8)
    assert q.jump_cut == P.jump_cut and q.air_control == P.air_control and math.isinf(q.max_fall_speed)


def test_unity_export_is_complete(tmp_path):
    f = export_unity(P, tmp_path / "FittedPlatformerController.cs", {"model": "D", "fit_error": 0.02, "confidence": "HIGH"})
    src = f.read_text(encoding="utf-8")
    assert "{{" not in src
    assert "public class FittedPlatformerController : MonoBehaviour" in src
    assert "public float maxSpeed = 7.5f;" in src and "public float jumpCut = 0.4f;" in src
    assert "Time.fixedDeltaTime" in src and "JsonUtility.FromJson" in src
    assert "#if ENABLE_INPUT_SYSTEM" in src
    # balanced braces / parentheses as a cheap syntax sanity check
    code = "\n".join(l.split("//")[0] for l in src.splitlines())
    assert code.count("{") == code.count("}") and code.count("(") == code.count(")")


def test_godot_export_is_complete(tmp_path):
    f = export_godot(P, tmp_path / "c.gd", {"model": "D"}, pixels_per_unit=40)
    src = f.read_text(encoding="utf-8")
    assert "{{" not in src and "extends CharacterBody2D" in src
    assert "var pixels_per_unit: float = 40.0" in src
    assert "move_and_slide()" in src


@pytest.mark.skipif(not (compile_check.dotnet_available() and compile_check.find_unity_editors()),
                    reason="needs dotnet SDK and an installed Unity editor")
def test_unity_export_compiles_against_unity_assemblies(tmp_path):
    f = export_unity(P, tmp_path / "FittedPlatformerController.cs", {})
    results = compile_check.check_all(f)
    assert results
    for r in results:
        assert r.ok, (r.unity, r.errors, r.output[-2000:])
        assert not r.warnings, (r.unity, r.warnings)
