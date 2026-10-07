# Reference Controller Fitter — Research Notes

Compiled 2026-10-06. Scope: inputs for implementation decisions (candidate model structure, CV pipeline,
model selection, Unity export). Every item lists source, key facts, and an **Implication** line.

Verification legend:
- **[V-src]** read in primary source (code / slides / paper text) during this session
- **[V-local]** verified on this machine (Unity DLLs / templates in `C:\Program Files\Unity\Hub\Editor\*`, project `.venv`)
- **[V-2nd]** secondary source (wiki / forum / blog summary), plausible but not primary
- **[UNVERIFIED]** from background knowledge; could not confirm a primary source this session

---

## 0. TL;DR — decisions these notes support

1. **Jump cut has (at least) three distinct real-world implementations**; they produce different
   trajectory signatures and should be separate candidate models, not one parameter:
   - *velocity cap on release* (Sonic: `if !held && vy < -4: vy = -4`; Celeste-style `min()` hold) → kink in
     velocity (discontinuity) at release, then same gravity;
   - *gravity multiplier while rising and not held* (GMTK `jumpCutOff`, SMB-style "held gravity" vs normal) →
     continuous velocity, change in curvature at release;
   - *velocity multiplier on release* (common Unity tutorial pattern `vy *= 0.5`) → velocity discontinuity
     proportional to vy. [UNVERIFIED as a named-game reference; widely used pattern]
   - Celeste additionally uses *hold-to-sustain* (`varJumpTimer`: vy held at launch speed for up to 0.2 s
     while held) — a 4th structure (min/max hold, Summerville et al.).
2. **Asymmetric gravity is universal** in references (SMB, GMTK, Pittman "series of parabolic arcs",
   Celeste half-grav apex). Model ladder should go: single g → g_up/g_down → +jump cut → +apex modifier.
3. **Apex modifier in Celeste is keyed on |vy| < threshold AND button held** (both sides of apex), not on
   time. Parameterize as (threshold speed, multiplier).
4. **Horizontal**: `MoveTowards(v, vmax*input, rate*dt)` with separate accel / decel / turn rates and an
   air multiplier is the shared structure (Celeste, GMTK, Sonic). Celeste: accel 1000, "reduce" 400 only for
   over-max same-direction; AirMult 0.65.
5. **Integrate exactly per piecewise-constant-acceleration segment** (Pittman: `p += v dt + ½a dt²; v += a dt`
   is exact when a is constant). Use the same integrator in the fitter and in the exported Unity code.
6. **Unity export**: `Rigidbody2D.velocity` → **obsolete warning (not error)** in Unity 6000.3.8f1; property
   `linearVelocity` does **not exist** in 2022.3. Use `#if UNITY_6000_0_OR_NEWER`. Unity 6.3 2D template
   ships with **Active Input Handling = Input System Package only** (`activeInputHandler: 1`) → legacy
   `UnityEngine.Input` compiles but throws `InvalidOperationException` at runtime. Guard with
   `ENABLE_INPUT_SYSTEM` / `ENABLE_LEGACY_INPUT_MANAGER`.
7. **Model selection**: frame residuals are strongly autocorrelated → naive AIC/BIC with n = frames will
   over-select complex models. Use clip-level (grouped / leave-one-clip-out) CV as the primary criterion,
   BIC with effective n as a secondary, and a "1-SE rule" to pick the simplest adequate model.
8. **CV**: For crisp pixel-art sprites, template matching (NCC) with a sub-pixel peak refine is likely more
   accurate than CSRT/KCF; use CSRT as a fallback/re-acquisition aid. Camera: phase correlation on a
   HUD-masked, background-only region, verified by ORB+RANSAC `estimateAffinePartial2D` (or LK median).

---

## 1. Platformer movement references

### 1.1 Kyle Pittman — "Math for Game Programmers: Building a Better Jump" (GDC 2016) [V-src]
- Slides: https://media.gdcvault.com/gdc2016/Presentations/Pittman_Kyle_BuildingBetterJump.pdf
- Vault/video: https://gdcvault.com/play/1023559/Math-for-Game-Programmers-Building ,
  https://www.youtube.com/watch?v=hG9SzQxaCm8
