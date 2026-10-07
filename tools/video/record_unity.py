"""Record the Unity part of the project video (background window capture, scripted editor steps).

Usage: python tools/video/record_unity.py WORK_DIR STAGE_DIR [UNITY_VERSION]
STAGE_DIR must contain the exported FittedPlatformerController.cs and fitted_controller.json.
Produces WORK_DIR/unity_shots/<step>.png (editor window) and WORK_DIR/unity_frames/f*.png (gameplay).
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wincap  # noqa: E402

UNITY = Path(r"C:\Program Files\Unity\Hub\Editor")


def write_stage(stage: Path):
    ch = json.loads((HERE / "choreo.json").read_text())
    (stage / "level.txt").write_text("\n".join(f"{n}|{a}|{b}|{c}|{d}" for n, (a, b, c, d) in zip(ch["names"], ch["level"])))
    (stage / "events.txt").write_text("\n".join(f"{e['t']} {e['axis']} {int(e['jump'])}" for e in ch["events"]))
    rt = stage / "unity_runtime"
    rt.mkdir(exist_ok=True)
    for f in (HERE / "unity" / "Runtime").glob("*.cs"):
        shutil.copy(f, rt / f.name)
    return ch["duration"]


def kill_hub_for(proj: Path):
    """The editor launches a silent Unity Hub bound to this project (licensing); close only that one."""
    ps = (f"Get-CimInstance Win32_Process -Filter \"Name='Unity Hub.exe'\" | "
          f"? {{ $_.CommandLine -like '*{proj.name}*' }} | % {{ Stop-Process -Id $_.ProcessId -Force }}")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)


def main():
    work = Path(sys.argv[1]); stage = Path(sys.argv[2])
    version = sys.argv[3] if len(sys.argv) > 3 else "6000.3.8f1"
    exe = UNITY / version / "Editor" / "Unity.exe"
    work.mkdir(parents=True, exist_ok=True)
    duration = write_stage(stage)
    proj = work / "RCF_Unity_Demo"
    signal = work / "unity_signal"; frames = work / "unity_frames"; shots = work / "unity_shots"
    for d in (signal, frames, shots):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    kill_hub_for(proj)
    time.sleep(1)
    if proj.exists():
        shutil.rmtree('\\\\?\\' + str(proj.resolve()))  # Library paths exceed MAX_PATH
    print("creating project…", flush=True)
    subprocess.run([str(exe), "-batchmode", "-nographics", "-createProject", str(proj), "-quit",
                    "-logFile", str(work / "unity_create.log")], check=True, timeout=1200)
    (proj / "Assets" / "Editor").mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "unity" / "Editor" / "RcfVideoDirector.cs", proj / "Assets" / "Editor" / "RcfVideoDirector.cs")
    env = dict(os.environ, RCF_SIGNAL=str(signal), RCF_STAGE=str(stage), RCF_FRAMES=str(frames),
               RCF_DURATION=str(duration))
    print("launching editor…", flush=True)
    proc = subprocess.Popen([str(exe), "-projectPath", str(proj), "-executeMethod", "RcfVideoDirector.Begin",
                             "-logFile", str(work / "unity_editor.log")], env=env)
    hwnd = None
    t0 = time.time()
    done = set()
    while proc.poll() is None and time.time() - t0 < 1800:
        if hwnd is None:
            wins = wincap.find_windows("RCF_Unity_Demo", proc.pid) or wincap.find_windows("RCF_Unity_Demo")
            big = [w for w in wins if wincap.client_size(w[0])[0] > 800]
            if big:
                hwnd = big[0][0]
                print("editor window:", big[0][1], flush=True)
        if hwnd is not None:
            # keep the editor behind the user's windows, at 1920x1080
            wincap.park_behind(hwnd, 0, 0, 1920, 1080)
        for f in sorted(signal.glob("ready_*.txt")):
            if f.name in done:
                continue
            time.sleep(1.0)
            name = f.read_text().strip()
            img = wincap.grab(hwnd) if hwnd else None
            if img is not None:
                cv2.imwrite(str(shots / f"{f.stem.split('_')[1]}_{name}.png"), img)
            print("shot", f.stem, name, None if img is None else img.shape, flush=True)
            (signal / f.name.replace("ready_", "shot_")).write_text("ok")
            done.add(f.name)
        for f in sorted(signal.glob("playshot_*.txt")):
            if f.name in done:
                continue
            img = wincap.grab(hwnd) if hwnd else None
            if img is not None:
                cv2.imwrite(str(shots / f"9{f.stem.split('_')[1]}_playing.png"), img)
            print("play shot", f.stem, flush=True)
            done.add(f.name)
        hb = signal / "heartbeat.txt"
        if time.time() - getattr(main, "_last_hb", 0) > 10 and hb.exists():
            main._last_hb = time.time()
            try:
                print("heartbeat:", hb.read_text(), flush=True)
            except OSError:
                pass
        if (signal / "error.txt").exists():
            print("DIRECTOR ERROR:", (signal / "error.txt").read_text())
            break
        time.sleep(0.25)
    if proc.poll() is None:
        print("waiting for editor to exit…")
        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
    kill_hub_for(proj)
    print("finished:", (signal / "finished.txt").exists(), "frames:", len(list(frames.glob("f*.png"))))


if __name__ == "__main__":
    main()
