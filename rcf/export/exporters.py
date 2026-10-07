"""Exporters: fitted parameters -> JSON, Unity C# controller, Godot 4 GDScript controller."""
from __future__ import annotations

import datetime as _dt
import json
import math
from pathlib import Path

from .. import __version__
from ..model import ControllerParams

TEMPLATES = Path(__file__).parent / "templates"

LENGTH_PARAMS = ["max_speed", "ground_accel", "ground_decel", "turn_accel", "jump_velocity", "rise_gravity",
                 "fall_gravity", "apex_threshold", "max_fall_speed"]


def scaled(p: ControllerParams, length_scale: float) -> ControllerParams:
    """Express the parameters in different length units (e.g. fit units -> Unity units)."""
    d = {}
    for n in ControllerParams.names():
        v = getattr(p, n)
        d[n] = v * length_scale if n in LENGTH_PARAMS and math.isfinite(v) else v
    return ControllerParams(**d)


def _num(v: float) -> str:
    if not math.isfinite(v):
        return "0"
    s = f"{v:.6g}"
    if "e" in s or "." in s:
        return s if "e" not in s else f"{v:.6f}"
    return s + ".0"


def export_json(p: ControllerParams, path: str | Path, meta: dict | None = None) -> Path:
    path = Path(path)
    d = p.to_dict()
    # Unity/Godot friendly: 0 = unlimited terminal velocity
    d["max_fall_speed"] = 0.0 if d["max_fall_speed"] is None else d["max_fall_speed"]
    out = {"format": "rcf-controller", "version": 1, "generator": f"Reference Controller Fitter {__version__}",
           "units": {"length": "world unit", "time": "second", "y_axis": "up"},
           "parameters": d, "meta": meta or {}}
    path.write_text(json.dumps(out, indent=2))
    return path


def load_json(path: str | Path) -> tuple[ControllerParams, dict]:
    d = json.loads(Path(path).read_text())
    if d.get("format") != "rcf-controller":
        raise ValueError("Not an exported controller JSON.")
    pp = dict(d["parameters"])
    if not pp.get("max_fall_speed"):
        pp["max_fall_speed"] = None
    return ControllerParams.from_dict(pp), d.get("meta", {})


def _fill(template: str, p: ControllerParams, meta: dict, extra: dict | None = None) -> str:
    rep = dict(extra or {})
    rep.update({"{{VERSION}}": __version__, "{{DATE}}": _dt.date.today().isoformat(),
           "{{MODEL}}": str(meta.get("model", "manual")),
           "{{FIT_ERROR}}": (f"{100 * meta['fit_error']:.1f}%" if meta.get("fit_error") is not None else "n/a"),
           "{{CONFIDENCE}}": str(meta.get("confidence", "n/a")),
           "{{UNIT_NOTE}}": str(meta.get("unit_note", "the reference character's height"))})
    for n in ControllerParams.names():
        v = getattr(p, n)
        if n == "max_fall_speed" and not math.isfinite(v):
            v = 0.0
        rep["{{" + n + "}}"] = _num(v)
    out = template
    for k, v in rep.items():
        out = out.replace(k, v)
    if "{{" in out:
        raise ValueError("Unfilled template placeholder: " + out[out.index("{{"):out.index("{{") + 40])
    return out


def export_unity(p: ControllerParams, path: str | Path, meta: dict | None = None) -> Path:
    path = Path(path)
    if path.suffix != ".cs":
        path = path / "FittedPlatformerController.cs"
    path.parent.mkdir(parents=True, exist_ok=True)
    tpl = (TEMPLATES / "FittedPlatformerController.cs.txt").read_text(encoding="utf-8")
    path.write_text(_fill(tpl, p, meta or {}), encoding="utf-8")
    return path


def export_godot(p: ControllerParams, path: str | Path, meta: dict | None = None,
                 pixels_per_unit: float = 32.0) -> Path:
    path = Path(path)
    if path.suffix != ".gd":
        path = path / "fitted_platformer_controller.gd"
    path.parent.mkdir(parents=True, exist_ok=True)
    tpl = (TEMPLATES / "fitted_platformer_controller.gd.txt").read_text(encoding="utf-8")
    text = _fill(tpl, p, meta or {}, {"{{PPU}}": _num(pixels_per_unit)})
    path.write_text(text, encoding="utf-8")
    return path
