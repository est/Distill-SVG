#!/usr/bin/env python3
"""Visual diff helper for screenshot ↔ SVG renders.

    python3 tools/compare.py tests/2.jpg /tmp/2.png /tmp/2cmp

Writes:
  <prefix>_side.png  side-by-side (original left, render right)
  <prefix>_diff.png  amplified absolute difference heat map
and prints SSIM / PSNR / MAE.

This is a sanity check, not a ground-truth metric: embedded photos and
intentional omissions (shadows, exact fonts) will move the numbers.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import cv2
import numpy as np


def _load(path: str, shape: tuple[int, int]) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"cannot read {path}")
    if (img.shape[1], img.shape[0]) != shape:
        img = cv2.resize(img, shape, interpolation=cv2.INTER_AREA)
    return img


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float64)
    b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float64)
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    k = (11, 11)
    mu_a = cv2.GaussianBlur(a, k, 1.5)
    mu_b = cv2.GaussianBlur(b, k, 1.5)
    va = cv2.GaussianBlur(a * a, k, 1.5) - mu_a * mu_a
    vb = cv2.GaussianBlur(b * b, k, 1.5) - mu_b * mu_b
    cov = cv2.GaussianBlur(a * b, k, 1.5) - mu_a * mu_b
    num = (2 * mu_a * mu_b + c1) * (2 * cov + c2)
    den = (mu_a * mu_a + mu_b * mu_b + c1) * (va + vb + c2)
    return float(np.mean(num / den))


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("original")
    p.add_argument("render")
    p.add_argument("prefix")
    p.add_argument("--width", type=int, default=0, help="scale output width")
    args = p.parse_args(argv)

    a = cv2.imread(args.original)
    if a is None:
        raise SystemExit(f"cannot read {args.original}")
    b = _load(args.render, (a.shape[1], a.shape[0]))

    diff = cv2.absdiff(a, b)
    diff_vis = np.clip(diff.astype(np.float32) * 3.0, 0, 255).astype(np.uint8)
    side = np.hstack([a, np.full((a.shape[0], 8, 3), 255, np.uint8), b])

    prefix = Path(args.prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    if args.width and side.shape[1] > args.width:
        scale = args.width / side.shape[1]
        side = cv2.resize(side, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        diff_vis = cv2.resize(diff_vis, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    cv2.imwrite(str(prefix) + "_side.png", side)
    cv2.imwrite(str(prefix) + "_diff.png", diff_vis)

    mse = float(np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2))
    psnr = 99.0 if mse <= 1e-9 else 10 * math.log10(255.0 ** 2 / mse)
    print(
        {
            "ssim": round(ssim(a, b), 4),
            "psnr": round(psnr, 2),
            "mae": round(float(np.mean(diff)), 2),
            "side": str(prefix) + "_side.png",
            "diff": str(prefix) + "_diff.png",
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
