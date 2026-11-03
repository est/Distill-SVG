"""Thin-stroke and separator detection.

Flat-panel extraction recovers large filled rounded rectangles, but it
deliberately rejects very long, thin components (see ``panels.py``).  Those
rejects are exactly the UI chrome this module is for:

* 1-2 px dividers/rules between list rows and sections,
* card and input borders,
* underlines below labels,
* thin progress bars and scrollbar tracks.

The detector works on the full-resolution image, not the quantised analysis
image, because a one-pixel separator is easily lost when a screenshot is
downsampled.  Lines are found with a local-background contrast mask followed
by directional morphological opening; components are filtered by length,
thickness and aspect ratio, collinear fragments are merged, and the stroke
colour is sampled from the strongest-contrast pixel in each column/row.
Candidates inside text/photo masks or duplicating a flat panel fill are
discarded.

Coordinates returned by :func:`detect_strokes` are in the pixel space of the
supplied image (for a :class:`~caption_svg.flatten.Prepared` this is
``prep.original``, i.e. the full screenshot).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from .flatten import Prepared, color_distance, hex_to_rgb
from .panels import Panel

__all__ = ["Stroke", "detect_strokes"]


@dataclass
class Stroke:
    """An axis-aligned thin line, in full-resolution image coordinates."""

    x1: float
    y1: float
    x2: float
    y2: float
    color: str
    width: float = 1.0
    #: ``divider`` | ``border`` | ``underline`` | ``bar``
    kind: str = "divider"
    confidence: float = 0.5

    @property
    def horizontal(self) -> bool:
        return abs(self.x2 - self.x1) >= abs(self.y2 - self.y1)

    @property
    def length(self) -> float:
        return float(np.hypot(self.x2 - self.x1, self.y2 - self.y1))

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["confidence"] = round(float(self.confidence), 3)
        return data


@dataclass
class _Candidate:
    """Geometry-only line fragment, before colour/context classification."""

    horizontal: bool
    pos: float          # y for horizontal lines, x for vertical lines
    lo: int             # inclusive start along the long axis
    hi: int             # inclusive end along the long axis
    thickness: float

    @property
    def length(self) -> int:
        return self.hi - self.lo + 1


def _to_bgr(source: Prepared | np.ndarray | str | Path) -> np.ndarray:
    """Normalise any supported input to a contiguous uint8 BGR image."""
    if isinstance(source, Prepared):
        return source.original
    if isinstance(source, (str, Path)):
        image = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if image is None:
            raise FileNotFoundError(str(source))
        return image

    array = np.asarray(source)
    if array.ndim == 2:
        array = cv2.cvtColor(array, cv2.COLOR_GRAY2BGR)
    elif array.ndim == 3 and array.shape[2] == 4:
        array = cv2.cvtColor(array, cv2.COLOR_BGRA2BGR)
    elif not (array.ndim == 3 and array.shape[2] == 3):
        raise ValueError(f"expected HxW or HxWx3/4 image, got shape {array.shape}")

    if array.dtype != np.uint8:
        floats = np.clip(array.astype(np.float32), 0.0, 255.0)
        if floats.size and float(floats.max()) <= 1.0:
            floats *= 255.0
        array = floats.astype(np.uint8)
    return np.ascontiguousarray(array)


def _background_diff(gray: np.ndarray) -> np.ndarray:
    """Signed difference from a median-filtered local background.

    A 1-4 px stroke is narrower than the 9 px median window, so the median
    encodes the surrounding flat surface and the stroke survives as contrast.
    Wide fills and step edges are suppressed, which is right: panels own those.
    """
    ksize = 9
    if min(gray.shape[:2]) < ksize:
        ksize = max(3, (min(gray.shape[:2]) // 2) * 2 - 1)
    background = cv2.medianBlur(gray, ksize)
    return gray.astype(np.float32) - background.astype(np.float32)


def _local_std(gray: np.ndarray, ksize: int = 7) -> np.ndarray:
    """Per-pixel local standard deviation, used to reject busy photo/text areas."""
    values = gray.astype(np.float32)
    mean = cv2.blur(values, (ksize, ksize))
    mean_sq = cv2.blur(values * values, (ksize, ksize))
    return np.sqrt(np.maximum(0.0, mean_sq - mean * mean))


def _extract(mask: np.ndarray, horizontal: bool, min_length: int, max_thickness: int) -> list[_Candidate]:
    """Directional opening + connected-component filtering for one orientation.

    Vertical detection transposes the mask so both orientations share the same
    code; the resulting ``pos``/``lo``/``hi`` values are already in original
    image coordinates.
    """
    work = mask if horizontal else np.ascontiguousarray(cv2.transpose(mask))
    if work.shape[1] < min_length:
        return []

    kernel_len = min(min_length + (0 if min_length % 2 else 1), work.shape[1])
    bridge = np.ones((1, 3), np.uint8)
    work = cv2.morphologyEx(work, cv2.MORPH_CLOSE, bridge)
    work = cv2.morphologyEx(work, cv2.MORPH_OPEN, np.ones((1, kernel_len), np.uint8))
    work = cv2.morphologyEx(work, cv2.MORPH_CLOSE, bridge)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(work, connectivity=8)
    found: list[_Candidate] = []
    for index in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[index])
        if w < min_length or h < 1 or h > max_thickness or w < h * 3:
            continue
        component = labels[y:y + h, x:x + w] == index
        rows = component.sum(axis=1)
        # A straight stroke fills most of its longest row; a glyph does not.
        if rows.max() < 0.60 * w:
            continue
        pos = y + float((np.arange(h, dtype=np.float32) * rows).sum() / max(1, rows.sum()))
        found.append(_Candidate(horizontal, pos, x, x + w - 1, max(1.0, area / float(w))))
    return found


def _merge(candidates: Sequence[_Candidate], pos_tolerance: float, gap: int) -> list[_Candidate]:
    """Merge collinear fragments separated by small gaps (text holes, AA)."""
    merged_all: list[_Candidate] = []
    for horizontal in (True, False):
        group = sorted((c for c in candidates if c.horizontal == horizontal), key=lambda c: (c.pos, c.lo))
        merged: list[_Candidate] = []
        for candidate in group:
            last = merged[-1] if merged else None
            if last and abs(candidate.pos - last.pos) <= pos_tolerance and candidate.lo <= last.hi + gap:
                total = last.length + candidate.length
                last.pos = (last.pos * last.length + candidate.pos * candidate.length) / total
                last.lo, last.hi = min(last.lo, candidate.lo), max(last.hi, candidate.hi)
                last.thickness = max(last.thickness, candidate.thickness)
            else:
                merged.append(_Candidate(candidate.horizontal, candidate.pos,
                                         candidate.lo, candidate.hi, candidate.thickness))
        merged_all.extend(merged)
    return merged_all


def _matching_panel(candidate: _Candidate, panels: Sequence[Panel], tolerance: float = 2.0) -> Panel | None:
    """Return the panel whose edge this stroke overlaps, if any."""
    for panel in panels:
        if candidate.horizontal:
            edge_a, edge_b, start, end, span = panel.y, panel.y + panel.h, panel.x, panel.x + panel.w, panel.w
        else:
            edge_a, edge_b, start, end, span = panel.x, panel.x + panel.w, panel.y, panel.y + panel.h, panel.h
        if min(abs(candidate.pos - edge_a), abs(candidate.pos - edge_b)) > tolerance:
            continue
        overlap = min(candidate.hi + 1, end) - max(candidate.lo, start)
        if overlap > 0 and overlap >= 0.5 * min(candidate.length, span) and overlap >= 0.6 * candidate.length:
            return panel
    return None


def _text_hit(candidate: _Candidate, boxes: Sequence[tuple[float, float, float, float]], *,
              pad: float = 1.0, below: float = 0.0, ratio: float = 0.5) -> bool:
    """Check overlap with text boxes; ``below>0`` looks for an underline under them."""
    if not boxes or (below > 0 and not candidate.horizontal):
        return False
    for x, y, w, h in boxes:
        if below > 0:
            if not (y + h - 2.0 <= candidate.pos <= y + h + below):
                continue
            lo, hi = x, x + w
        elif candidate.horizontal:
            if not (y - pad <= candidate.pos <= y + h + pad):
                continue
            lo, hi = x, x + w
        else:
            if not (x - pad <= candidate.pos <= x + w + pad):
                continue
            lo, hi = y, y + h
        if min(candidate.hi + 1, hi) - max(candidate.lo, lo) >= ratio * candidate.length:
            return True
    return False


def _hex(rgb: np.ndarray) -> str:
    r, g, b = (int(round(float(v))) for v in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def _close_to_fill(rgb: np.ndarray, fill: str, tolerance: float = 14.0) -> bool:
    try:
        reference = np.asarray(hex_to_rgb(fill), dtype=np.float32)
    except (ValueError, IndexError):
        return False
    return color_distance(rgb, reference) <= tolerance


def _measure(candidate: _Candidate, bgr: np.ndarray, diff_abs: np.ndarray, std_map: np.ndarray, *,
             contrast_threshold: int, max_ambient_std: float | None, panels: Sequence[Panel],
             text_boxes: Sequence[tuple[float, float, float, float]], exclude_mask: np.ndarray | None,
             reject_panel_edges: bool, max_thickness: int) -> Stroke | None:
    """Sample colour/context for one merged candidate and classify it."""
    height, width = diff_abs.shape[:2]
    horizontal = candidate.horizontal
    center = int(round(candidate.pos))
    thickness = max(1, int(round(candidate.thickness)))
    perp0 = center - (thickness - 1) // 2
    perp1 = center + thickness // 2 + 1

    if horizontal:
        y0, y1, x0, x1 = max(0, perp0), min(height, perp1), max(0, candidate.lo), min(width, candidate.hi + 1)
    else:
        x0, x1, y0, y1 = max(0, perp0), min(width, perp1), max(0, candidate.lo), min(height, candidate.hi + 1)
    if x1 <= x0 or y1 <= y0:
        return None

    band = bgr[y0:y1, x0:x1]
    weights = diff_abs[y0:y1, x0:x1]
    # Pick the strongest-contrast pixel in each column/row of the band so AA
    # fringe pixels do not wash out the true stroke colour.
    idx = weights.argmax(axis=0 if horizontal else 1)
    if horizontal:
        colours = band[idx, np.arange(band.shape[1])]
        contrast = float(np.median(weights.max(axis=0)))
    else:
        colours = band[np.arange(band.shape[0]), idx]
        contrast = float(np.median(weights.max(axis=1)))
    if colours.size == 0:
        return None

    offset = thickness // 2 + 2
    ambient_parts = []
    for position in (perp0 - offset, perp1 - 1 + offset):
        if horizontal and 0 <= position < height:
            ambient_parts.append(std_map[position, x0:x1])
        elif not horizontal and 0 <= position < width:
            ambient_parts.append(std_map[y0:y1, position])
    ambient = float(np.median(np.concatenate(ambient_parts))) if ambient_parts else 0.0
    if max_ambient_std is not None and ambient > max_ambient_std:
        return None

    if exclude_mask is not None:
        sample = exclude_mask[center, candidate.lo:candidate.hi + 1] if horizontal \
            else exclude_mask[candidate.lo:candidate.hi + 1, center]
        if sample.size and float(np.mean(sample != 0)) > 0.5:
            return None
    if text_boxes and _text_hit(candidate, text_boxes):
        return None

    rgb = np.median(colours, axis=0)[::-1]
    panel = _matching_panel(candidate, panels) if panels else None
    if panel is not None and (reject_panel_edges or _close_to_fill(rgb, panel.fill)):
        return None

    if panel is not None:
        kind = "border"
    elif candidate.thickness >= 2.5:
        kind = "bar"
    elif _text_hit(candidate, text_boxes, below=10.0, ratio=0.35):
        kind = "underline"
    else:
        kind = "divider"

    length_score = min(1.0, candidate.length / 90.0)
    contrast_score = min(1.0, contrast / max(1.0, 2.0 * contrast_threshold))
    ambient_score = 1.0 if max_ambient_std is None else max(0.0, 1.0 - ambient / float(max_ambient_std))
    confidence = 0.30 + 0.28 * length_score + 0.27 * contrast_score + 0.15 * ambient_score
    confidence = float(np.clip(confidence + (0.05 if kind == "border" else 0.0), 0.05, 0.99))

    pos = float(round(candidate.pos * 2.0) / 2.0)
    if horizontal:
        x1_out, y1_out, x2_out, y2_out = float(candidate.lo), pos, float(candidate.hi), pos
    else:
        x1_out, y1_out, x2_out, y2_out = pos, float(candidate.lo), pos, float(candidate.hi)
    return Stroke(
        x1=x1_out, y1=y1_out, x2=x2_out, y2=y2_out, color=_hex(rgb),
        width=float(np.clip(round(candidate.thickness), 1, max_thickness)),
        kind=kind, confidence=confidence,
    )


def detect_strokes(
    prep_or_image: Prepared | np.ndarray | str | Path,
    panels: Sequence[Panel] | None = None,
    *,
    text_boxes: Sequence[tuple[float, float, float, float]] | None = None,
    exclude_mask: np.ndarray | None = None,
    min_length: int = 16,
    max_thickness: int = 4,
    contrast: int = 10,
    merge_gap: int = 4,
    merge_tolerance: float = 1.0,
    max_ambient_std: float | None = 34.0,
    reject_panel_edges: bool = False,
) -> list[Stroke]:
    """Find thin horizontal/vertical strokes missed by flat-panel extraction.

    Args:
        prep_or_image: A :class:`~caption_svg.flatten.Prepared` (detection runs
            on ``prep.original``), an HxW/HxWx3/HxWx4 array, or an image path.
        panels: Panels from :func:`caption_svg.panels.detect_panels`.  Used to
            label edge strokes as ``border`` and drop strokes that duplicate a
            flat fill boundary.  Coordinates must match the image.
        text_boxes: Optional OCR boxes ``(x, y, w, h)``.  Strokes mostly inside
            a box are suppressed; a rule below a box becomes ``underline``.
        exclude_mask: Optional non-zero mask (photo/icon/text regions).  Strokes
            with more than half their pixels inside it are dropped.  It is
            resized to the working image, so an analysis-scale mask is fine.
        min_length: Minimum stroke length in pixels.
        max_thickness: Maximum accepted stroke thickness in pixels (1-4 typical).
        contrast: Minimum absolute grey-level difference from local background.
        merge_gap: Maximum collinear gap (pixels) bridged during merging.
        merge_tolerance: Maximum perpendicular offset when merging fragments.
        max_ambient_std: Reject strokes surrounded by a busy area (photos,
            textured cards).  ``None`` disables the check.
        reject_panel_edges: Drop *all* strokes on panel boundaries, including
            real borders.  Useful when the caller draws panel borders itself.

    Returns:
        Deterministically sorted strokes in the image's pixel coordinates.
    """
    bgr = _to_bgr(prep_or_image)
    height, width = bgr.shape[:2]
    if min(height, width) < 8 or min_length < 3 or max_thickness < 1:
        return []

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    diff_abs = np.abs(_background_diff(gray))
    line_mask = (diff_abs >= float(contrast)).astype(np.uint8)
    std_map = _local_std(gray)
    mask = None
    if exclude_mask is not None:
        mask = np.asarray(exclude_mask)
        if mask.ndim == 3:
            mask = mask[..., 0]
        if mask.shape[:2] != (height, width):
            mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)

    candidates: list[_Candidate] = []
    for horizontal in (True, False):
        candidates.extend(_extract(line_mask, horizontal, min_length, max_thickness))

    strokes: list[Stroke] = []
    for candidate in _merge(candidates, merge_tolerance, merge_gap):
        stroke = _measure(
            candidate, bgr, diff_abs, std_map,
            contrast_threshold=contrast, max_ambient_std=max_ambient_std,
            panels=panels or (), text_boxes=text_boxes or (), exclude_mask=mask,
            reject_panel_edges=reject_panel_edges, max_thickness=max_thickness,
        )
        if stroke is not None:
            strokes.append(stroke)

    strokes.sort(key=lambda s: (0 if s.horizontal else 1, round(s.y1, 2),
                                round(s.x1, 2), round(s.x2, 2), round(s.y2, 2)))
    return strokes
