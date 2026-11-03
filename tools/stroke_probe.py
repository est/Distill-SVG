#!/usr/bin/env python3
"""Synthetic probe for :mod:`caption_svg.strokes`.

Run from the repository root::

    python tools/stroke_probe.py

The probe builds a small UI-like card (rounded 1 px border, horizontal divider,
vertical separator, underline, progress bar, text-like noise and a flat filled
footer), runs the detector and prints every stroke.  It exits non-zero if an
expected stroke is missing or an obvious false positive appears.

No files are written into the repository; the synthetic PNG goes to the system
temporary directory only.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from caption_svg.panels import Panel  # noqa: E402
from caption_svg.strokes import Stroke, detect_strokes  # noqa: E402

CARD = (40, 30, 400, 270, 14)  # x0, y0, x1, y1, radius
TEXT_BOX = (56.0, 56.0, 148.0, 52.0)  # x, y, w, h


def _rounded_outline(image: np.ndarray, box: tuple[int, int, int, int, int],
                     colour: tuple[int, int, int], thickness: int = 1) -> None:
    x0, y0, x1, y1, radius = box
    cv2.line(image, (x0 + radius, y0), (x1 - radius, y0), colour, thickness, cv2.LINE_8)
    cv2.line(image, (x0 + radius, y1), (x1 - radius, y1), colour, thickness, cv2.LINE_8)
    cv2.line(image, (x0, y0 + radius), (x0, y1 - radius), colour, thickness, cv2.LINE_8)
    cv2.line(image, (x1, y0 + radius), (x1, y1 - radius), colour, thickness, cv2.LINE_8)
    cv2.ellipse(image, (x0 + radius, y0 + radius), (radius, radius), 180, 0, 90, colour, thickness, cv2.LINE_8)
    cv2.ellipse(image, (x1 - radius, y0 + radius), (radius, radius), 270, 0, 90, colour, thickness, cv2.LINE_8)
    cv2.ellipse(image, (x1 - radius, y1 - radius), (radius, radius), 0, 0, 90, colour, thickness, cv2.LINE_8)
    cv2.ellipse(image, (x0 + radius, y1 - radius), (radius, radius), 90, 0, 90, colour, thickness, cv2.LINE_8)


def synthesize() -> tuple[np.ndarray, list[Panel], list[tuple[float, float, float, float]]]:
    """Build a deterministic UI-like test image."""
    image = np.full((340, 460, 3), 255, np.uint8)
    border = (200, 200, 200)       # #c8c8c8
    divider = (208, 208, 208)      # #d0d0d0
    text_ink = (70, 70, 70)

    _rounded_outline(image, CARD, border)

    # Horizontal divider and vertical separator inside the card.
    cv2.line(image, (60, 140), (380, 140), divider, 1, cv2.LINE_8)
    cv2.line(image, (230, 50), (230, 250), divider, 1, cv2.LINE_8)

    # Underline just below the text-noise box.
    cv2.line(image, (60, 116), (150, 116), (176, 176, 176), 1, cv2.LINE_8)

    # Four-pixel progress bar (a "thick" stroke the panel pass rejects).
    cv2.rectangle(image, (60, 228), (220, 231), (246, 130, 59), -1, cv2.LINE_8)

    # Text-like noise: short glyph strokes, all below min_length=16.
    rng = np.random.default_rng(7)
    for _ in range(18):
        x = int(rng.integers(62, 194))
        y = int(rng.integers(62, 103))
        length = int(rng.integers(3, 10))
        cv2.line(image, (x, y), (min(x + length, 198), y), text_ink, 1, cv2.LINE_8)
    for _ in range(10):
        x = int(rng.integers(62, 198))
        y = int(rng.integers(62, 100))
        length = int(rng.integers(4, 10))
        cv2.line(image, (x, y), (x, min(y + length, 104)), text_ink, 1, cv2.LINE_8)

    # Flat filled footer: a panel, so it must not produce duplicate strokes.
    cv2.rectangle(image, (20, 300), (440, 320), (240, 240, 240), -1, cv2.LINE_8)

    panels = [
        Panel(x=40, y=30, w=360, h=240, rx=14, fill="#ffffff"),
        Panel(x=20, y=300, w=420, h=20, rx=0, fill="#f0f0f0"),
    ]
    return image, panels, [TEXT_BOX]


def _has_line(strokes: list[Stroke], *, horizontal: bool, pos: float, lo: float, hi: float,
              kind: str | None = None, tolerance: float = 3.0) -> bool:
    for stroke in strokes:
        if stroke.horizontal != horizontal:
            continue
        stroke_pos = stroke.y1 if horizontal else stroke.x1
        if abs(stroke_pos - pos) > tolerance:
            continue
        start, end = (stroke.x1, stroke.x2) if horizontal else (stroke.y1, stroke.y2)
        if start <= lo + tolerance and end >= hi - tolerance:
            if kind is None or stroke.kind == kind:
                return True
    return False


def _centre_in(stroke: Stroke, x: float, y: float, w: float, h: float) -> bool:
    cx = (stroke.x1 + stroke.x2) / 2.0
    cy = (stroke.y1 + stroke.y2) / 2.0
    return x <= cx <= x + w and y <= cy <= y + h


def main() -> int:
    image, panels, text_boxes = synthesize()
    strokes = detect_strokes(image, panels, text_boxes=text_boxes)

    print(f"Detected {len(strokes)} strokes")
    for stroke in strokes:
        orientation = "h" if stroke.horizontal else "v"
        if stroke.horizontal:
            geometry = f"y={stroke.y1:6.1f} x={stroke.x1:5.0f}..{stroke.x2:<5.0f}"
        else:
            geometry = f"x={stroke.x1:6.1f} y={stroke.y1:5.0f}..{stroke.y2:<5.0f}"
        print(
            f"  {stroke.kind:9s} {orientation} {geometry} "
            f"w={stroke.width:.0f} {stroke.color} conf={stroke.confidence:.2f}"
        )

    checks = {
        "rounded border top": _has_line(strokes, horizontal=True, pos=30, lo=50, hi=390, kind="border"),
        "rounded border bottom": _has_line(strokes, horizontal=True, pos=270, lo=50, hi=390, kind="border"),
        "rounded border left": _has_line(strokes, horizontal=False, pos=40, lo=44, hi=256, kind="border"),
        "rounded border right": _has_line(strokes, horizontal=False, pos=400, lo=44, hi=256, kind="border"),
        "horizontal divider": _has_line(strokes, horizontal=True, pos=140, lo=60, hi=380),
        "vertical separator": _has_line(strokes, horizontal=False, pos=230, lo=50, hi=250),
        "text underline": _has_line(strokes, horizontal=True, pos=116, lo=60, hi=150, kind="underline"),
        "progress bar": _has_line(strokes, horizontal=True, pos=230, lo=60, hi=220, kind="bar"),
    }

    # No strokes should survive inside the text-noise box or the flat footer.
    text_false = [
        s for s in strokes
        if _centre_in(s, TEXT_BOX[0] - 2, TEXT_BOX[1] - 2, TEXT_BOX[2] + 4, TEXT_BOX[3] + 4)
    ]
    footer_false = [
        s for s in strokes
        if s.horizontal and 296 <= s.y1 <= 324 and s.x1 < 445
    ]
    checks["no text noise"] = not text_false
    checks["no flat-fill duplicate"] = not footer_false

    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if text_false:
        print(f"  text false positives: {len(text_false)}")
    if footer_false:
        print(f"  footer false positives: {len(footer_false)}")

    out_path = Path(tempfile.gettempdir()) / "caption_svg_stroke_probe.png"
    cv2.imwrite(str(out_path), image)
    print(f"probe image: {out_path}")

    failed = [name for name, ok in checks.items() if not ok]
    print(f"Probe: {'PASS' if not failed else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)} checks)")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
