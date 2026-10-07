# Development log

Problem → experiment → result → decision, in order. Failures are included.

## Day 1 (2026-10-06)

### Setup
* Python 3.12, ffmpeg 9, .NET 9 SDK. Unity 2022.3.51, 2022.3.62 and 6000.3.8 were found locally, which made real compile checks possible.
* `opencv-python-headless` 5.0 has no CSRT/KCF, so the project venv uses `opencv-contrib-python-headless`.
* Two research tracks: literature (→ `research/RESEARCH_NOTES.md`) and licensed footage. The footage search found 6 Wikimedia Commons videos from 5 games, all GPL or CC BY-SA. No YouTube ripping.

### Integrator first
* **Decision:** an *exact* piecewise-constant-acceleration integrator. It splits steps at target-speed reached, vy sign change, apex band, terminal velocity and landing.
* **Why:**
  * the fitter samples at video frame times, the prototype at 60 Hz, and Unity at `fixedDeltaTime`; all must agree;
  * the objective is continuous in parameters and event times, which suits gradient-based least squares.
* **Tests:**
  * analytic apex height and landing time at dt = 1/30 … 0.25 s;
  * identical samples from coarse vs dense stepping.
* One test failed on first run: a vy that started exactly at the apex threshold was handled as outside the band. The band was made inclusive (`<=`).

### Synthetic benchmark
* Rendered 7 clips of the ground-truth controller from the brief: camera follow with dead zone, parallax clouds/hills, static HUD with changing score, squash-and-stretch sprite, noise, H.264.
* The truth JSON is read only by the evaluation code. The fitter gets the video plus a hand-drawn-style box (truth box loosened by 2–4 px) and the pixel scale.

### Trackers (measured on the benchmark)
* Template 0.41 px mean feet error, flow 0.54, CSRT 1.07, KCF 2.06, MIL 1.99 (one 24 px blow-up), hybrid (colour segmentation for extents) 1.00.
* **Failure:** the colour model initially returned nothing. A 30×32×16 HSV histogram was too fine for a noisy sprite (only 4 pixels matched). Coarser bins fixed it, but hybrid still lost because the sprite outline is not in the colour model.
* **Failure:** the fixed-size template box put the feet up to 3.5 px off during squash and stretch. The fit compensated with gravity bias (rise +7 %, fall −10 %) and the selection picked model E to absorb it. **Fix:** vertical-scale search (0.8–1.25). Feet error 0.41 → 0.15 px, gravity bias gone, model D selected.

### Camera
* LK features + weighted mean-shift mode: max error 1.03 px, mostly accumulated drift.
* Tried keyframe-relative estimation. **Failure:** errors of 60–150 px. The synthetic camera *pans 30 px/frame* at clip start and LK breaks at that range. **Fix:** use a keyframe only while the expected offset is under 16 px, and cross-check it against frame-to-frame. Max error 0.21 px.

### Input inference
* Threshold-based input classification worked on synthetic data. On SuperTux it produced **70 axis toggles** in 9 s: the speed ripples (frame drops, animation) and braking was detected everywhere.
* **Fix:** a 3-state Viterbi decoder with a switching penalty. SuperTux becomes "hold right" for the whole run, and the synthetic events stay within 1–3 frames.
* **Failure:** take-off time was estimated 25 ms early, because the smoothed velocity flags the take-off too soon. **Fix:** walk forward to the last frame still at ground level, then extrapolate a parabola.

### Multi-clip fitting
* Shared parameters + per-clip latent input timings, `least_squares` with a sparse Jacobian.
* First run on noisy *trajectories* (no video) recovered every GT parameter within 2 % (model D, 0.4 % error) in 3 s.
* First run end-to-end *from video* chose E. Two causes:
  * tracking bias (fixed above);
  * BIC_eff rewarding a 0.05 % gain.

  Added a practical-significance rule. Now D is chosen, all parameters are within 5 %, and behaviour error is ≤ 0.6 %, including unseen input sequences.

### Auto-detection (two failed attempts)
* Three-frame differencing: picked HUD digits and cloud edges, and found only the edges of the flat sprite.
* Median background mosaic: clouds (parallax) and the "ghost" of the standing player in the median.
* **Decision:** keep it as an assist that always asks for verification. Box drawing is primary, as the brief allows. Documented in KNOWN_LIMITATIONS.