- Key facts (text extracted from slides):
  - Model: simple projectile, position/velocity integrated on a timestep, gravity the only force, no drag.
  - Time domain (peak height h, time to peak t_h):  **v0 = 2h / t_h**,  **g = −2h / t_h²**.
  - Space domain (foot speed v_x, horizontal distance to peak x_h):  **v0 = 2h·v_x / x_h**,
    **g = −2h·v_x² / x_h²**.
  - "Break our path into a series of parabolic arcs of different shapes", maintaining continuity of
    position and velocity, choosing a new gravity at the break. Applied to: **fast falling** (different
    gravity after apex), **variable-height jumping** (new gravity on release), **double jump** (new arc).
  - Integration: Euler "easy, unstable"; RK4 overkill; velocity Verlet recommended; **with constant
    acceleration, `pos += vel·dt + ½·acc·dt²; vel += acc·dt` is 100% accurate**; error when acc changes is
    ~Δacc·dt².
- **Implication for our implementation:** Express gravity params in designer units too (h, t_h, or x_h) for
  the UI/report — `g_up = 2h/t_h²`, `g_down = 2h/t_fall²`. Pittman's variable-height approach = "gravity
  switch on release" (continuous velocity). Our simulator should use the exact constant-acceleration update
  per frame (or analytic segments) so the fitted parameters transfer to the exported controller without
  integrator bias; export the *same* update rule.

### 1.2 Celeste — `Player.cs` (Maddy Thorson / Noel Berry, source released 2018) [V-src]
- https://github.com/NoelFB/Celeste/blob/master/Source/Player/Player.cs
  (raw: https://raw.githubusercontent.com/NoelFB/Celeste/master/Source/Player/Player.cs)
- Units: pixels and seconds (game is 320×180; y-down, so negative = up).
- Constants:

  | Constant | Value | Meaning |
  |---|---|---|
  | MaxRun | 90 | px/s max run speed |
  | RunAccel | 1000 | px/s² toward target |
  | RunReduce | 400 | px/s² when \|vx\| > max and input same direction (soft cap) |
  | AirMult | 0.65 | multiplies RunAccel/RunReduce in air |
  | HoldingMaxRun | 70 | max run while carrying |
  | Gravity | 900 | px/s² |
  | HalfGravThreshold | 40 | px/s, apex band |
  | MaxFall | 160 | px/s terminal |
  | FastMaxFall | 240 | px/s when holding down |
  | FastMaxAccel | 300 | px/s² rate maxFall moves toward FastMaxFall |
  | JumpSpeed | −105 | px/s |
  | JumpHBoost | 40 | px/s added to vx in input direction on jump |
  | VarJumpTime | 0.2 | s |
  | JumpGraceTime | 0.1 | s (coyote time) |
  | CeilingVarJumpGrace | 0.05 | s |
  | WallJumpHSpeed | MaxRun+JumpHBoost = 130 | |
  | DashSpeed / EndDashSpeed / DashTime | 240 / 160 / 0.15 | |

- Code structure (quoted from source):
  - Horizontal: `mult = onGround ? 1 : AirMult;`
    `if (Math.Abs(Speed.X) > max && Math.Sign(Speed.X) == moveX) Speed.X = Calc.Approach(Speed.X, max*moveX, RunReduce*mult*dt); else Speed.X = Calc.Approach(Speed.X, max*moveX, RunAccel*mult*dt);`
    → with no input (moveX = 0) it decelerates toward 0 at RunAccel (same rate as accelerating); turning
    also uses RunAccel. [structure V-src; the "else" branch details partly from memory of the file]
  - Gravity: `mult = (Math.Abs(Speed.Y) < HalfGravThreshold && (Input.Jump.Check || AutoJump)) ? .5f : 1f;`
    `Speed.Y = Calc.Approach(Speed.Y, maxFall, Gravity * mult * dt);`  → **half gravity on both sides of
    the apex, only while jump is held**; terminal velocity via Approach target.
  - Variable jump: `if (varJumpTimer > 0) { if (AutoJump || Input.Jump.Check) Speed.Y = Math.Min(Speed.Y, varJumpSpeed); else varJumpTimer = 0; }`
    → while held (≤0.2 s) vertical speed is *held at* launch speed (no deceleration); release just ends
    the sustain; gravity then applies normally. This is a "hold duration" jump, not a cut.
  - Jump(): `varJumpTimer = VarJumpTime; Speed.X += JumpHBoost * moveX; Speed.Y = JumpSpeed;`
- Derived: full-hold apex ≈ 105·0.2 + (105²−40²)/(2·900) + 40²/(2·450) ≈ 21 + 5.2 + 1.8 ≈ 28 px; tap apex
  (release at t≈0) ≈ 105²/(1800) ≈ 6 px (plus half-grav band only if held). [derived, not from source]
- **Implication for our implementation:** (a) Add a candidate "hold-sustain" model (constant upward speed
  for up to T_hold while held) — it produces a *linear* rise segment, which a gravity-only model can't fit.
  (b) Apex modifier parameterization: `{threshold |vy|, multiplier, requires_held}`. (c) Horizontal model
  needs at least `accel`, `decel` (no input), optional `turn`, optional `over_max_reduce`, and `air_mult`
  (or separate air accel/decel). (d) Celeste values are a good synthetic benchmark preset with known truth.

### 1.3 Super Mario Bros. (NES) physics
- Sources: Data Crystal notes https://datacrystal.tcrf.net/wiki/Super_Mario_Bros./Notes [V-2nd];
  speedrun.com physics guide https://www.speedrun.com/smb1/guides/eigmc (403 to fetch; references
  jdaster64's SMB physics doc) ; TASVideos https://tasvideos.org/GameResources/NES/SuperMarioBros (16 subpx
  per px) ; Summerville et al. 2017 (below) [V-src].
