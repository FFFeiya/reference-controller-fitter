"""Camera (background) motion estimation for 2D side-scrolling footage.

Assumption: the gameplay layer is a static world viewed through a translating camera.
Per frame pair we track sparse background features with pyramidal Lucas-Kanade
(forward-backward checked), then pick the dominant *translation mode* with a weighted
mean-shift vote. Parallax layers show up as separate modes; votes are weighted towards
features near the player's height (the gameplay layer the player stands on), and
features in static screen regions (HUD) are excluded when the camera moves.
Phase correlation is used as a fallback when too few features survive.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class ShiftEstimate:
    dx: float          # background feature displacement (pixels) from prev to cur
    dy: float
    confidence: float  # 0..1
    inliers: int
    method: str
    second_mode: tuple[float, float] | None = None


@dataclass
class CameraTrack:
    cam_x: np.ndarray   # camera position in pixels (relative to the first frame)
    cam_y: np.ndarray
    confidence: np.ndarray
    method: list[str] = field(default_factory=list)

    @property
    def overall_confidence(self) -> float:
        if len(self.confidence) < 2:
            return 1.0
        c = self.confidence[1:]
        return float(np.clip(0.5 * np.median(c) + 0.5 * np.percentile(c, 10), 0, 1))

    @property
    def level(self) -> str:
        c = self.overall_confidence
        return "HIGH" if c >= 0.6 else ("MEDIUM" if c >= 0.35 else "LOW")

    @property
    def moved(self) -> bool:
        return bool(np.ptp(self.cam_x) > 2 or np.ptp(self.cam_y) > 2)


def static_mask(gray_samples: list[np.ndarray], thresh: float = 2.5) -> np.ndarray | None:
    """Mask (255 = usable) excluding pixels that never change over the clip (HUD/overlays).

    Returns None when most of the frame is static (camera never moves) – then nothing
    should be excluded.
    """
    if len(gray_samples) < 4:
        return None
    stack = np.stack([g.astype(np.float32) for g in gray_samples])
    std = stack.std(axis=0)
    static = (std < thresh).astype(np.uint8)
    static = cv2.morphologyEx(static, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    frac = static.mean()
    if frac > 0.6:
        return None
    usable = (1 - static) * 255
    return cv2.erode(usable.astype(np.uint8), np.ones((5, 5), np.uint8))


def _mode_shift(d: np.ndarray, w: np.ndarray, radius: float = 1.0):
    """Weighted mean-shift mode of 2D displacements. Returns (mode, inlier mask)."""
    if len(d) == 0:
        return None, None
    # start from the best of a coarse vote
    best, best_score = None, -1.0
    for c in d[:: max(1, len(d) // 60)]:
        m = np.linalg.norm(d - c, axis=1) < radius
        s = w[m].sum()
        if s > best_score:
            best, best_score = c.copy(), s
    c = best
    for _ in range(10):
        m = np.linalg.norm(d - c, axis=1) < radius
        if not m.any():
            break
        nc = (d[m] * w[m, None]).sum(0) / w[m].sum()
        if np.linalg.norm(nc - c) < 1e-3:
            c = nc
            break
        c = nc
    m = np.linalg.norm(d - c, axis=1) < radius
    return c, m


def estimate_shift(prev: np.ndarray, cur: np.ndarray, mask: np.ndarray | None,
                   focus_y: float | None = None) -> ShiftEstimate:
    h, w = prev.shape[:2]
    pts = cv2.goodFeaturesToTrack(prev, maxCorners=500, qualityLevel=0.005, minDistance=6, mask=mask)
    if pts is not None and len(pts) >= 8:
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, winSize=(21, 21), maxLevel=3)
        back, st2, _ = cv2.calcOpticalFlowPyrLK(cur, prev, nxt, None, winSize=(21, 21), maxLevel=3)
        fb = np.linalg.norm((back - pts).reshape(-1, 2), axis=1)
        ok = (st.ravel() == 1) & (st2.ravel() == 1) & (fb < 0.5)
        p0 = pts.reshape(-1, 2)[ok]
        d = (nxt - pts).reshape(-1, 2)[ok]
        if len(d) >= 6:
            wts = np.ones(len(d))
            if focus_y is not None:
                wts += 2.0 * np.exp(-((p0[:, 1] - focus_y) / (0.25 * h)) ** 2)
            mode, inl = _mode_shift(d, wts)
            n_in = int(inl.sum())
            frac = wts[inl].sum() / wts.sum()
            # second mode (e.g. a parallax layer)
            rest = ~inl
            second = None
            if rest.sum() >= 6:
                m2, inl2 = _mode_shift(d[rest], wts[rest])
                if inl2.sum() >= max(6, 0.08 * len(d)):
                    second = (float(m2[0]), float(m2[1]))
                    # Parallax: distant layers scroll slower than the gameplay layer. If the
                    # other layer moves clearly faster AND its features sit closer to the
                    # player's feet, it is the gameplay layer -> use it.
                    if focus_y is not None and np.linalg.norm(m2) > 1.15 * np.linalg.norm(mode) + 0.5:
                        idx_rest = np.where(rest)[0][inl2]
                        y2 = np.median(p0[idx_rest, 1]); y1 = np.median(p0[inl, 1])
                        if abs(y2 - focus_y) < abs(y1 - focus_y):
                            second = (float(mode[0]), float(mode[1]))
                            mode = m2
                            inl = np.zeros(len(d), bool); inl[idx_rest] = True
                            n_in = int(inl.sum())
                            frac = max(frac, 0.5)
            conf = float(np.clip(frac * min(1.0, n_in / 25.0), 0, 1))
            if n_in >= 6:
                return ShiftEstimate(float(mode[0]), float(mode[1]), conf, n_in, "lk-mode", second)
    # fallback: phase correlation over the masked frame
    a = prev.astype(np.float32); b = cur.astype(np.float32)
    if mask is not None:
        mm = (mask > 0).astype(np.float32)
        a = a * mm + a[mm > 0].mean() * (1 - mm) if mm.any() else a
        b = b * mm + b[mm > 0].mean() * (1 - mm) if mm.any() else b
    win = cv2.createHanningWindow((w, h), cv2.CV_32F)
    (sx, sy), resp = cv2.phaseCorrelate(a, b, win)
    return ShiftEstimate(float(sx), float(sy), float(np.clip(resp * 2, 0, 0.5)), 0, "phase")


def estimate_shift_layered(prev: np.ndarray, cur: np.ndarray, mask: np.ndarray | None,
                           focus_y: float | None) -> ShiftEstimate:
    """Full-frame estimate cross-checked with the band *below the player's feet*.

    The ground the player stands on belongs to the gameplay layer by definition, while the
    rest of the frame is often dominated by slower parallax layers (hills, clouds). If the
    ground band gives a confident estimate that disagrees with the full-frame one, the
    ground band wins."""
    full = estimate_shift(prev, cur, mask, focus_y)
    if focus_y is None:
        return full
    h, w = prev.shape[:2]
    top = int(np.clip(focus_y - 0.06 * h, 0, h - 8))
    band = np.zeros((h, w), np.uint8)
    band[top:, :] = 255
    if mask is not None:
        band = cv2.bitwise_and(band, mask)
    if band.mean() < 255 * 0.02:
        return full
    g = estimate_shift(prev, cur, band, None)
    if g.method == "phase" or g.inliers < 8 or g.confidence < 0.35:
        return full
    if abs(g.dx - full.dx) > 2.0 or abs(g.dy - full.dy) > 2.0:
        g.second_mode = (full.dx, full.dy)
        g.method = "lk-ground"
        return g
    return full


def player_exclusion_mask(shape, bbox, base: np.ndarray | None, margin: float = 0.6) -> np.ndarray:
    h, w = shape[:2]
    m = np.full((h, w), 255, np.uint8) if base is None else base.copy()
    if bbox is not None:
        x, y, bw, bh = bbox
        mx, my = bw * margin + 4, bh * margin + 4
        x0 = int(max(0, x - mx)); y0 = int(max(0, y - my))
        x1 = int(min(w, x + bw + mx)); y1 = int(min(h, y + bh + my))
        m[y0:y1, x0:x1] = 0
    return m


class CameraEstimator:
    """Online estimator: call update(gray, player_bbox_prev) for each frame in order.

    Shifts are measured against a *keyframe* rather than the previous frame, so sub-pixel
    errors do not accumulate into drift while the camera is still or moving slowly. A new
    keyframe is taken when the overlap gets small or the match gets weak.
    """

    def __init__(self, static: np.ndarray | None = None, roi: tuple[int, int, int, int] | None = None,
                 key_max_px: float = 16.0):
        self.static = static
        self.roi = roi       # optional user-chosen background region (x, y, w, h)
        self.prev = None
        self.key = None
        self.key_cam = (0.0, 0.0)
        self.key_max_px = key_max_px
        self.xs, self.ys, self.conf, self.methods = [0.0], [0.0], [1.0], ["start"]
        self.cx = 0.0
        self.cy = 0.0

    def _mask(self, gray, player_bbox):
        base = self.static
        if self.roi is not None:
            base = np.zeros(gray.shape[:2], np.uint8)
            x, y, w, h = self.roi
            base[y:y + h, x:x + w] = 255
            if self.static is not None:
                base = cv2.bitwise_and(base, self.static)
        return player_exclusion_mask(gray.shape, player_bbox, base)

    def update(self, gray: np.ndarray, player_bbox) -> ShiftEstimate | None:
        if self.prev is None:
            self.prev = gray
            self.key = gray
            self.key_mask_box = player_bbox
            return None
        mask = self._mask(gray, player_bbox)
        focus = None if player_bbox is None else player_bbox[1] + player_bbox[3]
        # keyframe mask must also exclude the player position at the keyframe time
        if self.key_mask_box is not None:
            mask = player_exclusion_mask(gray.shape, self.key_mask_box, mask)
        # frame-to-frame estimate (robust for any speed) ...
        f2f = estimate_shift_layered(self.prev, gray, self._mask(gray, player_bbox), focus)
        pred = (self.cx - f2f.dx, self.cy - f2f.dy)
        # ... refined against the keyframe when the keyframe is still close (no drift)
        kdx = self.key_cam[0] - pred[0]
        kdy = self.key_cam[1] - pred[1]   # expected keyframe->current feature shift
        est = f2f
        use_key = abs(kdx) < self.key_max_px and abs(kdy) < self.key_max_px
        if use_key:
            ke = estimate_shift_layered(self.key, gray, mask, focus)
            if ke.method != "phase" and ke.confidence >= 0.3 and abs(ke.dx - kdx) < 2.0 and abs(ke.dy - kdy) < 2.0:
                est = ke
                self.cx = self.key_cam[0] - ke.dx
                self.cy = self.key_cam[1] - ke.dy
            else:
                use_key = False
        if not use_key:
            self.cx, self.cy = pred
            self.key = gray
            self.key_cam = (self.cx, self.cy)
            self.key_mask_box = player_bbox
        self.xs.append(self.cx); self.ys.append(self.cy)
        self.conf.append(est.confidence); self.methods.append(est.method)
        self.prev = gray
        return est

    def result(self, despike: bool = True) -> CameraTrack:
        xs, ys = np.array(self.xs), np.array(self.ys)
        if despike and len(xs) >= 4:
            # A single wrong frame-to-frame step leaves a permanent offset; a 3-tap median on the
            # steps removes isolated outlier steps while keeping real starts/stops of scrolling.
            xs, ys = _despike(xs), _despike(ys)
        return CameraTrack(xs, ys, np.array(self.conf), list(self.methods))


def _despike(p: np.ndarray, tol: float = 3.0) -> np.ndarray:
    d = np.diff(p)
    pad = np.concatenate([[d[0]], d, [d[-1]]])
    med = np.median(np.stack([pad[:-2], pad[1:-1], pad[2:]]), axis=0)
    out = d.copy()
    bad = np.abs(d - med) > tol
    out[bad] = med[bad]
    return np.concatenate([[p[0]], p[0] + np.cumsum(out)])
