"""Synthetic probe for :mod:`distill_svg.gradients`.

Run from anywhere::

    python tools/gradient_probe.py

No files are written.  The cases exercise the happy paths plus the four
regions that must be rejected: flat fill, photo-like texture, text and a
two-dimensional (mesh) gradient.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from distill_svg.gradients import detect_gradient, to_svg_defs  # noqa: E402


def _bgr(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)


def linear(w: int = 280, h: int = 180, angle_deg: float = 27.0) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a = np.deg2rad(angle_deg)
    t = xx * np.cos(a) + yy * np.sin(a)
    t = (t - t.min()) / (t.max() - t.min())
    c0 = np.array([28.0, 92.0, 214.0])
    c1 = np.array([238.0, 74.0, 126.0])
    rgb = c0 + t[..., None] * (c1 - c0)
    rng = np.random.default_rng(1)
    rgb += rng.normal(0.0, 0.55, rgb.shape)
    return _bgr(rgb)


def radial(w: int = 260, h: int = 220) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = 0.36 * w, 0.42 * h
    r = np.hypot(xx - cx, yy - cy)
    t = np.clip(r / (0.85 * max(w, h)), 0.0, 1.0)
    c0 = np.array([250.0, 246.0, 232.0])
    c1 = np.array([34.0, 86.0, 178.0])
    rgb = c0 + t[..., None] * (c1 - c0)
    rng = np.random.default_rng(2)
    rgb += rng.normal(0.0, 0.5, rgb.shape)
    return _bgr(rgb)


def flat(w: int = 200, h: int = 140) -> np.ndarray:
    rng = np.random.default_rng(3)
    rgb = np.full((h, w, 3), [238.0, 235.0, 231.0], dtype=np.float32)
    rgb += rng.integers(-1, 2, rgb.shape)
    return _bgr(rgb)


def photo(w: int = 240, h: int = 180) -> np.ndarray:
    rng = np.random.default_rng(4)
    small = rng.integers(0, 256, (14, 18, 3), dtype=np.uint8)
    base = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC).astype(np.float32)
    base += rng.normal(0.0, 7.0, base.shape)
    return _bgr(base)


def text_region(w: int = 260, h: int = 120) -> np.ndarray:
    rgb = np.full((h, w, 3), 250.0, dtype=np.float32)
    ink = (26.0, 28.0, 34.0)
    for y in range(14, h - 10, 22):
        cv2.putText(
            rgb,
            "Sample text 0123",
            (10, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            ink,
            1,
            cv2.LINE_AA,
        )
    return _bgr(rgb)


def mesh(w: int = 240, h: int = 180) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    rgb = np.empty((h, w, 3), dtype=np.float32)
    rgb[:, :, 0] = 30.0 + 210.0 * xx / (w - 1)
    rgb[:, :, 1] = 40.0 + 190.0 * yy / (h - 1)
    rgb[:, :, 2] = 120.0
    return _bgr(rgb)


def masked_linear() -> tuple[np.ndarray, np.ndarray]:
    crop = linear(300, 200, angle_deg=90.0)
    mask = np.ones(crop.shape[:2], dtype=np.uint8) * 255
    mask[:20, :] = 0
    mask[-20:, :] = 0
    mask[:, :25] = 0
    return crop, mask


def show(name: str, crop: np.ndarray, mask: np.ndarray | None = None, expect: str = "") -> bool:
    grad = detect_gradient(crop, mask)
    if grad is None:
        print(f"{name:14s} -> None")
        return expect == "none"
    geo = (
        f"angle={grad.angle_deg:.1f}"
        if grad.kind == "linear"
        else f"cx={grad.cx:.1f} cy={grad.cy:.1f} r={grad.r:.1f}"
    )
    print(
        f"{name:14s} -> {grad.kind:6s} {geo:34s} "
        f"conf={grad.confidence:.3f} residual={grad.residual:.2f} "
        f"stops={len(grad.stops)} {grad.stops}"
    )
    defs, fill = to_svg_defs(f"{name}g", grad, (10.0, 20.0, 400.0, 300.0))
    print(f"{'':14s}    fill={fill} defs={defs[:96]}...")
    return expect == grad.kind


def main() -> int:
    ok = True
    ok &= show("linear", linear(), expect="linear")
    ok &= show("radial", radial(), expect="radial")
    ok &= show("masked", *masked_linear(), expect="linear")
    ok &= show("flat", flat(), expect="none")
    ok &= show("photo", photo(), expect="none")
    ok &= show("text", text_region(), expect="none")
    ok &= show("mesh", mesh(), expect="none")
    print("\nPROBE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
