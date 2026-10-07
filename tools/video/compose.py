"""Edit the project video: storyboard -> 1920x1080 MP4 with burned-in Chinese subtitles + .srt.

Usage: python tools/video/compose.py WORK_DIR OUT_DIR
WORK_DIR contains tool/ (record_tool.py), unity_shots/, unity_frames/, godot_shots/, godot_frames/.
Rendering is done offscreen with Qt (QPainter), frames are piped to ffmpeg.
"""
from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import Qt, QRectF, QPointF  # noqa: E402
from PySide6.QtGui import (QImage, QPainter, QColor, QFont, QPen, QBrush, QLinearGradient,  # noqa: E402
                           QPainterPath, QFontMetrics)
from PySide6.QtWidgets import QApplication  # noqa: E402

W, H, FPS = 1920, 1080, 30
BG = QColor(18, 20, 26)
ACCENT = QColor(255, 170, 40)
CYAN = QColor(80, 220, 255)
FONT = "Noto Sans SC"


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


@dataclass
class Scene:
    kind: str                       # card | frames | still | split | diagram
    dur: float = 0.0                # seconds (frames scenes default to their length)
    caption: str = ""
    chapter: str = ""
    title: str = ""
    lines: list = field(default_factory=list)
    frames: list = field(default_factory=list)   # image paths
    frames2: list = field(default_factory=list)  # split screen right side
    labels: tuple = ("", "")
    speed: float = 1.0
    crop_from: tuple | None = None  # (x, y, w, h) in source pixels
    crop_to: tuple | None = None
    zoom_at: float = 0.6            # seconds into the scene when the zoom finishes
    zoom_start: float = 0.0
    highlights: list = field(default_factory=list)  # [(x,y,w,h, t_from)]
    keys: list | None = None        # per output frame: (left, right, jump)
    keys_note: str = ""
    caption_top: bool = False


