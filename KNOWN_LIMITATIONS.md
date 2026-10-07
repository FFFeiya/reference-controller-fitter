# Known limitations

This file lists what the tool does **not** do, or does only partially. Every item has been
observed or checked, not guessed.

## Scope
* 2D side-view platformers only. The supported moves are run, stop, turn, jumps (variable height), falling, walk-off and air control.
  * Not modelled: wall jump/slide, ledge grab, dash, double jump, swimming, slopes, moving platforms, enemy stomps or bounces, knock-back.
  * Clips that contain these moves will not be explained, and the tool reports them as LOW confidence ("model cannot explain clip").
* **No level geometry model.** The fitter knows where the character landed (the observed floor heights) and when it walked off a ledge. It does *not* know about ceilings or walls.
  * Head-bumps into blocks and running into walls cannot be explained. Choose clip ranges that avoid them (the SuperTux/SMC examples do).
* **Slopes and curved ground** are not modelled. Real example: in the SuperTux speedrun, Tux running over a smooth bump looks like a small hop in the trajectory. The fitter cannot explain it (landing timing 140 %, max height 64 %). Exclude such ranges.
* There is one maximum speed. Games with a walk/run button (SuperTux, SMB) have two. Fit walking and running clips separately, or the fit will compromise between them.
* Variable jump: *velocity multiplier on release* (D) and *stronger gravity while released* (D2) are implemented. *Velocity cap on release* (Sonic) and *hold-sustain timer* (Celeste `VarJumpTime`) are not. Those games are approximated by D/D2.
* No coyote time or jump buffering in the IR. Both are invisible in footage of a competent player.

## Tracking / video
* **Automatic player detection is unreliable.** It fails when the character stands still for most of the clip (it merges into the median background) and when parallax decorations move, and it mistakes HUD and clouds for players. It is an assist button that always asks for verification. **Drawing the box is the supported path.**
* Characters that change appearance completely (e.g. Guacamelee's dimension swap) or are heavily occluded lose track. The dialog marks those frames red and supports *draw the box → re-track from here*.
* The 15 fps SMC recording works, but only 8–12 samples per jump arc limit timing precision.
* Videos with dropped game frames (e.g. the SuperTux speedrun, whose in-game FPS counter shows a minimum of 11 fps) show velocity ripple. The Viterbi input decoder is robust to it, but the per-clip error is higher (about 3 % vs about 1.5 %).

## Camera compensation
* It assumes a translating camera. Zoom, rotation, screen shake bigger than a few pixels and perspective (FEZ) are not handled.
* Strong parallax:
  * the estimator prefers the layer directly below the player's feet;
  * if the player is airborne high above any visible ground for a long time, it falls back to the full-frame consensus, which may be a parallax layer;
  * confidence is shown, and the user can draw a background region or disable compensation.
* With static cameras and almost no texture, the estimate is near zero (correct), but the confidence can read MEDIUM.

## Fitting
* Input timing is unknown in video. Inferred events and fitter-refined latent timings are a *plausible* explanation, not the true button presses.
  * The jump-cut factor and the release time trade off against each other. `jump_cut` is only well determined when short and long jumps are both present.
  * The report flags poorly constrained parameters (±%) and ties between equally good models.
* Results are local optima of a non-convex problem. Multi-start, nested starts, the polish pass and a "reach the platform" residual (for landings on raised floors) make them stable in our tests. Even so, D and D2 swapped places for SuperTux between data revisions. They fit within 0.1 pp of each other, and the tool reports this as an ambiguity.
* Units: everything is fitted in character heights, with one scale per video (the median tracked box height). Sprites whose box height changes a lot with animation (crouching, rolling) make this scale less reliable.
* Model comparison uses BIC on the effective sample size plus a practical-significance rule. Leave-one-clip-out CV exists (`select_model(cross_validate=True)`) but is off by default because of run time.
* Fit time grows with clips × models: about 20–60 s for 5–7 clips with the full A–E search (6 models + polish pass).

## Exports
* **Unity:** verified by compiling against the 2022.3 and 6000.3 assemblies and by batch-mode runs with both input backends.
  * Not verified on other major versions (2021 LTS or older).
  * The controller handles axis-aligned solid geometry. One-way platforms and slopes are not handled.
* **Godot 4 GDScript:** run headless in Godot 4.7.2 (`tools/godot_check.py`): no script errors, run speed exact, apex within 0.002 units, final x within 0.001 units.
  * Unlike the fitter, landing happens at the physics-frame boundary (`move_and_slide`), so it is up to one frame later (measured: 1 frame).
  * The script uses the built-in `ui_left`, `ui_right` and `ui_accept` actions. Remap them to A/D/Space in the Input Map if you want those keys.
  * Only Godot 4.7 was tested.
* Units: parameters are in character heights. At export you enter your character's height in engine units, and all lengths are scaled.

## GUI
* Tested with the offscreen Qt platform (automated screenshots and tests) on Windows 11. It was not tested on macOS or Linux, though nothing is Windows-specific.
* Random access in long videos uses OpenCV seeking. Scrubbing far back in long, high-resolution files can lag.
