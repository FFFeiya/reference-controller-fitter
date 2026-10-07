# Real gameplay reference footage — sources

All files were downloaded directly from Wikimedia Commons (`upload.wikimedia.org`) with
`curl.exe -L -A "ReferenceControllerFitter/0.1 (educational research tool)"`. No DRM, login,
paywall or platform anti-download measure was involved. Licenses were checked through the Commons
API (`prop=imageinfo&iiprop=extmetadata`), which returns the license metadata shown on each file page.
Downloaded on 2026-10-06.

Segments were cut with `ffmpeg -ss <start> -i <orig> -t <dur> -c:v libx264 -crf 16 -preset slow -pix_fmt yuv420p -fps_mode passthrough -an`.
They keep the original resolution and fps. Segment times below are **absolute times in the original file**.
Times *inside* a segment are given as `rel`. Jump times and bounding boxes are approximate. They
were read by eye from 3–4 fps contact sheets and full-resolution first frames.
Bounding boxes are `(x, y, w, h)` in segment pixels, origin top-left.

Frame timing: all originals have constant PTS steps. For 30 fps the steps alternate 0.033/0.034 s
because of rounding, which is normal. `mpdecimate` found no runs of duplicated frames in any segment.
Both checks look at the container only. They cannot detect frames the game itself dropped
(see the SuperTux Frosted Fields notes).

## Summary

| # | short_name | Game | License | Segment files | fps | Resolution | Usability |
|---|---|---|---|---|---|---|---|
| 1 | supertux_l1 | SuperTux 0.6.x (Icy Island lvl 1) | GPL | `supertux_l1_seg01.mp4`, `supertux_l1_seg02.mp4` | 30 | 1280×720 | **Good** (best overall) |
| 2 | supertux_ff | SuperTux 0.6 (The Frosted Fields) | GPLv3 | `supertux_ff_seg01.mp4` | 30 | 1600×900 | **Good** (speedrun, game fps unstable) |
| 3 | smc | Secret Maryo Chronicles | GPL | `smc_seg01.mp4`, `smc_seg02.mp4` | **15** | 960×608 | **Good content, low fps** |
| 4 | flinthook | Flinthook (Tribute Games), early build | CC BY-SA 3.0 | `flinthook_seg01.mp4` | 30 | 1920×1080 | Fair: fixed camera, but motion is mostly hookshot plus combat |
| 5 | guacamelee | Guacamelee! STCE (DrinkBox) | CC BY-SA 3.0 | `guacamelee_seg01.mp4` | 60 | 1280×720 | Fair/stress test: wall jumps, dimension swaps, vertical scroll |
| 6 | tobutobugirl | Tobu Tobu Girl (Game Boy homebrew), TAS | Public domain (video) | `tobutobugirl_seg01.mp4` | 60 | 1600×1440 (10× integer upscale of 160×144) | Fair/stress test: bounce-on-enemies jumping, vertical auto-follow camera, TAS inputs |
| – | fez | FEZ trial | CC BY-SA 3.0 | original only, no segment | 30 | 1280×720 | **Rejected** (see below) |

---

## 1. supertux_l1 — SuperTux, level 1 "pacifist run"