class Composer:
    def __init__(self, out_mp4: Path):
        self.proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                      "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow",
                                      "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_mp4)],
                                     stdin=subprocess.PIPE)
        self.n = 0
        self.srt = []
        self.cache = {}

    def img(self, path):
        if path not in self.cache:
            if len(self.cache) > 64:
                self.cache.clear()
            self.cache[path] = QImage(str(path))
        return self.cache[path]

    def emit(self, im: QImage):
        im = im.convertToFormat(QImage.Format_RGB888)
        self.proc.stdin.write(bytes(im.constBits())[: W * H * 3])
        self.n += 1

    def close(self):
        self.proc.stdin.close(); self.proc.wait()

    # ------------------------------------------------------------------ drawing helpers
    def font(self, px, bold=False):
        f = QFont(FONT); f.setPixelSize(px); f.setBold(bold)
        return f

    def draw_caption(self, p: QPainter, text: str, alpha=1.0, top=False):
        if not text:
            return
        p.setFont(self.font(36))
        fm = QFontMetrics(p.font())
        maxw = W - 240
        lines = []
        for para in text.split("\n"):
            cur = ""
            for ch in para:
                if fm.horizontalAdvance(cur + ch) > maxw:
                    lines.append(cur); cur = ch
                else:
                    cur += ch
            lines.append(cur)
        lh = fm.height() + 8
        bh = lh * len(lines) + 36
        y0 = 96 if top else H - bh - 28
        bw = max(fm.horizontalAdvance(l) for l in lines) + 80
        rect = QRectF((W - bw) / 2, y0, bw, bh)
        path = QPainterPath(); path.addRoundedRect(rect, 18, 18)
        p.fillPath(path, QColor(10, 12, 16, int(215 * alpha)))
        p.setPen(QColor(255, 255, 255, int(255 * alpha)))
        for i, l in enumerate(lines):
            p.drawText(QRectF(0, y0 + 18 + i * lh, W, lh), Qt.AlignHCenter | Qt.AlignVCenter, l)

    def draw_chapter(self, p: QPainter, text: str):
        if not text:
            return
        p.setFont(self.font(26, True))
        fm = QFontMetrics(p.font())
        w = fm.horizontalAdvance(text) + 40
        r = QRectF(28, 24, w, 48)
        path = QPainterPath(); path.addRoundedRect(r, 24, 24)
        p.fillPath(path, QColor(255, 170, 40, 235))
        p.setPen(QColor(20, 20, 20))
        p.drawText(r, Qt.AlignCenter, text)

    def draw_keys(self, p: QPainter, state, note=""):
        left, right, jump = state
        caps = [("← A", left), ("→ D", right), ("Space 跳跃", jump)]
        x = W - 28
        y = 24
        p.setFont(self.font(24, True))
        fm = QFontMetrics(p.font())
        for label, on in reversed(caps):
            w = fm.horizontalAdvance(label) + 36
            x -= w
            r = QRectF(x, y, w, 50)
            path = QPainterPath(); path.addRoundedRect(r, 10, 10)
            p.fillPath(path, ACCENT if on else QColor(40, 44, 54, 230))
            p.setPen(QPen(QColor(255, 255, 255, 120), 2)); p.drawPath(path)
            p.setPen(QColor(20, 20, 20) if on else QColor(220, 220, 220))
            p.drawText(r, Qt.AlignCenter, label)
            x -= 12
        if note:
            p.setFont(self.font(20))
            p.setPen(QColor(200, 200, 200))
            p.drawText(QRectF(W - 900, y + 56, 872, 30), Qt.AlignRight | Qt.AlignVCenter, note)

    def place(self, p: QPainter, im: QImage, crop, target=QRectF(0, 0, W, H)):
        """Draw crop of im into target keeping aspect; returns mapping (sx, sy, ox, oy) src->dst."""
        cx, cy, cw, ch = crop
        s = min(target.width() / cw, target.height() / ch)
        dw, dh = cw * s, ch * s
        ox = target.x() + (target.width() - dw) / 2
        oy = target.y() + (target.height() - dh) / 2
        p.drawImage(QRectF(ox, oy, dw, dh), im, QRectF(cx, cy, cw, ch))
        return s, ox - cx * s, oy - cy * s

    # ------------------------------------------------------------------ scenes
    def render(self, sc: Scene):
        # every scene stays on screen at least as long as its subtitle needs to be read
        # (~7 Chinese characters per second + 1.5 s); short clips are played slower instead
        min_dur = len(sc.caption.replace("\n", "")) / 7.0 + 1.5 if sc.caption else 0.0
        if sc.kind in ("frames", "split"):
            if not sc.dur:
                sc.dur = len(sc.frames) / FPS / sc.speed
            if sc.dur < min_dur:
                sc.speed = sc.speed * sc.dur / min_dur
                sc.dur = min_dur
        else:
            sc.dur = max(sc.dur, min_dur)
        n = max(1, int(round(sc.dur * FPS)))
        t_start = self.n / FPS
        if sc.caption:
            self.srt.append((t_start, t_start + sc.dur, sc.caption))
        for i in range(n):
            t = i / FPS
            im = QImage(W, H, QImage.Format_RGB32)
            im.fill(BG)
            p = QPainter(im)
            p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
            if sc.kind == "card":
                self.card(p, sc, t)
            elif sc.kind == "diagram":
                self.diagram(p, sc, t)
            elif sc.kind in ("frames", "still"):
                src = sc.frames[min(len(sc.frames) - 1, int(i * sc.speed))] if sc.kind == "frames" else sc.frames[0]
                img = self.img(src)
                full = (0, 0, img.width(), img.height())
                a = sc.crop_from or full
                b = sc.crop_to or a
                k = ease((t - sc.zoom_start) / max(sc.zoom_at - sc.zoom_start, 1e-6)) if sc.crop_to else 0.0
                crop = tuple(a[j] + (b[j] - a[j]) * k for j in range(4))
                s, tx, ty = self.place(p, img, crop)
                for (hx, hy, hw, hh, tf) in sc.highlights:
                    if t < tf:
                        continue
                    al = ease((t - tf) / 0.4)
                    r = QRectF(tx + hx * s, ty + hy * s, hw * s, hh * s)
                    outer = QPainterPath(); outer.addRect(QRectF(0, 0, W, H))
                    inner = QPainterPath(); inner.addRoundedRect(r, 10, 10)
                    p.fillPath(outer.subtracted(inner), QColor(0, 0, 0, int(110 * al)))
                    p.setPen(QPen(QColor(255, 170, 40, int(255 * al)), 5)); p.setBrush(Qt.NoBrush)
                    p.drawRoundedRect(r, 10, 10)
                if sc.keys:
                    self.draw_keys(p, sc.keys[min(len(sc.keys) - 1, int(i * sc.speed))], sc.keys_note)
            elif sc.kind == "split":
                for side, (frames, label) in enumerate(((sc.frames, sc.labels[0]), (sc.frames2, sc.labels[1]))):
                    img = self.img(frames[min(len(frames) - 1, int(i * sc.speed))])
                    tgt = QRectF(side * W / 2 + 12, 150, W / 2 - 24, (W / 2 - 24) * 9 / 16)
                    self.place(p, img, (0, 0, img.width(), img.height()), tgt)
                    p.setPen(QPen(QColor(255, 255, 255, 60), 2)); p.drawRect(tgt)
                    p.setFont(self.font(34, True)); p.setPen(ACCENT)
                    p.drawText(QRectF(tgt.x(), 92, tgt.width(), 50), Qt.AlignCenter, label)
                if sc.keys:
                    self.draw_keys(p, sc.keys[min(len(sc.keys) - 1, int(i * sc.speed))], sc.keys_note)
            if sc.kind not in ("card",):
                self.draw_chapter(p, sc.chapter)
            self.draw_caption(p, sc.caption, alpha=ease(t / 0.25), top=sc.caption_top)
            p.end()
            self.emit(im)

    def card(self, p, sc, t):
        g = QLinearGradient(0, 0, W, H)
        g.setColorAt(0, QColor(24, 28, 40)); g.setColorAt(1, QColor(12, 13, 18))
        p.fillRect(QRectF(0, 0, W, H), QBrush(g))
        al = ease(t / 0.5)
        if sc.chapter:
            p.setFont(self.font(30, True)); p.setPen(QColor(255, 170, 40, int(255 * al)))
            p.drawText(QRectF(0, 250, W, 50), Qt.AlignCenter, sc.chapter)
        p.setFont(self.font(68, True)); p.setPen(QColor(255, 255, 255, int(255 * al)))
        p.drawText(QRectF(0, 310, W, 110), Qt.AlignCenter, sc.title)
        p.setFont(self.font(34)); p.setPen(QColor(205, 210, 220, int(255 * al)))
        for i, l in enumerate(sc.lines):
            p.drawText(QRectF(160, 450 + i * 62, W - 320, 60), Qt.AlignCenter, l)

    def diagram(self, p, sc, t):
        p.fillRect(QRectF(0, 0, W, H), QColor(16, 18, 24))
        p.setFont(self.font(44, True)); p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(0, 90, W, 70), Qt.AlignCenter, sc.title)
        boxes = sc.lines
        n = len(boxes)
        bw, bh, gap = 280, 150, 46
        total = n * bw + (n - 1) * gap
        x0 = (W - total) / 2
        y = 330
        for i, (head, sub) in enumerate(boxes):
            al = ease((t - 0.35 * i) / 0.4)
            if al <= 0:
                continue
            r = QRectF(x0 + i * (bw + gap), y, bw, bh)
            path = QPainterPath(); path.addRoundedRect(r, 16, 16)
            p.fillPath(path, QColor(40, 46, 60, int(255 * al)))
            p.setPen(QPen(QColor(255, 170, 40, int(255 * al)), 3)); p.drawPath(path)
            p.setFont(self.font(30, True)); p.setPen(QColor(255, 255, 255, int(255 * al)))
            p.drawText(QRectF(r.x(), r.y() + 18, bw, 50), Qt.AlignCenter, head)
            p.setFont(self.font(22)); p.setPen(QColor(200, 205, 215, int(255 * al)))
            p.drawText(QRectF(r.x() + 12, r.y() + 70, bw - 24, 70), Qt.AlignCenter | Qt.TextWordWrap, sub)
            if i < n - 1:
                ax = r.right() + 6
                p.setPen(QPen(QColor(255, 170, 40, int(255 * al)), 4))
                p.drawLine(QPointF(ax, y + bh / 2), QPointF(ax + gap - 14, y + bh / 2))
                p.drawLine(QPointF(ax + gap - 14, y + bh / 2), QPointF(ax + gap - 26, y + bh / 2 - 10))
                p.drawLine(QPointF(ax + gap - 14, y + bh / 2), QPointF(ax + gap - 26, y + bh / 2 + 10))
        if sc.frames:  # extra bullet text under the diagram
            p.setFont(self.font(30)); p.setPen(QColor(220, 225, 235))
            for i, l in enumerate(sc.frames):
                al = ease((t - 0.35 * n - 0.3 * i) / 0.4)
                p.setPen(QColor(220, 225, 235, int(255 * al)))
                p.drawText(QRectF(200, 560 + i * 56, W - 400, 50), Qt.AlignCenter, l)


def srt_time(s):
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def keys_from_csv(path, n_out, speed=1.0):
    rows = list(csv.DictReader(open(path)))
    out = []
    for i in range(n_out):
        r = rows[min(len(rows) - 1, int(i * speed))]
        a, j = int(r["axis"]), int(r["jump"])
        out.append((a < 0, a > 0, j == 1))
    return out
