# Architecture

```
video ──► tracking/ ──────────────► reference.py ─────────────► fitting/ ─────────────► model params ──► export/
          (player box per frame,     (screen → world units,       (multi-clip least-squares    │            (JSON, Unity C#,
           camera path, confidence)   phases, inferred inputs)     over candidate models,      │             Godot GDScript)
                                                                   model selection)            ▼
                                                                                         playground.py / GUI
                                                                                         (synchronized playback,
                                                                                          keyboard play mode)
```

Everything is plain Python (NumPy/SciPy/OpenCV). The GUI is PySide6. All persistent state is
JSON (`*.rcf.json` projects, `rcf-controller` parameter files).

## Package map

| module | responsibility |
|---|---|
| `rcf/model.py` | **Movement IR**: `ControllerParams` and the candidate models A, B, C, D, D2 and E (which parameters are free and which are tied or neutral). Also contains the **exact integrator** `advance()` |
| `rcf/sim.py` | `Scenario` (start state, input timeline, observed floors and ledges) and `simulate()`: event-driven simulation sampled at arbitrary times |
| `rcf/tracking/video.py` | probing, sequential reading, LRU random access for the GUI |
| `rcf/tracking/tracker.py` | trackers: `template` (default), `csrt`, `kcf`, `mil`, `flow`, `hybrid`; colour model |
| `rcf/tracking/camera.py` | camera translation estimation, HUD mask, layer selection, despiking |
| `rcf/tracking/pipeline.py` | one streaming pass: camera + tracker + loss gating/recovery. `TrackResult` with `slice`/`splice` |
| `rcf/tracking/detect.py` | automatic player detection (assistive only) |
| `rcf/reference.py` | `ReferenceTrajectory` (normalized gameplay space), grounded/airborne segmentation, Viterbi input decoding, take-off timing → `Inference` |
| `rcf/fitting/fit.py` | data coverage analysis, initial estimates, `Problem` (parameters + per-clip latents + residuals + Jacobian sparsity), `fit_model`, `fit_inputs_only` |
| `rcf/fitting/selection.py` | candidate search, polish pass, scoring, confidence and explanations, optional leave-one-clip-out CV |
| `rcf/fitting/metrics.py` | normalized behavioural metrics |
| `rcf/project.py` | `Clip` and `Project` (serialization, derived reference/inference caches) |
| `rcf/playground.py` | playable level with AABB collisions, using the same integrator and collision scheme as the Unity export |
| `rcf/export/` | exporters, templates, `compile_check` (dotnet + Unity assemblies) |
| `rcf/synthetic/render.py`, `rcf/benchmark.py` | ground-truth benchmark: render videos of a known controller, then evaluate |
| `rcf/gui/` | main window, clip dialog, views, timeline, parameter panel, worker threads |

## Movement IR and the integrator

State: `x, y, vx, vy, grounded, jump_active` in world units with y up. The position is the
character's **feet**.

* **Horizontal.** The target speed is `u·max_speed`.
  * Rates: `ground_accel` when speeding up, `ground_decel` when there is no input or the speed is above the maximum, and `turn_accel` when the input opposes the velocity (it brakes to 0 first, then accelerates).
  * In the air, every rate is multiplied by `air_control`.
* **Vertical.**
  * Jump: sets `vy = jump_velocity`.
  * Gravity is `rise_gravity` while `vy > 0` and `fall_gravity` otherwise.
  * Releasing jump while rising multiplies `vy` by `jump_cut` (model D). Alternatively, rise gravity is multiplied by `release_gravity_mult` while jump is not held (model D2, Mario-style).
  * Optional: an apex band where gravity is multiplied by `apex_gravity_mult` while jump is held, and `max_fall_speed` (model E).

`advance(state, params, u, held, h, floor)` integrates **exactly**. Within the step it finds the
next time the acceleration changes:
* the target speed is reached;
* vy crosses 0, the apex band or terminal velocity;
* the character lands on `floor` (found by solving the quadratic).

It integrates the constant-acceleration segment in closed form and repeats. Consequences:
* Results do not depend on the step size, so the fitter can sample at video frame times
  while the prototype runs at 60 Hz and Unity at `fixedDeltaTime`, and all agree (verified in Unity: dy ≤ 0.005 units).
