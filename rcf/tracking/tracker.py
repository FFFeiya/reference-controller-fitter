"""Player tracking: several interchangeable trackers + colour-segmentation bbox refinement.

All trackers share the interface ``init(frame, bbox)`` / ``update(frame, hint) -> (bbox, score)``
where ``hint`` is a predicted bbox (previous box moved by the camera shift and the
recent velocity). The evaluation in ``tools/eval_trackers.py`` compares them on the
synthetic benchmark (ground-truth boxes) – see DEVLOG/RESEARCH for the numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

BBox = tuple[float, float, float, float]  # x, y, w, h (pixels, top-left origin)

# "template" is the default: best accuracy on the synthetic benchmark (see DEVLOG / RESEARCH).
TRACKER_METHODS = ["template", "csrt", "kcf", "mil", "flow", "hybrid"]


def _clip_box(b, shape) -> tuple[int, int, int, int]:
    h, w = shape[:2]
    x, y, bw, bh = b
    x0 = int(round(max(0, min(w - 2, x)))); y0 = int(round(max(0, min(h - 2, y))))
    x1 = int(round(max(x0 + 2, min(w, x + bw)))); y1 = int(round(max(y0 + 2, min(h, y + bh))))
    return x0, y0, x1 - x0, y1 - y0


class BaseTracker:
    name = "base"

    def init(self, frame: np.ndarray, bbox: BBox) -> None:
        raise NotImplementedError

    def update(self, frame: np.ndarray, hint: BBox) -> tuple[BBox | None, float]:
        raise NotImplementedError


class OpenCVTracker(BaseTracker):
    def __init__(self, kind: str):
        self.name = kind
        self.kind = kind

    def init(self, frame, bbox):
        if self.kind == "csrt":
            self.t = cv2.TrackerCSRT_create()
        elif self.kind == "kcf":
            self.t = cv2.TrackerKCF_create()
        elif self.kind == "mil":
            self.t = cv2.TrackerMIL_create()
        else:
            raise ValueError(self.kind)
        self.t.init(frame, tuple(int(v) for v in _clip_box(bbox, frame.shape)))

    def update(self, frame, hint):
        ok, b = self.t.update(frame)
        return (tuple(float(v) for v in b), 1.0) if ok else (None, 0.0)


class TemplateTracker(BaseTracker):
    """NCC template matching in a window around the prediction, slow template update.

    With ``scale_search`` the template is also matched at several *vertical* scales so the
    box follows squash & stretch animation and the feet (box bottom) stay accurate. The
    mirrored template is tried too (sprites flip when the character turns around).
    """
    name = "template"

    def __init__(self, search: float = 1.5, update_rate: float = 0.05, scale_search: bool = True,
                 mirror: bool = True, bank_size: int = 12):
        self.search = search
        self.rate = update_rate
        self.scales = np.exp(np.linspace(np.log(0.8), np.log(1.25), 11)) if scale_search else np.array([1.0])
        self.mirror = mirror
        self.bank_size = bank_size if update_rate > 0 else 0

    def init(self, frame, bbox):
        x, y, w, h = _clip_box(bbox, frame.shape)
        self.w, self.h = w, h
        self.t0 = frame[y:y + h, x:x + w].astype(np.float32)
        self.t = self.t0.copy()
        self.cur_scale = 1.0
        # appearance bank: distinct animation frames seen with high confidence
        self.bank: list[np.ndarray] = []

    def _templates(self):
        out = [(self.t, 1.0), (self.t0, 1.0)]
        if self.mirror:
            out.append((cv2.flip(self.t0, 1), 1.0))
        out += [(b, 1.0) for b in self.bank]
        return out

    def _maybe_bank(self, patch: np.ndarray, score: float):
        """Add a new appearance (animation frame) if it differs from everything known."""
        if self.bank_size <= 0 or score < 0.55:
            return
        known = [self.t0] + self.bank
        sims = [float(cv2.matchTemplate(patch, k, cv2.TM_CCOEFF_NORMED).max()) for k in known]
        if max(sims) < 0.85:
            self.bank.append(patch.copy())
            if self.mirror:
                self.bank.append(cv2.flip(patch, 1))
            if len(self.bank) > self.bank_size:
                del self.bank[:2]

    def update(self, frame, hint):
        H, W = frame.shape[:2]
        cx = hint[0] + hint[2] / 2; cy = hint[1] + hint[3] / 2
        sw = self.w * (1 + 2 * self.search); sh = self.h * (1.25 + 2 * self.search)
        x0 = int(max(0, cx - sw / 2)); y0 = int(max(0, cy - sh / 2))
        x1 = int(min(W, cx + sw / 2)); y1 = int(min(H, cy + sh / 2))
        win = frame[y0:y1, x0:x1].astype(np.float32)
        if win.shape[0] < self.h * 1.3 or win.shape[1] < self.w:
            return None, 0.0
        best, loc, best_s, res = -2.0, None, 1.0, None
        # coarse: scale 1 with all templates; then vertical scales with the winning template
        best_t = None
        for tpl, _ in self._templates():
            r = cv2.matchTemplate(win, tpl, cv2.TM_CCOEFF_NORMED)
            _, mv, _, ml = cv2.minMaxLoc(r)
            if mv > best:
                best, loc, res, best_t = mv, ml, r, tpl
        if len(self.scales) > 1:
            for s in self.scales:
                if abs(s - 1.0) < 1e-6:
                    continue
                hh = int(round(self.h * s))
                if hh < 4 or hh >= win.shape[0]:
                    continue
                tpl = cv2.resize(best_t, (self.w, hh), interpolation=cv2.INTER_LINEAR)
                r = cv2.matchTemplate(win, tpl, cv2.TM_CCOEFF_NORMED)
                _, mv, _, ml = cv2.minMaxLoc(r)
                # tiny preference for the unscaled template (avoids jitter on rigid sprites)
                if mv - 0.004 * abs(np.log(s)) / np.log(1.25) > best:
                    best, loc, res, best_s = mv, ml, r, s
        # sub-pixel peak refinement (parabola fit)
        px, py = float(loc[0]), float(loc[1])
        if 0 < loc[0] < res.shape[1] - 1:
            l, c, r_ = res[loc[1], loc[0] - 1], res[loc[1], loc[0]], res[loc[1], loc[0] + 1]
            den = l - 2 * c + r_
            if abs(den) > 1e-6:
                px += 0.5 * (l - r_) / den
        if 0 < loc[1] < res.shape[0] - 1:
            u, c, d = res[loc[1] - 1, loc[0]], res[loc[1], loc[0]], res[loc[1] + 1, loc[0]]
            den = u - 2 * c + d
            if abs(den) > 1e-6:
                py += 0.5 * (u - d) / den
        bx, by = x0 + px, y0 + py
        hh = int(round(self.h * best_s))
        if best > 0.7 and self.rate > 0:
            xi, yi = int(round(bx)), int(round(by))
            patch = frame[yi:yi + hh, xi:xi + self.w].astype(np.float32)
            if patch.shape[:2] == (hh, self.w):
                if hh != self.h:
                    patch = cv2.resize(patch, (self.w, self.h), interpolation=cv2.INTER_LINEAR)
                self.t = (1 - self.rate) * self.t + self.rate * patch
        return (bx, by, float(self.w), float(hh)), float(max(0.0, best))

    def commit(self, frame, box, score):
        """Called by the pipeline for frames it accepted (motion-consistent): learn appearance."""
        x, y, w, h = (int(round(v)) for v in box)
        if x < 0 or y < 0:
            return
        patch = frame[y:y + h, x:x + w].astype(np.float32)
        if patch.shape[0] < 4 or patch.shape[1] != w or patch.shape[0] != h:
            return
        if (h, w) != (self.h, self.w):
            patch = cv2.resize(patch, (self.w, self.h), interpolation=cv2.INTER_LINEAR)
        self._maybe_bank(patch, score)


class FlowTracker(BaseTracker):
    """Median LK flow of features inside the box."""
    name = "flow"

    def init(self, frame, bbox):
        self.prev = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self.box = list(_clip_box(bbox, frame.shape))

    def update(self, frame, hint):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        x, y, w, h = _clip_box(self.box, gray.shape)
        m = np.zeros_like(gray); m[y:y + h, x:x + w] = 255
        pts = cv2.goodFeaturesToTrack(self.prev, 60, 0.01, 2, mask=m)
        if pts is None or len(pts) < 3:
            self.prev = gray
            return None, 0.0
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(self.prev, gray, pts, None, winSize=(15, 15), maxLevel=3)
        ok = st.ravel() == 1
        self.prev = gray
        if ok.sum() < 3:
            return None, 0.0
        d = np.median((nxt - pts).reshape(-1, 2)[ok], axis=0)
        self.box = [self.box[0] + d[0], self.box[1] + d[1], self.box[2], self.box[3]]
        return tuple(float(v) for v in self.box), float(ok.mean())


class ColorModel:
    """Object/background colour ratio histogram for segmentation-based bbox refinement."""

    def __init__(self, frame, bbox):
        x, y, w, h = _clip_box(bbox, frame.shape)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        self.size = (w, h)
        obj_mask = np.zeros(frame.shape[:2], np.uint8)
        obj_mask[y:y + h, x:x + w] = 255   # background inside a loose box is suppressed by the ratio
        ring = np.zeros_like(obj_mask)
        X0, Y0 = max(0, x - w), max(0, y - h)
        ring[Y0:y + 2 * h, X0:x + 2 * w] = 255
        ring[y:y + h, x:x + w] = 0
        bins, ranges = [18, 8, 4], [0, 180, 0, 256, 0, 256]
        ho = cv2.calcHist([hsv], [0, 1, 2], obj_mask, bins, ranges)
        hb = cv2.calcHist([hsv], [0, 1, 2], ring, bins, ranges)
        ho /= max(ho.sum(), 1); hb /= max(hb.sum(), 1)
        ratio = ho / (ho + hb + 1e-6)
        ratio[ho < 0.002] = 0
        self.hist = (ratio * 255).astype(np.float32)
        self.bins, self.ranges = bins, ranges
        # how separable is the character from its surroundings?
        self.separability = float((ho * ratio).sum())

    def locate(self, frame, hint, radius: float) -> tuple[BBox | None, float]:
        """Pose-invariant fallback: find the blob of character colours nearest the prediction.

        Returns a box with the *original* box size, bottom-centre aligned to the blob."""
        H, W = frame.shape[:2]
        w0, h0 = self.size
        cx, cy = hint[0] + hint[2] / 2, hint[1] + hint[3] / 2
        rx, ry = w0 * (0.5 + radius), h0 * (0.5 + radius)
        x0, y0 = int(max(0, cx - rx)), int(max(0, cy - ry))
        x1, y1 = int(min(W, cx + rx)), int(min(H, cy + ry))
        if x1 - x0 < 4 or y1 - y0 < 4:
            return None, 0.0
        hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
        bp = cv2.calcBackProject([hsv], [0, 1, 2], self.hist, self.ranges, 1)
        fg = (bp > 128).astype(np.uint8)
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        n, lab, stats, cents = cv2.connectedComponentsWithStats(fg, 8)
        best, bd = None, 1e9
        area0 = w0 * h0
        for i in range(1, n):
            sx, sy, sw, sh, a = stats[i]
            if a < 0.15 * area0 or a > 2.0 * area0 or sw > 2.2 * w0 or sh > 2.2 * h0:
                continue
            d = np.hypot(cents[i][0] + x0 - cx, cents[i][1] + y0 - cy)
            if d < bd:
                best, bd = i, d
        if best is None:
            return None, 0.0
        sx, sy, sw, sh, a = stats[best]
        bcx = x0 + sx + sw / 2
        bottom = y0 + sy + sh
        fill = a / float(area0)
        return (float(bcx - w0 / 2), float(bottom - h0), float(w0), float(h0)), float(min(1.0, fill))

    def refine(self, frame, box) -> tuple[BBox | None, float]:
        H, W = frame.shape[:2]
        x, y, w, h = box
        ew, eh = self.size[0] * 0.8 + 6, self.size[1] * 0.8 + 6
        x0, y0 = int(max(0, x - ew)), int(max(0, y - eh))
        x1, y1 = int(min(W, x + w + ew)), int(min(H, y + h + eh))
        if x1 - x0 < 4 or y1 - y0 < 4:
            return None, 0.0
        hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
        bp = cv2.calcBackProject([hsv], [0, 1, 2], self.hist, self.ranges, 1)
        fg = (bp > 128).astype(np.uint8)
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        n, lab, stats, cents = cv2.connectedComponentsWithStats(fg, 8)
        if n <= 1:
            return None, 0.0
        cx, cy = x + w / 2 - x0, y + h / 2 - y0
        area0 = self.size[0] * self.size[1]
        keep = []
        for i in range(1, n):
            sx, sy, sw, sh, a = stats[i]
            if a < 0.03 * area0:
                continue
            # component must overlap the tracker box (in window coords)
            if sx + sw < x - x0 or sx > x - x0 + w or sy + sh < y - y0 or sy > y - y0 + h:
                continue
            keep.append(i)
        if not keep:
            return None, 0.0
        xs0 = min(stats[i][0] for i in keep); ys0 = min(stats[i][1] for i in keep)
        xs1 = max(stats[i][0] + stats[i][2] for i in keep); ys1 = max(stats[i][1] + stats[i][3] for i in keep)
        bw, bh = xs1 - xs0, ys1 - ys0
        if not (0.5 < bw / self.size[0] < 1.8 and 0.5 < bh / self.size[1] < 1.8):
            return None, 0.0
        fill = sum(stats[i][4] for i in keep) / max(1, bw * bh)
        return (float(x0 + xs0), float(y0 + ys0), float(bw), float(bh)), float(min(1.0, fill * 1.2))


class HybridTracker(BaseTracker):
    """Template matching for position + colour segmentation for the bbox extents
    (so the feet stay accurate through squash/stretch animation); falls back to the
    template box when segmentation is unreliable."""
    name = "hybrid"

    def init(self, frame, bbox):
        self.tm = TemplateTracker(search=1.5, update_rate=0.08)
        self.tm.init(frame, bbox)
        self.color = ColorModel(frame, bbox)
        self.use_seg = self.color.separability > 0.6
        self.last = tuple(float(v) for v in bbox)

    def update(self, frame, hint):
        b, score = self.tm.update(frame, hint)
        if b is None:
            return None, 0.0
        if self.use_seg:
            r, fill = self.color.refine(frame, b)
            if r is not None and fill > 0.3:
                # x/width from template centre (stable), vertical extents from segmentation
                cx = b[0] + b[2] / 2
                seg_cx = r[0] + r[2] / 2
                if abs(seg_cx - cx) < 0.35 * b[2]:
                    b = (seg_cx - r[2] / 2, r[1], r[2], r[3])
        self.last = b
        return b, score


def make_tracker(method: str, search: float = 1.5) -> BaseTracker:
    if method in ("csrt", "kcf", "mil"):
        return OpenCVTracker(method)
    if method == "template":
        return TemplateTracker(search=search)
    if method == "flow":
        return FlowTracker()
    if method == "hybrid":
        return HybridTracker()
    raise ValueError(f"unknown tracker {method}")


def appearance_score(frame, box, template) -> float:
    """NCC between the current box content and the initial template (resized)."""
    x, y, w, h = _clip_box(box, frame.shape)
    patch = frame[y:y + h, x:x + w]
    if patch.size == 0:
        return 0.0
    t = cv2.resize(template, (patch.shape[1], patch.shape[0]))
    r = cv2.matchTemplate(patch.astype(np.float32), t.astype(np.float32), cv2.TM_CCOEFF_NORMED)
    return float(max(0.0, r.max()))
