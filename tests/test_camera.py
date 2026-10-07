"""Camera compensation on generated images with known motion (fast, no video decoding)."""
import numpy as np
import pytest
import cv2

from rcf.tracking.camera import CameraEstimator, estimate_shift, estimate_shift_layered, _despike


def textured(w, h, seed):
    rng = np.random.default_rng(seed)
    img = (rng.random((h // 8, w // 8)) * 255).astype(np.uint8)
    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_NEAREST)
    return cv2.GaussianBlur(img, (3, 3), 0)


def test_translation_is_recovered():
    world = textured(1200, 300, 1)
    a = world[:, 100:500]
    b = world[:, 107:507]          # camera moved right by 7 px -> features move left by 7
    e = estimate_shift(a, b, None)
    assert e.dx == pytest.approx(-7, abs=0.15) and e.dy == pytest.approx(0, abs=0.15)
    assert e.confidence > 0.5


def test_estimator_accumulates_and_has_no_drift_when_static():
    world = textured(2000, 240, 2)
    est = CameraEstimator(None)
    cams = [int(round(3.3 * k)) for k in range(60)] + [int(round(3.3 * 59))] * 60   # pan, then stop
    for c in cams:
        est.update(world[:, c:c + 400], (180, 150, 20, 40))
    tr = est.result()
    err = np.abs((tr.cam_x - tr.cam_x[0]) - (np.array(cams) - cams[0]))
    assert err.max() < 1.0
    assert np.ptp(tr.cam_x[-60:]) < 0.3            # no drift while static
    assert tr.level == "HIGH"


def test_parallax_prefers_ground_layer_below_the_feet():
    # far layer (top 2/3) moves 1 px/frame, ground layer (bottom third) moves 6 px/frame
    far = textured(1600, 160, 3)
    ground = textured(3000, 80, 4)
    def frame(k):
        return np.vstack([far[:, k:k + 400], ground[:, 6 * k:6 * k + 400]])
    a, b = frame(10), frame(11)
    mask = np.full(a.shape, 255, np.uint8)
    e = estimate_shift_layered(a, b, mask, focus_y=165.0)
    assert e.dx == pytest.approx(-6, abs=0.3)


def test_despike_removes_single_bad_step_but_keeps_stops():
    p = np.cumsum([0, 5, 5, 5, -12, 5, 5, 0, 0, 0], dtype=float)
    q = _despike(p)
    assert np.allclose(np.diff(q), [5, 5, 5, 5, 5, 5, 0, 0, 0])