- Key facts:
  - Position/velocity stored as pixel + subpixel (+ sub-subpixel for velocity); 60 Hz.
  - Holding A **reduces gravity while moving upward**; when A is released (or apex passed) the stronger
    gravity applies → this is a **gravity switch**, not a velocity cap. [V-2nd, consistent across sources]
  - Initial jump velocity and both gravities **depend on horizontal speed at takeoff** (speed brackets).
    Data Crystal: "set to 0x30 (weaker) when going up and then 0xA8 (stronger) when falling ... values VARY
    based on jump power/horizontal velocity". [V-2nd]
  - Commonly cited table (jdaster64), units px/frame, hex px.subpx.subsubpx: [UNVERIFIED]
    | takeoff \|vx\| | vy0 | gravity A held (rising) | gravity otherwise |
    |---|---|---|---|
    | < 1.0 | −4.0 | 0.125 (0x0200/0x1000) | 0.4375 |
    | 1.0 – 2.3 | −4.0 | ~0.117 | 0.375 |
    | ≥ 2.3 | −5.0 | ~0.156 | 0.5625 |
    Max fall ≈ 4 px/frame; walk max ≈ 1.56, run max ≈ 2.56 px/frame. [UNVERIFIED]
- **Implication for our implementation:** (a) SMB-style = "held gravity" model: `g = held && vy<0 ? g_held : g_normal`
  — distinct candidate from cap/multiplier. (b) Takeoff-speed-dependent jump velocity exists in a famous
  reference; consider an optional "vy0 = a + b·|vx|" extension only if residuals demand it (likely out of
  scope for v1, but document as a known failure mode). (c) Work in px/frame at the source fps internally;
  the 60 Hz quantization of NES positions means ±1 px measurement noise floor.

### 1.4 Sonic Physics Guide (Sonic Retro SPG) [V-2nd mirror of SPG]
- Original: https://info.sonicretro.org/Sonic_Physics_Guide (blocked by anti-bot during this session);
  mirror used: https://github.com/trentbrew/sonic-physics-guide (`docs/10-forces.md`,
  `docs/11-forces-subtopics/air-state.md`, `jumping.md`).
- Units: px/frame at 60 fps; subpixel = 1/256 px.

  | Name | spx | px/frame |
  |---|---|---|
  | acceleration_speed | 12 | 0.046875 |
  | deceleration_speed (input opposite) | 128 | 0.5 |
  | friction_speed (no input) | 12 | 0.046875 |
  | top_speed | — | 6 |
  | air_acceleration_speed | 24 | 0.09375 (2× ground acc) |
  | gravity_force | 56 | 0.21875 |
  | jump_force | 6 px 128 spx | 6.5 (Knuckles 6) |
  | release cap | — | −4 |
  | top y speed (Sonic CD) | — | 16 |

- Logic: toward-input adds acc only if below top speed (Sonic 1 clamps; later games just stop adding — so
  speeds above top are preserved); opposite input applies dec (quirk: on reversal sets ±0.5); no input
  applies frc toward 0. **Jump release: "When the jump button is no longer being held while jumping, Y
  Speed is capped at −4"**, applied before movement & gravity. Air drag: `if (-4 < ysp < 0) xsp -= floor(xsp*… )/256`
  (≈ xsp·(1 − 1/32) per frame near apex). Jump is applied along ground normal.
- **Implication for our implementation:** (a) Sonic is the canonical **velocity-cap** jump cut —
  candidate model "cut = min(vy_up, v_cut)" with one param. (b) Separate `decel` (opposing input, large) vs
  `friction` (no input, small) — our horizontal model should allow decel ≠ friction ≠ accel; this is a
  standard 3-rate structure. (c) "Don't clamp above max, just stop accelerating" vs "clamp" is a modeling
  choice — use Approach/MoveTowards (clamp-to-target) by default; it matches Celeste/GMTK.

