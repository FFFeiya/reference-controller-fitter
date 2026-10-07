# Research

The detailed literature notes (sources, key facts, verification level) are in
[`research/RESEARCH_NOTES.md`](research/RESEARCH_NOTES.md). This file records what was **used**,
what was **rejected**, and the **experiments** behind each technical decision.

## 1. Prior art and competitors

| work | what it does | relation to this tool |
|---|---|---|
| Summerville et al., *Mechanics Automatically Recognized via Interactive Observation: Jumping* (FDG 2017) | infers jump models from emulator traces (ground / rising-held / rising-released / falling states, with velocity resets and multipliers per state); uses robust regression | Closest precedent. It confirmed our state structure and the robust loss. They have ground-truth input from emulator traces; we only have video, hence input inference + latent timing |
| Fasterholdt, Pichlmair, Holmgård, *You Say Jump, I Say How High?* (2016) | manual measurement of jump arcs from video to compare game feel | This is the manual workflow we automate |
| Guzdial, Li, Riedl, *Game Engine Learning from Video* (IJCAI 2017) | learns rule-based engines from sprite-level video parsing | Uses template matching for sprites (as we do). It learns discrete rules, not continuous, editable parameters |
| Tracker (Open Source Physics), Kinovea | physics video analysis: point tracking, then model overlay | Proven "track → fit → overlay the model on the video" UX. Single trajectory, no controller or inputs, no multi-clip shared model |
| GMTK Platformer Toolkit (Mark Brown) | interactive toy that exposes ~10 movement parameters | It is the *forward* direction (parameters → feel). Its parameter set matches our IR closely, which confirms that the IR is designer-readable |

No public tool was found that goes from video to a fitted controller to an engine export. That is the gap this tool fills.

## 2. Movement model structure (from game references)

* **Pittman, *Building a Better Jump* (GDC 2016)**:
  * jump height and time-to-apex determine `v0` and `g` (`g = 2h/t²`, `v0 = 2h/t`);
  * separate fall gravity is recommended;
  * position integration that is exact for constant acceleration.

  We went further: the integrator splits steps where acceleration changes, so it is exact for the whole piecewise model and step-size independent.
* **Variable jump height comes in several mechanisms**:
  * a velocity *multiplier* on release (common tutorial pattern);
  * a velocity *cap* on release (Sonic);
  * stronger gravity while the button is not held (Super Mario Bros);
  * hold-sustain of the jump velocity for up to N seconds (Celeste `VarJumpTime`).

  These are only distinguishable when the clips contain both short and long jumps. We implement the multiplier (model **D**) and held-gravity (**D2**) as competing candidates and report when they are tied. Cap and hold-sustain are documented as future candidates (see KNOWN_LIMITATIONS).
* **Celeste**: half gravity near the apex while jump is held (`HalfGravThreshold`), plus `MaxFall`. These became model **E** (apex gravity band + terminal velocity + separate turn rate).
* **Sonic Physics Guide**: separate acceleration, deceleration and friction, and air acceleration. These map to `ground_accel`, `ground_decel`, `turn_accel` and `air_control`.

## 3. Computer vision decisions — measured, not assumed

### 3.1 Tracker comparison (synthetic benchmark, ground-truth boxes, 7 clips)

Feet position error after removing the constant offset that comes from the box convention:

| tracker | mean | max | lost | time/clip |
|---|---|---|---|---|
| template (fixed scale) | 0.41 px | 3.56 px | 0 % | 1.5 s |
| **template + vertical scale search** | **0.15 px** | **1.03 px** | 0 % | 3.0 s |
| flow (median LK in box) | 0.54 px | 3.53 px | 0 % | 1.9 s |
| CSRT | 1.07 px | 6.02 px | 0.6 % | 3.4 s |
| KCF | 2.06 px | 5.00 px | 0.4 % | 1.7 s |
| MIL | 1.99 px | 24.6 px | 3.4 % | 8.8 s |
| hybrid (template + colour-segmentation extents) | 1.00 px | 7.57 px | 0 % | 1.6 s |

Decision: **template matching** became the default. Then:
* The squash-and-stretch sprite showed that a fixed-size box puts the "feet" wrong by up to 3.5 px. That biased the fitted gravities by 7–10 %. Vertical-scale search fixed both.
* Colour segmentation (hybrid) lost because the dark outline is not in the colour model and the box edges jitter. It was kept only as a *pose-invariant re-detection fallback*.