* The simulated trajectory is a continuous, piecewise-smooth function of the parameters and the input event times, which suits gradient-based least squares.
* The same ~60 lines are ported to C# and GDScript.

## From video to gameplay space

1. **Tracking** (`pipeline.track_clip`), a single streaming pass per clip. Per frame:
   1. Estimate the camera shift, masking the HUD and the player.
   2. Run the tracker, searching around a constant-velocity prediction.
   3. Gate the result:
      * appearance score clearly below its recent median (capped at 0.6);
      * or an unexplained jump with a mediocre score.
   4. If gated, try to recover, in order:
      1. local template re-detection near the prediction;
      2. a colour-histogram fallback (pose-invariant);
      3. full-frame re-detection with a strict threshold.
   5. If nothing works, coast on the prediction and mark the frame `lost`. Lost frames are excluded from fitting.

   The `template` tracker:
   * matches a bank of appearances (initial, slowly-updated, mirrored, plus distinct animation frames learned on confident frames);
   * searches vertical scales 0.8–1.25, so squash and stretch keep the feet (box bottom) accurate;
   * refines the match position to sub-pixel accuracy.
2. **Camera.**
   * Background features are tracked with forward-backward-checked LK, and a weighted mean-shift picks the dominant translation mode.
   * The full-frame estimate is cross-checked against the band **below the player's feet**, which is by definition the gameplay layer. If that band is confident and disagrees, it wins. This rejects slow parallax layers.
   * Shifts are measured against a keyframe when the expected offset is under 16 px, so there is no drift while the camera is still. A frame-to-frame estimate is the fallback and cross-check.
   * A 3-tap median on the steps removes single bad steps.
   * Static screen regions (HUD) are masked when the camera moves.
   * A user region of interest can override all of this.
3. **Normalization** (`trajectory_from_track`, `Project.update_scales`):
   * world position = feet on screen + camera offset;
   * y is flipped so it points up;
   * the result is divided by the scale (pixels per **character height**). There is one scale per video: the median tracked box height over all clips of that video.
   * Fitting always happens in character heights; engine units are applied only at export.
4. **Inference** (`reference.infer`).
   * **Grounded segmentation:**
     * flat runs that are not an apex (an apex is only briefly flat);
     * V-shaped 1–2-frame contacts for bunny-hops;
     * short low blips between equal ground levels are animation offsets, not jumps.
   * Airborne phases are classified as jumps or walk-offs. Take-off time is estimated to sub-frame accuracy by extrapolating a parabola.
   * Landing floors come from the observed ground level after each phase, so platforms at other heights work.
   * **Horizontal input** comes from a 3-state Viterbi decode. Each state predicts the velocity change over 0.1 s, and switches are penalised, which makes it robust to speed ripple from frame drops and animation.
   * Jump release times are initialized by ranking jump heights across **all** clips.

## Fitting

`Problem` builds one parameter vector:
* the model's free controller parameters, shared by every clip (log-transformed for scale parameters);
* **per-clip latent input variables**:
  * axis event times ±0.12 s;
  * jump press ±0.05 s;
  * jump release offset (models with variable jump only);
  * initial velocity when a clip starts in motion.

The residuals are:
* x and y position errors at every valid frame, divided by the clip's extent and weighted by `sqrt(weight / n_frames)`, so every clip counts equally;
* "reach the platform": for jumps observed to land on a raised floor, the shortfall of the simulated apex below that floor. Landing is a discrete event, so without this term an arc that peaks just below the platform gets no useful gradient;
* tiny priors that pull "modifier" parameters toward neutral.