### 1.5 GMTK Platformer Toolkit (Mark Brown, 2022) [V-2nd: devlog]
- https://gmtk.itch.io/platformer-toolkit ; code walkthrough:
  https://gmtk.itch.io/platformer-toolkit/devlog/395523/behind-the-code
- Parameters exposed (~30): Max Speed, Acceleration, Deceleration, Turn Speed; Air Acceleration, Air
  Control, Air Brake; Jump Height, Time to Apex (Jump Duration), Down Gravity (multiplier),
  Jump Cutoff (multiplier, variable-height toggle), Terminal Velocity (speed limit), Coyote Time, Jump
  Buffer, double-jump count.
- Formulas: `gravity = (-2·jumpHeight)/(timeToApex²)` → `gravityScale = gravity / Physics2D.gravity.y`
  (× multiplier); `jumpSpeed = sqrt(-2·Physics2D.gravity.y·gravityScale·jumpHeight)`.
  `downwardMovementMultiplier` applied when vy < −0.01; **`jumpCutOff` is a gravity multiplier applied
  while rising with the button released**; horizontal uses `Mathf.MoveTowards(vx, target, rate·dt)`, choosing
  accel / decel (no input) / turnSpeed (input opposite to velocity), ground vs air sets.
- **Implication for our implementation:** GMTK's parameter set is effectively the target "interpretable"
  vocabulary and maps 1:1 to our param list. Its cut is the **gravity-multiplier** variant. Export naming
  (jumpHeight, timeToApex, downGravityMultiplier, jumpCutOff, maxSpeed, acceleration, deceleration,
  turnSpeed, airAcceleration, airBrake, terminalVelocity) will feel familiar to Unity users.

### 1.6 Summary: jump-release structures seen in references

| Structure | Reference | Signature in trajectory | Params |
|---|---|---|---|
| Velocity cap on release | Sonic (−4 px/f) | vy jumps to cap at release (if faster), same g | v_cut |
| Gravity switch while held | SMB, Pittman | continuous vy, curvature change at release | g_held, g_normal |
| Gravity multiplier when released & rising | GMTK jumpCutOff | continuous vy, curvature increase at release | cut_mult |
| Velocity multiplier on release | common tutorials [UNVERIFIED as reference] | vy scales by k at release | k |
| Hold-sustain (min/max hold) | Celeste varJump 0.2 s; Metroid min hold 10 f | linear rise while held, then parabola | T_max_hold, (T_min_hold) |
| Apex half-gravity while held | Celeste (\|vy\|<40, ×0.5) | flattened top | v_thresh, mult |

**Implication:** Cap vs multiplier vs gravity-switch are *identifiable* only from jumps with different
release times (short taps and long holds). Joint multi-clip fitting with varied jump heights is required;
the UI should warn when all jumps are full-height (cut parameters unidentifiable → fix to "none").

---

## 2. Prior art / competitors

### 2.1 Summerville, Osborn, Holmgård, Zhang — "Mechanics Automatically Recognized via Interactive Observation: Jumping" (FDG 2017) [V-src]
- https://arxiv.org/abs/1707.03865
- Data: modified NES emulator; reads **sprite table from the PPU** (exact positions, no CV); ~30% of NES
  platformers; experiments hold jump for k = 1..120 frames, wait 120 frames, reset.
- Model: 4-state automaton — ground, rising-with-button ("up-control"), rising-without-button ("up-fixed"),
  falling ("down"). In each state: `y(t) = y0 + (v_reset + m0·v0)·t + a·t²` → per-state **reset velocity,
  multiplier on entry velocity, constant gravity**. Global: **min hold, max hold** durations.
  This single structure subsumes velocity cap/reset (reset), velocity multiplier (m0), gravity switch (a).
- Mapping to Fasterholdt params: up-fixed reset = "instant brake"; up-control reset = takeoff velocity.
- Fitting: linear **Support Vector Regression** (ε-insensitive) to be robust to integer-pixel / bounding-box
  artifacts (e.g., Link tucking legs). Characters landing ±1 px from start are tolerated.
- Tracking: merge 8×8 sub-sprites by NPMI co-occurrence (>0.1); assignment by max-weight bipartite matching
  with N(0, 8 px) likelihood, 40 px new-track cost, tracks coast 4 frames (flicker).
- Findings: K-means → 3 jump families (Mario-style controlled; fixed tight jumps e.g. Castlevania; floaty
  SMB2 Luigi/Peach). Model fit SMB and Metroid well, struggled with Castlevania (fixed path, not physics).