- **File page:** https://commons.wikimedia.org/wiki/File:SuperTux_level_1_pacifist_run.webm
- **Direct URL:** https://upload.wikimedia.org/wikipedia/commons/b/b3/SuperTux_level_1_pacifist_run.webm
- **Author:** Video: Dabmasterars (Commons uploader, "own recording"). Software and assets: SuperTux Development Team.
- **License:** GPL (Commons tag "GPL", http://www.gnu.org/licenses/gpl.html)
- **Original file:** `SuperTux_level_1_pacifist_run.webm` (VP9, 1280×720, 30 fps CFR, 36.87 s, 11.2 MB). Recorded with OBS, cropped, re-encoded with HandBrake at CQ 25.
- **Camera:** smooth horizontal scroll that follows Tux, plus some vertical scroll when Tux climbs or drops (for example seg02 rel 0–2.5). The mountain backdrop is a parallax layer. When Tux stands still at the left edge, the camera does not move.
- **HUD/overlays:** coin counter top-right (~1190–1270, 8–30). Tutorial billboards show **Tux images** (distractor sprites that look like the player). At the end of the level an end-of-level stats panel is drawn semi-transparently (seg02 rel ≳ 9.5 s).
- **Compression:** mild VP9 artifacts on the ice texture. The sprites stay clean.

### supertux_l1_seg01.mp4 — abs 0.0–9.0 s (270 frames)
- **Action:** Tux starts standing at the left edge and runs right along flat ground. He makes a chain of jumps over small ice pillars, gaps and coin blocks: a short hop at rel ≈2.4, a jump at ≈3.6, a long running jump at ≈4.6–5.4, then jumps at ≈6.4, ≈7.0 (onto a pillar), ≈7.5 and ≈8.5. There is little true stopping in this segment.
- **Character:** small Tux, a black-and-white penguin with orange beak and feet, facing right.
- **First-frame bbox:** **(94, 426, 36, 38)**. He stands on the ground line at y≈464.

### supertux_l1_seg02.mp4 — abs 21.0–34.0 s (390 frames)
- **Action:** Tux stands on a wooden bridge, walks off and drops down two levels (rel ≈0.4–2.3). He runs right, jumps over a block and a coin row (rel ≈3.5, ≈4.2), hops up a staircase of ice steps (rel ≈5.0–7.5), and jumps or falls off the top (rel ≈7.7–8.5). He then walks right on flat ground toward the igloo and **slows down and stops/enters the igloo** (rel ≈9–13). The level-complete stats panel overlays the screen from rel ≈9.5.
- **First-frame bbox:** **(515, 253, 38, 38)**, on a wooden bridge. Two billboards with Tux pictograms are visible to the right.
- **Problems:** the billboards and the stats overlay are distractors. The camera scrolls vertically during the drop.

---

## 2. supertux_ff — SuperTux, "The Frosted Fields" speedrun

- **File page:** https://commons.wikimedia.org/wiki/File:SuperTux_Frosted_Fields_speedrun.webm
- **Direct URL:** https://upload.wikimedia.org/wikipedia/commons/d/d2/SuperTux_Frosted_Fields_speedrun.webm
- **Author:** SuperTux Development Team; video by Dexxor (Commons user, "own work").
- **License:** GPLv3 (http://www.gnu.org/licenses/gpl-3.0.html)
- **Original file:** `SuperTux_Frosted_Fields_speedrun.webm` (VP9, 1600×900, 30 fps CFR, 57.0 s, 18.6 MB). The first ~1.5 s is the level title card.
- **Camera:** horizontal scroll following Tux, with some vertical scroll. Flat-colour parallax background.
- **HUD/overlays:** an **input display** at top-left (~45–235, 15–105) shows the keys pressed. This could serve as a ground-truth input signal, but it would need decoding. There is a coin counter top-right and an in-game **FPS counter** reading "min 11.0 / avg 48.9 / max 403.7".

### supertux_ff_seg01.mp4 — abs 1.6–11.6 s (300 frames)
- **Action:** Tux idles at the start, then sprints right (speedrun style, near max speed). He makes several running jumps over a bump and enemies (rel ≈1.5–2.2, ≈2.6–3.3, ≈3.8–4.3), runs past pipes, jumps at ≈5.5–6.0, and runs on to the end of the segment.
- **Character:** small Tux, ~46 px tall.
- **First-frame bbox:** **(132, 518, 46, 46)**.
- **Problems:** the in-game FPS counter shows the **game render rate varied (min 11 fps)**, so some captured frames may repeat game states or skip them. Avoid relying on per-frame accelerations without robust fitting. A grey spiky enemy jumps around near Tux (a distractor). Speedrun inputs mean almost no stopping.

---

## 3. smc — Secret Maryo Chronicles

- **File page:** https://commons.wikimedia.org/wiki/File:Secret_Maryo_Chronicles.ogv
- **Direct URL:** https://upload.wikimedia.org/wikipedia/commons/7/7b/Secret_Maryo_Chronicles.ogv
- **Author:** Secret Maryo Team (game). Recording uploaded by UploadBunny, 2010, "recorded with RecordMyDesktop (sadly without sound)".
- **License:** GPL (http://www.gnu.org/licenses/gpl.html)
- **Original file:** `Secret_Maryo_Chronicles.ogv` (Theora, 960×608, **15 fps**, 130.7 s, 31.5 MB). Content: menu, world map, level 1 (≈5–75 s), a cave sub-area (≈77–90 s), level 2 start (≈95–128 s), menu.
- **Camera:** horizontal scroll following Maryo. Big parallax "hill with eyes" background in level 1 and rolling-hill parallax in level 2.
- **HUD:** text across the top ("Punkte", coin counter, item box, "Zeit", lives), y < 50.
- **Problems:** **only 15 fps**: a jump arc gets roughly 8–12 samples. RecordMyDesktop may have captured game frames irregularly, although the container PTS are uniform (0.0667 s). There is Theora compression blur. A 1–4 px dark border runs along the top edge of the frame.

### smc_seg01.mp4 — abs 6.0–15.0 s (135 frames)
- **Action:** Maryo stands on a green pipe, steps or jumps off (rel ≈0.3), and runs right. He makes multiple jumps of varying height (rel ≈2.7, ≈3.3, ≈4.0–5.3 hitting bonus blocks, ≈6.3, ≈7.3 hitting a ? block). A mushroom pops out at rel ≈8. The power-up/growth happens at abs ≈15.3, just after the segment ends.
- **Character:** small Maryo with red bandana cap, brown hair and red/white clothes, ~42×56 px.
- **First-frame bbox:** **(155, 352, 42, 56)**, standing on top of the pipe.

### smc_seg02.mp4 — abs 96.0–105.0 s (135 frames)
- **Action:** level 2 start. Maryo (big form) runs right, jumps onto a floating brick row (rel ≈2.0), runs along it collecting coins and drops off the end (rel ≈3.5). He runs on the ground, makes a jump at rel ≈5.7, jumps to a ? block at rel ≈6.7–7.3, and keeps running with an enemy nearby.
- **Character:** big Maryo, ~60×64 px.
- **First-frame bbox:** **(110, 476, 60, 64)**, running on the ground (ground line y≈540).

---

## 4. flinthook — Flinthook early gameplay (Tribute Games)

- **File page:** https://commons.wikimedia.org/wiki/File:Flinthook_early_gameplay.webm
- **Direct URL:** https://upload.wikimedia.org/wikipedia/commons/2/2c/Flinthook_early_gameplay.webm
- **Author:** Tribute Games (official video, 2016). Commons credit: YouTube "FLINTHOOK Early Gameplay!", imported to Commons by user Czar under the CC license. We downloaded the copy hosted on Commons, not from YouTube.
- **License:** CC BY-SA 3.0 (https://creativecommons.org/licenses/by-sa/3.0). Attribution: "Tribute Games".
- **Original file:** `Flinthook_early_gameplay.webm` (VP9, 1920×1080, 30 fps CFR, 269.0 s, 48.9 MB). Content: a single-screen room per encounter, with room transitions (anchor wipe) at ≈11–12 s, ≈20 s, ≈2:04, ≈3:20–3:24 and outro from 4:08.
- **Camera:** **fixed per room**. There are small camera shifts and shake: the frame moves by a few pixels, for example the room border is ~10 px lower in the last frame than in the first.
- **Gameplay caveat:** Flinthook's movement is dominated by **hookshot swings** (grappling rings), blasters and a slow-mo ability. Pure run/jump arcs are short. Combat starts quickly, with many skull enemies, bullets and flashes.

### flinthook_seg01.mp4 — abs 7.3–14.2 s (207 frames)
- **Action:** the first room. The player arrives hanging from the top rope (rel 0–1), swings and hooks between rings, falls and lands on platforms, fights a few small skull ghosts (rel ≈1–5), and leaves through the right-side door (rel ≈6.5). The segment ends before the room-transition wipe.
- **Character:** small space pirate in a white hood/helmet and purple outfit, ~40–55 px.
- **First-frame bbox:** **(935, 180, 55, 85)**, hanging from the rope at top-centre and partly occluded by the top border. In the last frame he is at the right door, about (1575, 705, 70, 60).
- **Problems:** hookshot physics (not a pure platformer controller), enemies with similar white colouring, muzzle flashes, slight camera shake. A HUD element sits in the bottom-right corner (~1745–1845, 840–940).

---

## 5. guacamelee — Guacamelee! Super Turbo Championship Edition B-roll (DrinkBox Studios)

- **File page:** https://commons.wikimedia.org/wiki/File:Guacamelee!_STCE_B-roll_gameplay.webm
- **Direct URL:** https://upload.wikimedia.org/wikipedia/commons/9/9c/Guacamelee%21_STCE_B-roll_gameplay.webm
- **Author:** DrinkBox Studios. Commons credit: "Chris McQuinn of DrinkBox, via email" (licence granted by the developer).
- **License:** CC BY-SA 3.0 (https://creativecommons.org/licenses/by-sa/3.0)
- **Original file:** saved locally as `Guacamelee_STCE_B-roll_gameplay.webm`; the Commons name is `Guacamelee!_STCE_B-roll_gameplay.webm`. VP8, 1280×720, 60 fps CFR, 61.0 s, 19.3 MB.
- **Content:** a B-roll montage with hard cuts every ~5–15 s. Most clips are brawler combat (combo counters, many enemies). Only one clip is mostly traversal.
- **Camera:** follows the player, mostly vertically in the chosen clip. Dark vignette at the screen edges.

### guacamelee_seg01.mp4 — abs 12.0–20.5 s (510 frames)
- **Action:** Juan (luchador) runs and jumps at the bottom of a shaft (rel 0–0.5). He **switches dimension** (rel ≈0.75–2.5 and ≈6–8.5), during which the sprite becomes a black/white silhouette. He climbs a narrow vertical shaft with **wall jumps** (rel ≈2.5–5.8; the camera scrolls upward), then exits along a horizontal corridor at the top and jumps across it (rel ≈6–8.5).
- **Character:** Juan, a blue luchador suit with red sash and mask, ~85×92 px when running. In the other dimension he is a black silhouette.
- **First-frame bbox:** **(420, 578, 85, 92)**, running at bottom-left of the shaft.
- **Problems:** the **appearance changes completely** on dimension swaps, so template trackers will fail. Wall-jump physics. Vertical camera scroll. HUD at top-left (health/stamina, ~5–260, 0–50). Black letterbox band at the top. Background art also changes on swaps. Treat this as a stress test, not a primary fitting clip.

---

## 6. tobutobugirl — Tobu Tobu Girl (Game Boy homebrew) TAS

- **File page:** https://commons.wikimedia.org/wiki/File:-TAS-_GB_Tobu_Tobu_Girl_%22All_Levels%22_by_Eltrion_%26_ThunderAxe31_in_02-42.92_(unofficial_encode).webm
- **Direct URL:** https://upload.wikimedia.org/wikipedia/commons/a/a4/-TAS-_GB_Tobu_Tobu_Girl_%22All_Levels%22_by_Eltrion_%26_ThunderAxe31_in_02-42.92_%28unofficial_encode%29.webm
- **Author:** TAS by Eltrion and ThunderAxe31. Game by Simon Larsen/Tangram Games, music by potato-tan. The Commons uploader re-rendered it with BizHawk 2.3.1 (https://tasvideos.org/6309S, https://tasvideos.org/3931M).
- **License:** Public domain (Commons tag "Public domain"; a TAS input movie rendered from the game). The game itself is open source (https://tangramgames.itch.io/tobutobugirl).
- **Original file:** saved locally as `Tobu_Tobu_Girl_TAS_All_Levels.webm` (renamed for shell-safety). VP9, 1600×1440, 60 fps CFR, 227.5 s, 15.7 MB. Each Game Boy pixel is a 10×10 block.
- **Content:** a vertical "jump-up" platformer. The girl bounces off enemies to climb upward and steers horizontally with momentum. There are 4 levels (≈20–34, ≈44–60, ≈72–92, ≈106–138 s), then results screens and a long high-score screen.
- **Camera:** **vertical auto-follow**: the player stays at roughly mid-height while the background scrolls. Horizontal position is screen-fixed, with a 160 px-wide (1/10 scale) playfield and a right-side HUD column (x ≳ 1440).

### tobutobugirl_seg01.mp4 — abs 45.0–56.5 s (690 frames)
- **Action:** level 2 ("Clouds"). She spawns at rel ≈0, then performs continuous bounce jumps off birds and bats with horizontal drift and direction changes. There are several mid-air dashes. The TAS makes inputs frame-perfect and non-human.
- **Character:** a girl with a dark hat with a little flag, white face and dark dress; 4-tone greyscale; ~160×160 px at this scale (16×16 GB pixels).
- **First-frame bbox:** **(716, 676, 160, 160)**, at her spawn point (a large circular sprite with her inside).
- **Problems:** vertical motion is mostly converted into camera scroll, so you must compensate against the cloud background, which has 2–3 parallax-like bands. Many similar-sized enemy sprites. Greyscale only. Bounce physics rather than ground-based jumps; no running/stopping on ground. The GB runs at ~59.73 Hz and the encode at 60 fps, so roughly one game frame in ~370 is duplicated (mpdecimate flagged 15/690 frames as near-duplicates).

---

## Rejected candidates

| Candidate | URL | License | Reason |
|---|---|---|---|
| FEZ trial gameplay HD (Polytron) | https://commons.wikimedia.org/wiki/File:FEZ_trial_gameplay_HD.webm | CC BY-SA 3.0 | Downloaded (`fez/FEZ_trial_gameplay_HD.webm`, 1280×720, 30 fps, 390 s, 144 MB) but **not segmented**. Gomez is tiny (~15–20 px at 720p). There are frequent 2D↔rotation perspective turns, long tutorial/dialogue sections and swimming, and the camera zooms and pans vertically. Kept on disk only for reference; it can be deleted. |
| Gameplay of SuperTux (8 Minutes) | https://commons.wikimedia.org/wiki/File:Gameplay_of_SuperTux_(8_Minutes).webm | (not checked) | 309 MB at 1706×960. Two other SuperTux clips already cover this game. |
| SuperTux level 1 all coins run | https://commons.wikimedia.org/wiki/File:SuperTux_level_1_all_coins_run.webm | GPL | Same level, recorder and style as `supertux_l1`, so redundant. A good backup if more SuperTux data is wanted. |
| SuperTux 0.4.0 Ice in the Hole speedrun | https://commons.wikimedia.org/wiki/File:SuperTux_0.4.0_Ice_in_the_Hole_speedrun.webm | CC BY 3.0 | Not downloaded (third SuperTux clip; a backup option with an older engine version). |
| Togges gameplay clips | https://commons.wikimedia.org/wiki/File:Togges_Gameplay_Clips_(2018).webm | CC BY 3.0 | 3D platformer. |
| Junk Runner 64 | https://commons.wikimedia.org/wiki/File:Junk_Runner_64_gameplay.webm | MIT | N64 3D game, 624×480. |
| Push Me Pull You, Hedgewars, Mini Metro, etc. | Commons "Videos of video game gameplay" | various | Not side-view single-character platformers. |
| Frogatto, Mari0, Open Surge | – | – | No gameplay videos found on Commons (search returned nothing). |
| Misc. GitHub student platformer repos | – | – | Not pursued. The media licences are unclear and the quality is low compared with the Commons material. |

## Fallback (step 5)
Not needed: there are 6 usable videos from 5 different games (SuperTux, Secret Maryo Chronicles, Flinthook, Guacamelee!, Tobu Tobu Girl). No game binaries were downloaded.