On real footage (SMC, 15 fps, strong animation) every tracker failed at first (CSRT 61 % lost, template 56 %). Adding three things brought template tracking to 5.9 % lost:
* an appearance bank (distinct animation frames learned on confident frames);
* the colour fallback;
* an fps-scaled search radius.

SuperTux stays at 1–3 % lost.

### 3.2 Camera estimation

Methods tried:
* phase correlation (fallback only: sensitive to the moving player and to HUD);
* LK features with a median;
* LK features with a weighted mean-shift **mode** (chosen; robust to the player and other moving objects).

Problems found and fixes:

| problem | where | fix |
|---|---|---|
| 1 px drift on a *static* camera from frame-to-frame accumulation | synthetic | keyframe-relative estimation cross-checked with frame-to-frame. Max error 1.03 → 0.21 px |
| Naive keyframing broke on fast pans (LK fails beyond about 20 px) | synthetic | keyframe only while the expected offset is under 16 px |
| A single wrong step (coin-collect animation) left a permanent offset | SuperTux | 3-tap median on the camera steps |
| The parallax hills layer dominated the features. The camera estimate was 120 px vs the true 293 px over 10 frames (brute-force verified on the ground strip) | SMC | cross-check with the band below the player's feet, which is the gameplay layer by definition |
| The HUD (score counter) is static on screen while the world scrolls | all | temporal-variance static mask, applied only when the camera moves |

### 3.3 Automatic player detection

Tried:
1. **Three-frame differencing.** It sees only the edges of flat-coloured sprites and fires on HUD digits.
2. **A camera-aligned median background mosaic with blob chaining.** Better, but it fails on clips where the player stands still for most of the clip (the player becomes part of the median), and parallax objects (clouds) look like movers.

Kept as an *assistive* button that always asks for verification. Manual first-frame box selection is the primary path, as the brief allows.

## 4. Model selection with autocorrelated residuals

* Plain AIC/BIC over every frame overfits, because frame residuals are strongly autocorrelated. We use the **effective sample size** `n(1−ρ)/(1+ρ)` and floor the MSE at the tracking noise.
* Even so, on the synthetic benchmark BIC_eff picked E over D for a 0.05 % gain. The extra parameters were soaking up sprite-animation tracking bias. We therefore added a **practical-significance rule**: within 10 % relative + 0.2 pp of the best error counts as adequate, and the fewest parameters win. This is the "one standard error rule" idea from the literature, applied to the behavioural metric.
* Leave-one-clip-out CV is implemented (`cross_validate=True`). It is not the default because it multiplies fit time by the number of clips: 112 s vs about 40 s on the 7-clip synthetic set.
  * Held-out trajectory error: D 2.62 %, D2 2.63 %, E 2.71 %. CV also chooses D, agreeing with BIC_eff + practical significance.
  * The held-out error is higher than the in-sample 0.2 % because some clips are the *only* evidence for a parameter. For example, without the air-turn clip, air control is unconstrained.
* Local minima made the comparison luck-dependent (D once 5.7 %, once 2.5 % on the same SuperTux data). Two fixes:
  * nested starts that reproduce the parent model;
  * a **polish pass** that restarts every model from the best one's parameters.

## 5. Unity facts (verified locally)

* `Rigidbody2D.velocity` is deprecated in Unity 6 (warning), and `linearVelocity` does not exist in 2022.3. Our controller sidesteps both: Kinematic body + `MovePosition` + `Rigidbody2D.Cast`.
* New Unity 6 2D projects default to the Input System package only. The legacy `Input` class still compiles but throws at runtime. Hence `#if ENABLE_INPUT_SYSTEM`, verified with both handlers in batch mode.
* `Physics2D.Simulate` works in edit mode with `SimulationMode2D.Script`. That is what the batch-mode trajectory test uses.

## 6. Things considered and not done

| idea | why not |
|---|---|
| Neural or black-box controllers | not editable, against the product principle ("editability over 0.2 % accuracy") |
| SIFT / ORB + RANSAC homography for the camera | 2D platformer cameras are pure translations. Homographies add degrees of freedom that overfit to parallax |
| Optimizing in a game engine via ML-Agents-style search | far heavier, and our exact integrator is a faithful, fast surrogate (verified against Unity) |
| Downloading YouTube footage | platform terms; Wikimedia Commons material with clear licences was used instead |