- **Implication for our implementation:** Strong precedent for our candidate ladder. Adopt their
  per-phase "reset / multiplier / gravity" vocabulary as the most general jump model (top of ladder) and
  min/max hold. Use a robust loss (Huber / soft-L1 in `scipy.optimize.least_squares`) for the same reasons
  they used SVR. Note limitation they flag: some games are animation-driven (not physics) → our tool needs
  a "poor fit / non-physical" verdict.

### 2.2 Fasterholdt, Pichlmair, Holmgård — "You Say Jump, I Say How High? Operationalising the Game Feel of Jumping" (DiGRA/FDG 2016) [V-src partial]
- https://dl.digra.org/index.php/dl/article/download/771/771/768 ;
  https://www.researchgate.net/publication/314374307
- Measured SMB3, Super Meat Boy, Limbo etc. using **input plotter + input simulator + screen recording +
  a manual video measurement tool** (positions clicked relative to an anchor). Parameter categories: input,
  ground movement, jump, air control, jump release ("instant brake"), details.
- **Implication for our implementation:** Closest "video → jump parameters" precedent; it is manual. Our
  differentiators: automatic tracking, camera compensation, joint multi-clip fit, model selection, export.

### 2.3 Guzdial, Li, Riedl — "Game Engine Learning from Video" (IJCAI 2017) [V-src]
- https://www.ijcai.org/proceedings/2017/518 (PDF: /proceedings/2017/0518.pdf)
- Parses frames with **OpenCV template matching against a known spritesheet**, greedy matching across
  adjacent frames, converts to facts (Animation, Spatial, RelationshipX/Y, VelocityX/Y, CameraX); learns a
  rule-based engine by search when predicted vs actual frame differs beyond threshold. Tested on SMB
  (Infinite Mario); beats CNN baseline at next-state prediction.
- **Implication for our implementation:** Confirms template matching is the practical detector for
  sprite games. Their output is not an interpretable continuous controller — our niche (continuous
  parameters + uncertainty + export) is unoccupied. Their "CameraX" fact = explicit camera offset, same as
  our camera compensation step.

### 2.4 Tracker (Open Source Physics) [V-2nd: official site]
- https://opensourcephysics.github.io/tracker-website/ (GPL-3.0)
- Autotracker (template matching), calibration stick/points, time-varying coordinate systems and reference
  frames (can follow a moving camera if a reference point is tracked), Data Tool curve fitting, Model
  Builder (analytic & dynamic particle models overlaid on video), CSV export.
- **Implication for our implementation:** Proves the "track → fit → overlay model on video" UX. Our overlay of
  simulated vs observed trajectory on the reference video is the same idea; Tracker lacks game-controller
  semantics (input phases, hybrid ground/air, jump cut) and multi-clip joint fits. GPL — do not copy code.

### 2.5 Kinovea [V-2nd: docs]
- https://kinovea.readthedocs.io/en/latest/tracking/tracking.html (open source, GPL-2.0 [UNVERIFIED license version])
- Point/multi-point trajectory tracking with several algorithms; **2D stabilization uses a tracked point to
  compensate translation**; dedicated camera-motion-estimation mode for more complex motion
  (details page could not be fetched).
- **Implication for our implementation:** "Stabilize using a tracked static background point" is a
  useful manual fallback when automatic camera estimation fails (user clicks a background feature).