### Real footage: SuperTux
* **Occlusion:** coin-collect sparkles hid Tux for 3 frames and the tracker latched onto a pillar edge with a plausible score of 0.65. **Fix:**
  * motion-consistency gating (an unexplained jump combined with a mediocre score means lost);
  * coasting on the prediction;
  * re-detection accepted only near the prediction.
* A single bad camera step (−12 px) left a permanent offset. Fixed with a 3-tap median on the steps.
* **Bunny-hops:** 1–2-frame ground contacts were merged into one long airborne phase. Fixed with V-contact detection.
* **Failure, found in the GUI timeline:** the standing→running animation moved the feet estimate by 0.13 units for 2 frames, which was detected as a tiny jump, so the prototype hopped at clip start. **Fix:** short low blips between equal ground levels are animation, not jumps.
* **Failure, found in the demo recording:** standing on a bump for 0.6 s was classified as a jump apex (lower on both sides). **Fix:** an apex is never flat for more than 0.2 s. Clip error 3.5 → 1.4 %.
* Clips with head-bumps into blocks (seg01 after 3.4 s) cannot be explained without level geometry, so the clip ranges avoid them.
* Result: 5 clips from 2 recordings, model D, 1.4–4.5 %.

### Real footage: Secret Maryo Chronicles (15 fps)
* **Failure:** 89 % of frames lost. Three causes:
  * the per-frame motion (about 50 px) exceeded the search window;
  * the standing template didn't match run or jump frames;
  * after one global re-detection, velocity extrapolation from the stale box sent the prediction off-screen.
* Fixes, in order:
  1. fps-scaled search: 89 → 56 %;
  2. relative appearance threshold capped at 0.6 (a standing sprite scores 1.0, so the first animation frames looked "lost");
  3. appearance bank: worse at first. SuperTux went to 55 % lost because the take-off velocity change tripped the motion gate. Fixed by only gating far jumps whose score is also mediocre;
  4. colour-histogram fallback: 46 → 5.9 % lost.
* **Camera:** the "hills with eyes" are a slow parallax layer and dominated the features.
  * Verified by brute force on the ground strip: the true shift was 293 px over 10 frames, but the estimate was 120 px.
  * **Fix:** cross-check against the band below the player's feet (gameplay layer). The estimate now matches the ground-ROI estimate.
* **Low fps:** the minimum 5-sample smoothing window (0.33 s at 15 fps) smeared walk-off and contact times.
  * Lowered to 3 samples below 24 fps.
  * **Failure:** applying the smaller window at 30 fps too broke SuperTux (spurious walk-offs), so the change is restricted to < 24 fps.
* Result: model B, 2.5 / 3.3 %. "Constant vs asymmetric gravity cannot be distinguished" is reported, which is correct for 2 clips.

### Optimizer robustness
* Model D on SuperTux came out at 1.9 %, 5.7 % or 2.5 % depending on small data changes: local minima around jump-release timing.
* Added:
  * a start that reproduces the parent model exactly (so D can never be worse than C);
  * release-time initializations ranked by jump height across all clips;
  * a polish pass (restart every model from the best one).
* An unidentifiable `air_control` (0.21 ± 185 %) would have made the exported controller feel wrong. Modifiers with over 100 % uncertainty are now fixed to neutral and the model refitted, and the report says so.

### Exports
* **Unity C#:** Kinematic Rigidbody2D + `Rigidbody2D.Cast`, with the integrator ported exactly.
  * `dotnet build` against 2022.3.51, 2022.3.62 and 6000.3.8: 0 errors. CS0649 warnings from the JsonUtility DTO were silenced with a pragma.
  * Unity batch mode (2022.3.62 and 6000.3.8 × legacy input and Input System): the scripted run + long jump + short jump matches the Python model (dx = 0, dy ≤ 0.005 units, identical apex).
* **Godot 4:** downloaded the official 4.7.2 build (SHA-512 verified) and ran headless. No script errors, apex within 0.002 units, landing 1 frame later (expected with `move_and_slide`). The first check "failed" only because Godot rests 0.08 px above floors (`safe_margin`); the measurement tolerance was fixed.

