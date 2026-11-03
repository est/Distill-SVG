"""Image preparation: remove screenshot dither/steganographic noise and
reduce the image to a small, flat palette.

The important observation from real screenshots (especially Android apps) is
that flat UI areas are rarely flat in the file.  They contain:

  * a per-pixel +/- 1..2 level dither (sometimes a watermark carrier),
  * JPEG blocking/ringing,
  * a 2x nearest-neighbour upscale of an already-low-resolution capture.

Naive palette extraction turns that noise into hundreds of "colours" and
thousands of tiny shapes.  We deliberately normalise it before any layout
analysis.  This is also why the output SVG cannot carry the original hidden
payload: only a small palette and reconstructed geometry survive.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Prepared:
    original: np.ndarray          # BGR, full resolution
    flat: np.ndarray              # BGR, analysis scale, denoised
    quantized: np.ndarray         # BGR, analysis scale, palette colours
    labels: np.ndarray            # int32, palette label per pixel
    centers_rgb: np.ndarray       # (K, 3) RGB
    scale: int                    # original / analysis
    width: int
    height: int

    @property
    def aw(self) -> int:
        return self.flat.shape[1]

    @property
    def ah(self) -> int:
        return self.flat.shape[0]


def _conditional_median(bgr: np.ndarray, radius: int = 1, max_range: int = 10) -> np.ndarray:
    """Median-filter only locally flat pixels.

    A normal median filter destroys one-pixel text strokes and icon details.
    A normal blur destroys everything.  We only replace a pixel when its 3x3
    neighbourhood is already visually flat, which is exactly where dither
    lives.
    """
    k = 2 * radius + 1
    median = cv2.medianBlur(bgr, k)
    kernel = np.ones((k, k), np.uint8)
    hi = cv2.dilate(bgr, kernel)
    lo = cv2.erode(bgr, kernel)
    spread = (hi.astype(np.int16) - lo.astype(np.int16)).max(axis=2)
    out = bgr.copy()
    flat = spread <= max_range
    out[flat] = median[flat]
    return out


def palette(bgr: np.ndarray, k: int = 14, seed: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """K-means in Lab.  Returns (quantized BGR, labels, centres RGB)."""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    samples = lab.reshape(-1, 3).astype(np.float32)
    # A deterministic subsample keeps this fast on 2x/3x phone screenshots.
    if len(samples) > 250_000:
        rng = np.random.default_rng(seed)
        samples = samples[rng.choice(len(samples), 250_000, replace=False)]
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.35)
    _, _, centers = cv2.kmeans(
        samples, k, None, criteria, 8, cv2.KMEANS_PP_CENTERS
    )
    centers = np.uint8(np.clip(centers, 0, 255))

    lab_px = lab.reshape(-1, 3).astype(np.float32)
    # Assign every pixel to the nearest centre (rather than re-running kmeans).
    # Chunk the distance matrix so a 4K/5K screenshot does not allocate a
    # multi-hundred-megabyte temporary.
    labels_flat = np.empty(len(lab_px), dtype=np.int32)
    chunk = 200_000
    for start in range(0, len(lab_px), chunk):
        part = lab_px[start:start + chunk]
        d = ((part[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels_flat[start:start + chunk] = d.argmin(axis=1)
    labels = labels_flat.reshape(lab.shape[:2])
    quantized = centers[labels].reshape(lab.shape)
    quantized_bgr = cv2.cvtColor(quantized, cv2.COLOR_LAB2BGR)
    centers_rgb = cv2.cvtColor(centers.reshape(1, -1, 3), cv2.COLOR_LAB2RGB).reshape(-1, 3)
    return quantized_bgr, labels, centers_rgb


def prepare(path: str, analysis_scale: int = 2, k: int = 14) -> Prepared:
    original = cv2.imread(path, cv2.IMREAD_COLOR)
    if original is None:
        raise FileNotFoundError(path)

    h, w = original.shape[:2]
    scale = max(1, int(analysis_scale))
    if min(h, w) // scale < 80:
        scale = 1

    if scale > 1:
        small = cv2.resize(original, (w // scale, h // scale), interpolation=cv2.INTER_AREA)
    else:
        small = original

    flat = _conditional_median(small)
    quantized, labels, centers_rgb = palette(flat, k=k)
    return Prepared(
        original=original,
        flat=flat,
        quantized=quantized,
        labels=labels,
        centers_rgb=centers_rgb,
        scale=scale,
        width=w,
        height=h,
    )


def rgb_to_hex(rgb) -> str:
    r, g, b = (int(round(float(v))) for v in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def color_distance(a, b) -> float:
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    return float(np.sqrt(((a - b) ** 2).sum()))
