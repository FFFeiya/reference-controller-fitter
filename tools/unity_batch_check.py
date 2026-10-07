"""Verify an exported Unity controller inside real Unity Editors (batch mode).

For each installed Unity version and input backend (legacy Input Manager / Input System
package) this creates a throw-away project, compiles the controller, runs a scripted
run + jump through Physics2D.Simulate and compares the resulting trajectory with the
Python reference model (rcf.sim.simulate).

Usage: python tools/unity_batch_check.py [controller.json] [--versions 6000.3.8f1,2022.3.62f3]
Requires a Unity licence that allows batch mode (Personal works).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rcf.export.compile_check import find_unity_editors  # noqa: E402
from rcf.export.exporters import export_unity, load_json  # noqa: E402
from rcf.model import ControllerParams, State  # noqa: E402
from rcf.sim import Scenario, InputTrack, simulate  # noqa: E402

INPUT_SYSTEM_VERSION = {"6000": "1.18.0", "2022": "1.14.2"}


def python_reference(p: ControllerParams, dt=0.02, steps=150):
    sc = Scenario(0.0, steps * dt, State(), InputTrack(0, [(10 * dt, 1), (70 * dt, 0)],
                                                       [(25 * dt, 45 * dt), (100 * dt, 104 * dt)]),
                  floors=[(0.0, 0.0)])
    t = (np.arange(steps) + 1) * dt
    return t, simulate(p, sc, t)


def run_unity(editor: Path, cs: Path, backend: str, work: Path, log=print) -> dict:
    exe = editor / "Editor" / ("Unity.exe" if os.name == "nt" else "Unity")
    proj = work / f"proj_{editor.name}_{backend}"
    if proj.exists():
        shutil.rmtree(proj)
    r = subprocess.run([str(exe), "-batchmode", "-nographics", "-createProject", str(proj), "-quit",
                        "-logFile", str(work / f"create_{editor.name}_{backend}.log")], timeout=1200)
    if r.returncode != 0:
        return {"ok": False, "stage": "create", "log": str(work / f"create_{editor.name}_{backend}.log")}
    # input backend
    ps = proj / "ProjectSettings" / "ProjectSettings.asset"
    txt = ps.read_text(encoding="utf-8")
    handler = {"legacy": 0, "inputsystem": 1}[backend]
    if "activeInputHandler" in txt:
        txt = re.sub(r"activeInputHandler: \d", f"activeInputHandler: {handler}", txt)
    ps.write_text(txt, encoding="utf-8")
    if backend == "inputsystem":
        man = proj / "Packages" / "manifest.json"
        m = json.loads(man.read_text())
        major = editor.name.split(".")[0]
        m["dependencies"]["com.unity.inputsystem"] = INPUT_SYSTEM_VERSION.get(major, "1.14.2")
        man.write_text(json.dumps(m, indent=2))
    (proj / "Assets" / "Scripts").mkdir(parents=True, exist_ok=True)
    (proj / "Assets" / "Editor").mkdir(parents=True, exist_ok=True)
    shutil.copy(cs, proj / "Assets" / "Scripts" / cs.name)
    shutil.copy(ROOT / "tools" / "unity" / "RcfBatchTest.cs", proj / "Assets" / "Editor" / "RcfBatchTest.cs")
    out = work / f"traj_{editor.name}_{backend}.csv"
    if out.exists():
        out.unlink()
    logf = work / f"run_{editor.name}_{backend}.log"
    env = dict(os.environ, RCF_OUT=str(out))
    r = subprocess.run([str(exe), "-batchmode", "-nographics", "-projectPath", str(proj),
                        "-executeMethod", "RcfBatchTest.Run", "-quit", "-logFile", str(logf)], env=env, timeout=1800)
    text = logf.read_text(encoding="utf-8", errors="replace") if logf.exists() else ""
    ours = [l.strip() for l in text.splitlines() if "FittedPlatformerController.cs" in l and ("error" in l or "warning" in l)]
    errors = sorted(set(l for l in ours if "error" in l))
    warnings = sorted(set(l for l in ours if "warning" in l))
    res = {"ok": out.exists() and r.returncode == 0 and not errors, "returncode": r.returncode,
           "errors": errors, "warnings": warnings, "log": str(logf), "traj": str(out)}
    if out.exists():
        res["data"] = np.loadtxt(out, delimiter=",")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json", nargs="?", default=None)
    ap.add_argument("--versions", default=None)
    ap.add_argument("--backends", default="legacy,inputsystem")
    a = ap.parse_args()
    if a.json:
        p, meta = load_json(a.json)
    else:
        p = ControllerParams(max_speed=8, ground_accel=40, ground_decel=55, turn_accel=55, jump_velocity=12,
                             rise_gravity=38, fall_gravity=52, jump_cut=0.45, air_control=0.55)
        meta = {"model": "benchmark"}
    editors = find_unity_editors()
    if a.versions:
        want = a.versions.split(",")
        editors = [e for e in editors if e.name in want]
    if not editors:
        print("No Unity editor found.")
        return 1
    work = Path(tempfile.gettempdir()) / "rcf_unity_check"
    work.mkdir(exist_ok=True)
    cs = export_unity(p, work / "FittedPlatformerController.cs", meta)
    t, ref = python_reference(p)
    all_ok = True
    for ed in editors:
        for backend in a.backends.split(","):
            print(f"== Unity {ed.name} / {backend}", flush=True)
            res = run_unity(ed, cs, backend, work)
            for e in res.get("errors", []):
                print("   ", e)
            for w in res.get("warnings", []):
                print("   ", w)
            if "data" not in res:
                print("   FAILED", {k: v for k, v in res.items() if k != "data"})
                all_ok = False
                continue
            d = res["data"]
            ux = d[:, 1] - d[0, 1] + ref["x"][0]
            uy = d[:, 2]
            ex = np.max(np.abs(ux - ref["x"]))
            ey = np.max(np.abs(uy - ref["y"]))
            apex_u, apex_p = uy.max(), ref["y"].max()
            ok = res["ok"] and ex < 0.02 and ey < 0.02
            all_ok &= ok
            print(f"   compile errors: {len(res['errors'])}, warnings: {len(res['warnings'])}; "
                  f"max |dx| {ex:.5f}, max |dy| {ey:.5f} units; apex Unity {apex_u:.4f} vs model {apex_p:.4f} "
                  f"-> {'PASS' if ok else 'FAIL'}", flush=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