### GUI
* The offscreen Qt screenshots showed tofu boxes because the offscreen platform has no fonts. Fixed by setting `QT_QPA_FONTDIR` for screenshots and the demo.
* The timeline view exposed two inference bugs (the spurious hop and the bump plateau). Both fixed, see above.
* Demo: `tools/make_demo.py` records the real window. A generic controller at 40.6 % error drops to about 2.5 % live, then keyboard play, then Unity export.

### Independent QA review → fixes
An independent review pass did a fresh-venv install (43 tests passed) and drove the GUI as a first-time user. It found:
* **P0, duplicate clip names:** several clips cut from one video got the same name, and the fitted input timings were keyed by name, so clips swapped inputs. The panel showed 12.9 % while the fit said 2.3 %. **Fix:** a stable per-clip `uid`; names are made unique on add.
* **P0, project switch during a fit:** opening another project mid-fit applied the old fit to the new project ("Fit error 101.8 %"). **Fix:** cancel and wait, then ignore signals from stale threads with a `sender()` check. The first attempt used lambdas with a generation counter. That would have run GUI code on the worker thread (lambda slots have no receiver context, so they get a direct connection), so it was replaced before running.
* **P0, Cancel replaced a good fit with a partial one. Fix:** a separate `cancelled` signal; the previous parameters, report and confidence are restored.
* **P0, units:** a character height of 32 "units" broke fitting (bounds in absolute units: 2.3 % → 43 %), the panel ranges and Try-it. **Fix:** always fit in character heights, and convert only at export ("your character's height in engine units"). Panel ranges were widened.
* **P1, per-clip scale:** each clip got its own scale from its own median box height (42 vs 46 px for one video). **Fix:** one scale per video, the median over all its tracked frames.
* **P1, Unity collision order:** X and Y were both cast from the start position, and the floor was probed before moving horizontally. This caused corner clipping and "landing" in mid-air past a ledge. **Fix:**
  1. free move;
  2. resolve X;
  3. probe floor or ceiling from the new X with `Physics2D.BoxCast` (box shrunk by the skin);
  4. redo the step with the floor, for an exact landing time;
  5. re-check the ground at the new X to start falling in the same step.

  The Python playground was changed identically, with a regression test. All 6 Unity configurations were rechecked (2022.3.51 / 2022.3.62 / 6000.3.8 × two input backends): pass.
* **P1/P2:**
  * clip dialog: default range 5 s; changing the range discards the track; tracking can be cancelled; auto-detect shows a busy cursor;
  * tooltips explain the units (ch) and every metric;
  * the timeline has time ticks;
  * controls are locked during a fit;
  * the status bar shows the wall-clock fit time;
  * confidence shows "n/a (parameters edited)" after a manual edit;
  * Unity: optional coyote time and jump buffer (default 0 = fitted behaviour), `Teleport()`, and tooltips for the layer and JSON override;
  * requirements have upper bounds;
  * README notes Windows long paths.

### Fallout found while re-running the real cases
* The new "plateau = ground" rule mislabelled SuperTux's slow, flat jump apex (the bridge jump) as ground. **Fix:** a plateau must not be a dome (the run's ends must not be lower than its middle). Landing timing 175 % → 11 %.
* "Small hop onto a bump" (speedrun frames 30–70) kept failing: the fitted arc never reached the bump.
  * Fixes tried:
    1. release time initialized linearly in jump height;
    2. a "reach the platform" residual: a smooth penalty when the simulated apex stays below an observed raised landing floor.
  * Neither explained the clip. The contact sheet showed why: it is **Tux running over a smooth slope bump**, not a jump, and slopes are out of scope.
  * The metrics (landing timing 140 %, max height 64 %) had flagged it correctly. The clip was removed and the case documented.
  * A substitute clip (seg02 116–162) also failed (9.5 %, the jump clips a coin row) and was not used.
* Identifiability pruning once made model D *worse* (fixing jump cut and air control dropped D to the C level, 12 %). **Fix:** keep the pruned refit only if it is not more than 10 % worse.
* Selection tie-break among models with equal parameter counts now uses the score (BIC_eff), not list order.
* Final real results: SuperTux 4 clips, D (MEDIUM: the D/D2 ambiguity and jump-cut/release confounding are reported), 1.4–3.1 %. SMC B (HIGH), 2.6 / 3.3 %. Synthetic unchanged (D, all parameters within 5 %).
