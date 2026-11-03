"""Flat-panel extraction.

We do not try to understand every UI widget.  A screenshot is mostly a stack
of axis-aligned rounded rectangles: page background, cards, chat bubbles,
input bars, pills.  Recovering those as SVG <rect> elements gives most of the
visual structure for a tiny fraction of the size of a bitmap trace.

Panels are found from the quantized palette.  Text and icons punch holes in a
panel, so a component's "solidity" is deliberately allowed to be low.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import cv2
import numpy as np

from .flatten import Prepared, color_distance


@dataclass
class Panel:
    x: float
    y: float
    w: float
    h: float
    rx: float
    fill: str
    area: float = 0.0

    def to_dict(self):
        d = asdict(self)
        d.pop("area", None)
        return d


def _estimate_radius(comp: np.ndarray, w: int, h: int) -> float:
    """Estimate a rounded-corner radius from the empty corner area.

    For a quarter circle of radius r, the empty part of an r x r corner square
    is r^2 * (1 - pi/4).  Using a fixed 10px window is robust against the
    low-resolution / dithered captures we care about, and clamps to sane UI
    values.
    """
    m = min(10, w // 2, h // 2)
    if m < 3:
        return 0.0
    rs = []
    for flip_x, flip_y in ((0, 0), (1, 0), (0, 1), (1, 1)):
        patch = comp[:m, :m]
        if flip_x:
            patch = patch[:, ::-1]
        if flip_y:
            patch = patch[::-1]
        empty = m * m - int(patch.sum())
        rs.append(float(np.sqrt(max(0.0, empty) / (1.0 - np.pi / 4.0))))
    r = float(np.median(rs))
    # The corner-area estimate is biased low for pills; clamp and round.
    return float(np.clip(round(r), 0, min(w, h) // 2))


def _dedupe(panels: list[Panel]) -> list[Panel]:
    kept: list[Panel] = []
    for p in sorted(panels, key=lambda z: -z.area):
        duplicate = False
        for q in kept:
            # Nearly identical geometry: keep the larger one (usually the real
            # panel, not an anti-aliased fringe cluster).
            if (
                abs(p.x - q.x) <= 2
                and abs(p.y - q.y) <= 2
                and abs(p.w - q.w) <= 2
                and abs(p.h - q.h) <= 2
            ):
                duplicate = True
                break
            # A panel almost completely contained in another and sharing the
            # same fill is usually a text hole / reaction chip, not a new panel.
            if (
                p.fill == q.fill
                and p.x >= q.x - 1
                and p.y >= q.y - 1
                and p.x + p.w <= q.x + q.w + 1
                and p.y + p.h <= q.y + q.h + 1
                and p.area < q.area * 0.75
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(p)
    return kept


def _background_color(prep: Prepared) -> tuple[int, int, int]:
    """Mode of the whole flat image, robust to dithered flat backgrounds.

    The border is a bad place to sample a phone screenshot: it can be mostly
    the white composer/input bar.  The page background is normally the single
    most common colour in the image, so use the mode of all pixels.
    """
    flat = prep.flat
    # Coarse buckets collapse the last bit of dither; then use the median of
    # the winning bucket to recover the actual colour instead of the bucket
    # floor.
    q = (flat.astype(np.int16) // 3) * 3
    px = q.reshape(-1, 3)
    u, inverse, counts = np.unique(px, axis=0, return_inverse=True, return_counts=True)
    bucket = u[counts.argmax()]
    mask = np.all(px == bucket, axis=1)
    med = np.median(flat.reshape(-1, 3)[mask], axis=0)
    return tuple(int(round(v)) for v in med[::-1])


def detect_panels(prep: Prepared, min_area_frac: float = 0.0015) -> tuple[str, list[Panel]]:
    h, w = prep.labels.shape
    bg = _background_color(prep)
    panels: list[Panel] = []
    min_area = max(60.0, w * h * min_area_frac)

    for k in range(len(prep.centers_rgb)):
        cluster_rgb = prep.centers_rgb[k]
        if color_distance(cluster_rgb, bg) < 3.0:
            # Same colour as the page; deliberately invisible.  The background
            # rectangle already covers it.
            continue
        mask = (prep.labels == k).astype(np.uint8)
        if mask.sum() < min_area:
            continue
        n, cc, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        for i in range(1, n):
            x, y, cw, ch, area = (int(v) for v in stats[i])
            if area < min_area or cw < 8 or ch < 6:
                continue
            if cw > w * 0.98 and ch > h * 0.98:
                continue
            extent = area / float(cw * ch)
            if extent < 0.45:
                continue
            # Very long thin components are text lines, separators or borders,
            # not panels.
            if max(cw / ch, ch / cw) > 18:
                continue

            comp = (cc[y:y + ch, x:x + cw] == i).astype(np.uint8)
            contours, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                continue
            contour = max(contours, key=cv2.contourArea)
            hull = cv2.convexHull(contour)
            hull_area = max(float(cv2.contourArea(hull)), 1.0)
            solidity = area / hull_area
            if solidity < 0.52:
                continue

            # The fill is the median of the actual flat pixels in this
            # component, not the pixel at its centre (which is often glyph).
            ys, xs = np.where(comp)
            ys = ys + y
            xs = xs + x
            px = prep.flat[ys, xs]
            rgb = np.median(px, axis=0)[::-1]
            # Remove the last bit of dither by snapping to even values.
            rgb = np.clip(np.round(rgb / 2.0) * 2.0, 0, 255)

            rx = _estimate_radius(comp, cw, ch)
            panels.append(
                Panel(
                    x=x * prep.scale,
                    y=y * prep.scale,
                    w=cw * prep.scale,
                    h=ch * prep.scale,
                    rx=rx * prep.scale,
                    fill=_hex(rgb),
                    area=float(area),
                )
            )

    panels = _dedupe(panels)
    # Large panels first so that smaller cards/bubbles paint on top.
    panels.sort(key=lambda p: -p.area)

    return _hex(np.asarray(bg, dtype=np.float32)), panels


def _hex(rgb) -> str:
    r, g, b = (int(round(float(v))) for v in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"