The solver is `scipy.optimize.least_squares`:
* trust-region reflective with bounds;
* soft-L1 robust loss;
* a sparse Jacobian (one clip's latents only affect that clip), so the cost of a finite-difference Jacobian does not grow with the number of clips.

Starts:
1. the parent model's solution reproduced exactly, so a nested model can't do worse than its parent;
2. data-driven initial guesses with variable-jump modifiers switched on;
3. the same with ranked or fixed-fraction release times;
4. three random perturbations.

**Coverage analysis** fixes parameters the clips cannot identify and says why:
* no jump in any clip → vertical parameters;
* no short/long jump pair → jump cut;
* no input change in the air → air control;
* no stop → decel tied to accel;
* no turn → turn tied to decel;
* no long fall → terminal velocity.

After a fit, any modifier whose relative 1σ uncertainty exceeds 100 % is fixed to neutral and the model is refitted.

## Model selection

1. Fit A → B → C → D/D2 → E, each warm-started from its parent.
2. **Polish pass**: refit every model from the best model's parameters and keep the better solution. This removes most luck from local minima.
3. Score each model:
   * **BIC on the effective sample size**: `n_eff = Σ n(1−ρ)/(1+ρ)`, where ρ is the lag-1 autocorrelation of the residuals;
   * the mean squared error is floored at the tracking-noise level, so once a model fits down to the noise, extra parameters only cost.
4. A model is **adequate** if it is within the BIC tie margin **or** practically as good (trajectory error ≤ best × 1.1 + 0.2 pp). The simplest adequate model wins. The practical rule is needed because with hundreds of frames, BIC still rewards parameters that only absorb tracking bias.
5. Optional leave-one-clip-out cross-validation (`select_model(cross_validate=True)`): fit on n−1 clips, refit only the held-out clip's input timings with the parameters frozen, and score it.

**Confidence** starts at HIGH and is lowered:
* to LOW when any clip's trajectory error is over 8 % ("model cannot explain clip …");
* to MEDIUM when a clip is between 5 and 8 %, a parameter is poorly constrained (over 30 %), or the optimizer did not converge.

Ambiguities are reported, for example D vs D2 fitting equally well, or constant vs asymmetric gravity.

## Metrics (`fitting/metrics.py`)

All metrics are fractions:

| metric | definition |
|---|---|
| trajectory | position RMSE ÷ clip extent (the primary metric and the acceptance metric) |
| velocity profile | RMSE of the Savitzky–Golay derivatives of reference and simulation (same filter on both) ÷ peak speed |
| apex timing | apex time difference ÷ air time |
| landing timing | landing time difference ÷ air time |
| max height | apex height difference ÷ jump height |
| horizontal | net displacement difference ÷ horizontal extent |

## Exports and their verification

* **JSON** (`rcf-controller`) holds the parameters (0 = unlimited fall speed) plus metadata.
* **Unity C#** is a complete MonoBehaviour:
  * a Kinematic Rigidbody2D with its own collision, using `Physics2D.BoxCast` with a `ContactFilter2D` layer mask and a box shrunk by the skin. Each step:
    1. free move with the exact integrator;
    2. resolve walls (X);
    3. probe floor or ceiling from the new X;
    4. if a floor is hit, redo the step with that floor so the landing happens at the exact time;
    5. check whether the character walked past an edge;
  * the Python playground uses the same order;
  * optional coyote time and jump buffer (off by default, so behaviour equals the fit);
  * input is read through `#if ENABLE_INPUT_SYSTEM`, otherwise the legacy Input Manager is used;
  * parameters are Inspector fields or loaded from JSON.
  * Verification: `compile_check.py` (dotnet against the installed Unity assemblies) and `tools/unity_batch_check.py` (real Unity batch mode, both input backends, trajectory compared with the Python model).
* **Godot 4** is a CharacterBody2D script that ports the same integrator and uses `move_and_slide`. It was verified headless in Godot 4.7.2 by `tools/godot_check.py`: matches the model, with landing up to one frame later.

## GUI (`rcf/gui`)

* `MainWindow`:
  * the clip list (left);
  * `ReferenceView` showing the video, the tracked box, the orange feet path and the prototype path projected back into the video (left centre);
  * `PrototypeView` in world space, with playback or keyboard play mode (right centre);
  * `TimelineWidget` with input lanes, curves, seeking and editing (bottom);
  * `ParamsPanel` with the model choice, live parameters, error and report (right).
* `ClipDialog` handles import, range, box, auto-detect, track, and correct-and-re-track.
* Tracking and fitting run in `QThread`s. Fit progress streams parameter snapshots, so the prototype visibly converges.
