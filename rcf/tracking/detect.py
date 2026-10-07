"""Automatic player detection.

In a side-scroller the player is the object that is *not* part of the static world.
We estimate the camera shift between frames, build a camera-aligned median background
mosaic (moving objects are removed by the median), and subtract it from each frame to
get full silhouettes (unlike frame differencing, which only sees the edges of flat
coloured sprites). Foreground blobs are linked over time into chains; the longest
consistent, character-sized chain that is not screen-static (HUD) wins. The result is
localised in the clip's first frame (template match, also mirrored) and carries a
confidence; the UI asks the user to draw the box when it is low.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .camera import estimate_shift, static_mask
from .video import iter_frames, probe


@dataclass
class Detection:
    bbox: tuple[float, float, float, float] | None
    confidence: float
    frame: int
    message: str


def detect_player(video, start: int = 0, end: int | None = None, max_frames: int = 120,
                  work_height: int = 480) -> Detection:
    info = probe(video)
    stop = start + max_frames
    if info.frame_count:
        stop = min(stop, info.frame_count)
    if end is not None:
        stop = min(stop, end)
    frames = [f for _, f in iter_frames(video, start, stop)]
    if len(frames) < 6:
        return Detection(None, 0.0, start, "Clip too short for automatic detection.")
    H0, W0 = frames[0].shape[:2]
    sc = min(1.0, work_height / H0)
    small = [cv2.resize(f, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA) if sc < 1 else f for f in frames]
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in small]
    H, W = grays[0].shape
    hud = static_mask(grays[:: max(1, len(grays) // 20)])
    off = [(0.0, 0.0)]
    for a, b in zip(grays[:-1], grays[1:]):
        e = estimate_shift(a, b, hud)
        off.append((off[-1][0] + e.dx, off[-1][1] + e.dy))
    off = np.array(off)
    # canvas coordinates: p_canvas = p_screen - off[k] - (min over k of -off)
    neg = -np.round(off).astype(int)
    ox, oy = neg[:, 0].min(), neg[:, 1].min()
    cw = W + neg[:, 0].max() - ox
    chh = H + neg[:, 1].max() - oy
    idx = list(range(0, len(grays), 2))
    stack = np.full((len(idx), chh, cw, 3), np.nan, np.float32)
    for si, k in enumerate(idx):
        X, Y = neg[k, 0] - ox, neg[k, 1] - oy
        stack[si, Y:Y + H, X:X + W] = small[k]
    bg = np.nanmedian(stack, axis=0)
    del stack
    chains: list[list[tuple[int, tuple[int, int, int, int], float]]] = []
    for k in idx:
        X, Y = neg[k, 0] - ox, neg[k, 1] - oy
        b = bg[Y:Y + H, X:X + W]
        d = np.abs(small[k].astype(np.float32) - np.nan_to_num(b, nan=0)).max(axis=2)
        m = (d > 40).astype(np.uint8)
        if hud is not None:
            m[cv2.dilate(255 - hud, np.ones((7, 7), np.uint8)) > 0] = 0
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
        blobs = []
        for i in range(1, n):
            x, y, w, h, area = st[i]
            if area < 40 or h < 0.03 * H or h > 0.45 * H or w > 0.35 * W or w < 4:
                continue
            blobs.append(((x, y, w, h), area / float(w * h)))
        # link to chains (screen-space continuity, similar size)
        for box, fill in blobs:
            cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
            best, bd = None, 1e9
            for ch in chains:
                kk, pb, _ = ch[-1]
                if kk >= k or k - kk > 6:
                    continue
                pcx, pcy = pb[0] + pb[2] / 2, pb[1] + pb[3] / 2
                dist = np.hypot(cx - pcx, cy - pcy)
                ratio = (box[2] * box[3]) / max(1, pb[2] * pb[3])
                if dist < 0.12 * W and 0.4 < ratio < 2.5 and dist < bd:
                    best, bd = ch, dist
            if best is not None:
                best.append((k, box, fill))
            else:
                chains.append([(k, box, fill)])
    if not chains:
        return Detection(None, 0.0, start, "No moving object found – draw the character box manually.")
    camera_moved = np.ptp(off[:, 0]) > 4 or np.ptp(off[:, 1]) > 4

    def world_vel(ch):
        if len(ch) < 3:
            return None
        ks = np.array([c[0] for c in ch], float)
        cx = np.array([b[0] + b[2] / 2 for _, b, _ in ch]) - off[ks.astype(int), 0]
        return float(np.polyfit(ks, cx, 1)[0])

    vels = [world_vel(ch) for ch in chains]

    def layer_like(i):
        """Chain moves exactly like several other chains -> a parallax layer, not the player."""
        v = vels[i]
        if v is None or abs(v) < 0.3:
            return False
        same = sum(1 for j, u in enumerate(vels) if j != i and u is not None and abs(u - v) < 0.15 * abs(v) + 0.2
                   and len(chains[j]) >= 3)
        return same >= 2

    layer = {id(ch): layer_like(i) for i, ch in enumerate(chains)}

    def score(ch):
        L = len(ch)
        fill = np.mean([c[2] for c in ch])
        centers = np.array([[b[0] + b[2] / 2, b[1] + b[3] / 2] for _, b, _ in ch])
        wc = centers - off[[c[0] for c in ch]]  # world positions
        screen_static = len(ch) > 3 and centers.std(axis=0).max() < 1.0 and camera_moved
        world_static = len(ch) > 3 and wc.std(axis=0).max() < 1.0
        s = L * (0.5 + fill)
        if screen_static:
            s *= 0.1   # HUD element
        if world_static:
            s *= 0.3   # static scenery mis-registered
        top = np.mean([b[1] for _, b, _ in ch]) < 0.12 * H
        if top:
            s *= 0.5
        if layer[id(ch)]:
            s *= 0.15
        return s
    chains.sort(key=score, reverse=True)
    best = chains[0]
    total = len(idx)
    # a typical sample: median height (avoids merged ghost blobs), then best fill
    med_h = np.median([b[3] for _, b, _ in best])
    k, (x, y, w, h), _ = min(best, key=lambda c: (abs(c[1][3] - med_h) / med_h > 0.15, -c[2]))
    tpl = small[k][y:y + h, x:x + w]
    g0 = small[0]
    bm = (-1.0, None)
    for t in (tpl, cv2.flip(tpl, 1)):
        if t.shape[0] >= g0.shape[0] or t.shape[1] >= g0.shape[1]:
            continue
        r = cv2.matchTemplate(g0, t, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(r)
        if mv > bm[0]:
            bm = (mv, ml)
    if bm[1] is None:
        return Detection(None, 0.0, start, "Detection failed – draw the character box manually.")
    coverage = len(best) / total
    conf = float(np.clip(bm[0], 0, 1) * min(1.0, 0.3 + coverage))
    bx, by = bm[1]
    box = (bx / sc, by / sc, w / sc, h / sc)
    # The score is a heuristic, not a calibrated probability: always ask for verification.
    conf = min(conf, 0.75)
    msg = ("Automatic detection – please verify the box (redraw it if it is not the player)"
           if conf >= 0.5 else "Automatic detection is unsure – please draw the box around the player")
    return Detection(tuple(float(v) for v in box), conf, start, msg)