### 2.6 Other
- Jump-arc calculators (e.g., https://tools.puida.com/creative/gamedesign/jump-arc-calculator/, GDQuest
  https://www.gdquest.com/library/kinematic_jump_formulas/) — forward design tools, not inverse.
- No public tool was found that does automatic video → fitted platformer controller → engine export.
  [search-based, not exhaustive]
- System identification framing: our problem is grey-box identification of a **hybrid (switched)
  system** with known mode structure but partially unobserved inputs (button holds). Standard practice:
  simulate-and-compare (output error) rather than equation error on differentiated noisy positions.
  [UNVERIFIED as citation; standard sysid knowledge]
- **Implication:** Fit by forward-simulating the controller from inferred inputs and minimizing position
  error (output-error), not by regressing finite-difference accelerations (noise amplification ×1/dt²).

---

## 3. Computer vision

### 3.1 OpenCV trackers [V-local + V-2nd]
- Local env: **OpenCV 5.0.0** (opencv-contrib) in `.venv`. Available: `cv2.TrackerCSRT_create`,
  `TrackerKCF_create`, `TrackerMIL_create`, `TrackerDaSiamRPN/Nano/Vit` (need ONNX models), and
  `cv2.legacy.*` (Boosting, MOSSE, MedianFlow, TLD, CSRT, KCF, MIL). Also `phaseCorrelate`,
  `estimateAffinePartial2D` present. [V-local]
- General comparisons (PyImageSearch https://pyimagesearch.com/2018/07/30/opencv-object-tracking/ ;
  benchmark https://arxiv.org/pdf/2110.05102 ; https://www.sighthound.com/blog/csrt-kcf-opencv-tracker-real-time-video): CSRT most accurate, slower
  (~25 fps class); KCF faster, weaker under scale/occlusion; MOSSE fastest, least accurate; MIL poor at
  failure reporting; MedianFlow good on small smooth motion but fails on fast motion. [V-2nd]
- Domain specifics (engineering judgment) [UNVERIFIED — to be benchmarked on our synthetic set]:
  - Pixel-art sprites are small (8–48 px), change appearance per animation frame, flip horizontally, and
    move up to several px/frame. Correlation-filter trackers output **integer/box-center drift** and adapt
    their model to the background on fast motion.
  - **Template matching (`cv2.matchTemplate`, TM_CCOEFF_NORMED)** with a *template bank* (several animation
    frames + horizontal flips), a local search window from a motion prediction, and **sub-pixel parabola
    fit of the correlation peak** gives stable centers; combine with a mask to ignore sprite background.
  - Background subtraction is not usable with a scrolling camera unless frames are first registered.
  - Use a **bottom-center (feet) anchor** rather than bbox center — animation changes bbox height (Summerville
    notes bbox artifacts).
- **Implication for our implementation:** Primary = template-bank NCC + sub-pixel refine + motion-gated
  search; CSRT as alternative/recovery; report per-frame match confidence and drop/mark low-confidence
  frames (weight 0 in fit). Benchmark CSRT/KCF/MIL/template/LK on synthetic videos with known ground truth
  before choosing defaults (the parent plan already lists this).

### 3.2 Camera-motion estimation for 2D scrolling [mostly UNVERIFIED engineering guidance; OpenCV APIs V-local]
- Options:
  - **Phase correlation** `cv2.phaseCorrelate(a, b, window)` → global (dx, dy) sub-pixel + response.
    Fast, ideal for pure translation (2D scrolling cameras are pure translation, maybe zoom). Use a Hanning
    window (`cv2.createHanningWindow`). Breaks with large uniform areas / repetitive tiles (aliasing to tile
    period) and dominant moving foreground.
  - **ORB/FAST + match + `cv2.estimateAffinePartial2D(..., method=cv2.RANSAC)`** → 4-DOF similarity
    (translation, rotation, uniform scale), robust to outliers. Good verification; pixel-art has many
    repeated corners → ambiguous matches; restrict search via phase-correlation prior.
  - **LK optical flow** (`goodFeaturesToTrack` + `calcOpticalFlowPyrLK`) on a grid → **median flow
    consensus** (or mode via histogram). Robust and simple; median rejects the player and enemies if they
    cover < 50% of points.
- Parallax: multiple background layers move at different rates; the gameplay layer (tiles the player
  stands on) is what matters. Approach: cluster flow vectors (e.g., 1D histograms of dx) and take the
  **layer whose motion is consistent with the player's ground contact**, or let the user paint a ROI on
  the gameplay tile layer. Far backgrounds (sky) have near-zero motion and will bias a global median.
- HUD: static overlay → zero motion vectors that bias toward "no camera motion". Detect HUD as pixels with
  near-zero temporal variance while the camera demonstrably moves, or let the user draw HUD rectangles;
  mask them out (and mask the player bbox) before estimation.
- Pixel-art scrolling is integer-pixel at native resolution (often scaled by integer factor in captures);
  quantize/snap estimates to the native pixel grid when the scale factor is known — reduces jitter.
- Accumulate per-frame shifts to a world offset; drift is fine for short clips but verify with
  keyframe re-registration (frame t vs t−k).
- **Implication for our implementation:** Pipeline = mask(HUD ∪ player bbox ∪ user exclusions) →
  phase correlation (prior) → LK median consensus (or ORB+RANSAC) in a window around the prior → choose
  dominant layer cluster → confidence = inlier ratio / peak response. Expose a manual "track a background
  point" fallback (Kinovea-style). Validate on synthetic videos with known camera paths + parallax layer + HUD.

---

## 4. Model selection with autocorrelated residuals

Sources:
- Effective sample size: n_eff ≈ n·(1−ρ₁)/(1+ρ₁) for AR(1)-like residuals; general
  n_eff = n / (1 + 2Σ_k ρ_k) (Stan manual, https://mc-stan.org/docs/2_21/reference-manual/effective-sample-size-section.html) [V-2nd]
- Hurvich & Tsai (1989) AICc, Biometrika 76(2):297 — https://academic.oup.com/biomet/article-abstract/76/2/297/265326 [V-2nd]
- Blocked / grouped CV for dependent data: Roberts et al. 2017, Ecography
  https://www.wsl.ch/lud/biodiversity_events/papers/Roberts_et_al-2017-Ecography.pdf ; Bergmeir & Benítez
  "On the use of cross-validation for time series predictor evaluation" (2012); Liu 2024 BJMSP
  https://bpspsychub.onlinelibrary.wiley.com/doi/10.1111/bmsp.12330 (blocked CV performed best in
  simulations, generally better than AIC/BIC for time-series model selection) [V-2nd]

Key facts:
- AIC = 2k − 2 ln L; BIC = k ln n − 2 ln L. Both assume i.i.d. errors given the model. Tracking residuals
  of a simulated trajectory are **strongly autocorrelated** (model mismatch accumulates through
  integration; tracker error is temporally smooth) — ρ₁ ≈ 0.9+ is typical, so n_eff can be ~5–20× smaller
  than the frame count. Using n = frames makes ln L differences huge → any extra parameter "wins".
- BIC is consistent (selects the true model if in the set) and more conservative; AIC targets prediction
  and over-selects at large n. For "simplest adequate interpretable model", BIC-like behaviour is wanted.
- Blocked CV respects dependence; for us the natural block is **a whole clip** (or a whole jump segment).

**Implication for our implementation (recommendation):**
1. Primary criterion: **leave-one-clip-out (or leave-one-segment-out) CV** of the joint fit — fit
   parameters on K−1 clips, simulate the held-out clip from its inferred inputs, score RMSE in px (or in
   "character heights"). Falls back to leave-one-jump-segment-out when there are < 3 clips.
2. Secondary: **BIC with n_eff** — estimate ρ₁ (or the integrated autocorrelation) of residuals per clip,
   n_eff = Σ_clips n_c(1−ρ̂_c)/(1+ρ̂_c). Gaussian form: **BIC_eff = n_eff·ln(RSS/n) + k·ln(n_eff)**
   (i.e., log-likelihood down-weighted by n_eff/n, penalty on n_eff). Report ΔBIC_eff (>~6 = strong).
3. Decision rule: rank models by CV error; choose the **simplest model whose CV error is within 1 SE (across
   folds) of the best** ("1-SE rule"), and require that an added parameter reduce CV error by a meaningful
   absolute amount (e.g., > 0.5 px or > 5%) — sub-pixel gains are below tracking noise.
4. Also flag unidentifiable parameters (flat profile / huge confidence interval from the Jacobian
   (JᵀJ)⁻¹σ², inflated by n/n_eff) and auto-collapse them (e.g., no released jumps → cut = none).
5. Confidence intervals: use (JᵀJ)⁻¹ from `least_squares` scaled by n/n_eff, or a clip-level bootstrap
   (resample clips) — the bootstrap is more honest with few clips.

---

## 5. Unity 2D export facts

### 5.1 `Rigidbody2D.velocity` vs `linearVelocity` [V-local]
- Inspected `Editor\Data\Managed\UnityEngine\UnityEngine.Physics2DModule.dll`:
  - **2022.3.62f3**: no `linearVelocity` symbol at all → must use `velocity`. `bodyType` exists.
  - **6000.3.8f1**: `velocity` carries `[Obsolete("velocity has been deprecated. Please use linearVelocity. (UnityUpgradable) -> linearVelocity", false)]`
    — the attribute blob ends `00 00 00` (error flag = false) → **compiler warning CS0618, not an error**.
    Same for `velocityX`/`velocityY` → `linearVelocityX/Y`, `drag` → `linearDamping`,
    `angularDrag` → `angularDamping`, and **`isKinematic` ("has been deprecated. Please use bodyType.")** — also warning.
  - The "(UnityUpgradable)" tag means the Script Updater offers to auto-rewrite on import.
  - Docs: https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Rigidbody2D-linearVelocity.html
    (rename introduced with Unity 6.0 / 2023.3 [V-2nd]).
- **Implication:** Generated C# should use
  ```csharp
  #if UNITY_6000_0_OR_NEWER
      rb.linearVelocity = v;
  #else
      rb.velocity = v;
  #endif
  ```
  and `rb.bodyType = RigidbodyType2D.Kinematic` (never `isKinematic`). Compile-check against both installed
  editors' DLLs (2022.3 and 6000.3) with `-warnaserror` off but assert zero CS0618 in our generated file.

### 5.2 Ground check APIs [V-local for deprecations; API shapes UNVERIFIED-from-knowledge]
- `Physics2D.BoxCast(origin, size, angle, direction, distance, layerMask)` → `RaycastHit2D` (single).
- `Rigidbody2D.Cast(Vector2 direction, ContactFilter2D filter, RaycastHit2D[] results, float distance)` → int
  (casts all the body's attached colliders; ignores own colliders). List<RaycastHit2D> overloads exist in
  newer versions.
- `ContactFilter2D`: `useLayerMask`/`SetLayerMask`, `useTriggers`, `SetNormalAngle(min,max)`/`useNormalAngle`
  — normal angle 45°–135° filters "ground" contacts. `Rigidbody2D.IsTouching(ContactFilter2D)` and
  `GetContacts` also work for dynamic bodies.
- **In 6000.3.8f1 all `*NonAlloc` query methods are deprecated** ("BoxCastNonAlloc has been deprecated.
  Please use BoxCast.", same for Raycast/CircleCast/CapsuleCast/Overlap*). In 2022.3 they are not. [V-local]
- **Implication:** For a kinematic controller (we own velocity integration, matching the fitted model
  exactly), use `bodyType = Kinematic` + `Rigidbody2D.Cast` with a ContactFilter2D (layer mask + normal
  angle) for ground/ceiling/wall and move with `MovePosition`. Avoid NonAlloc APIs; `Rigidbody2D.Cast`
  with an array exists in both 2022.3 and 6000.x. Run the controller in `FixedUpdate` with a fixed dt equal
  to the fitted timestep (or substep) so the exported motion matches the fitter's integrator.

### 5.3 Input Manager vs Input System [V-local + V-2nd]
- Unity 6.3 (6000.3.8f1) built-in **2D template** (`com.unity.template.2d-cross-platform-2d-6.1.2.tgz`):
  `ProjectSettings.asset` → **`activeInputHandler: 1`** (0 = Input Manager (Old), 1 = Input System Package
  (New), 2 = Both) and `Packages/manifest.json` includes `"com.unity.inputsystem": "1.12.0"`. [V-local]
  Secondary sources say Input System became the default for new projects as of Unity 6.1
  (https://unity.huh.how/input/input-system/input-handling.html) and 6.3 shows an editor warning that the
  Input Manager "is marked for deprecation" [V-2nd].
- `UnityEngine.InputLegacyModule.dll` in 6000.3.8f1 has **no `[Obsolete]` on the `Input` class** (only on a
  couple of members, e.g., `isGyroAvailable`) → `Input.GetAxisRaw` / `GetButton` **compile fine**. [V-local]
- At runtime with handling = Input System only, any `UnityEngine.Input` call throws:
  `InvalidOperationException: You are trying to read Input using the UnityEngine.Input class, but you have
  switched active Input handling to Input System package in Player Settings.` (thrown from native code; not
  found in managed DLL strings) [V-2nd, consistent across many forum reports]
- Unity sets scripting defines `ENABLE_INPUT_SYSTEM` and/or `ENABLE_LEGACY_INPUT_MANAGER` according to
  Active Input Handling. [UNVERIFIED this session; well-known]
- **Implication:** Generated controller should read input through a tiny adapter:
  ```csharp
  #if ENABLE_INPUT_SYSTEM
      using UnityEngine.InputSystem;  // Keyboard.current / Gamepad.current
  #endif
  // ... read via InputSystem when ENABLE_INPUT_SYSTEM, else legacy Input when ENABLE_LEGACY_INPUT_MANAGER
  ```
  Requires `com.unity.inputsystem` package when ENABLE_INPUT_SYSTEM is on (it is, by definition). Keep the
  legacy path for 2022.3 projects. Note: compiling against bare UnityEngine DLLs for validation won't
  include `Unity.InputSystem.dll` unless referenced from the package cache — validation harness must define
  only `ENABLE_LEGACY_INPUT_MANAGER` or reference the Input System package assembly.

---

## 6. Open questions / not verified
- Exact SMB jdaster64 gravity/velocity table (primary doc is an image; fetch blocked) — [UNVERIFIED].
- Kinovea camera-motion algorithm details (page did not render).
- CSRT vs template matching accuracy on pixel-art sprites — needs our own benchmark.
- Whether `ENABLE_INPUT_SYSTEM` define is set when compiling outside the Editor (it isn't; it's an editor
  build define — our offline compile check must set defines manually).
