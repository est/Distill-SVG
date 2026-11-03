"""Optional gradient reconstruction for clean UI fills.

A region is worth turning into an SVG gradient only when its colour is a
smooth function of one parameter: position along a line (linear) or distance
from a point (radial).  Flat fills, text, icons, photos and mesh gradients
violate that and must stay flat or raster.

The module is standalone (numpy/opencv only).  ``Gradient`` geometry is in
crop pixels; :func:`to_svg_defs` maps it onto a placed bounding box.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, degrees, exp, radians, sin, sqrt
from typing import Literal

import cv2
import numpy as np

__all__ = ["Gradient", "detect_gradient", "to_svg_defs"]

_WORK_MAX = 160           # profile analysis resolution
_BINS = 32                # equal-count profile bins
_MIN_CONFIDENCE = 0.60
_MAX_RESIDUAL = 4.0       # per-pixel RMS colour error, 0..255 levels
_MAX_PROFILE_ERROR = 6.0  # max error of the simplified stop polyline
_MAX_HF = 9.0             # high-frequency energy that means "texture"
_MAX_STOPS = 8
_STOP_TOL = 1.25


@dataclass(slots=True)
class Gradient:
    """A detected gradient in crop-local pixel coordinates.

    ``stops`` is ``[(offset in [0, 1], "#rrggbb"), ...]``.  Linear gradients
    carry ``angle_deg`` and ``(x1, y1) -> (x2, y2)``; radial ones carry
    ``(cx, cy)`` and ``r``.  Unused fields are ``None``.
    """

    kind: Literal["linear", "radial"]
    stops: list[tuple[float, str]]
    confidence: float
    residual: float
    width: int
    height: int
    angle_deg: float | None = None
    x1: float | None = None
    y1: float | None = None
    x2: float | None = None
    y2: float | None = None
    cx: float | None = None
    cy: float | None = None
    r: float | None = None


@dataclass(slots=True)
class _Field:
    coords: np.ndarray        # (N, 2) working-scale x/y of valid pixels
    colors: np.ndarray        # (N, 3) smoothed float32 RGB
    shape: tuple[int, int]    # working (h, w)
    width: int                # original crop dimensions
    height: int
    scale_x: float
    scale_y: float
    hf: float
    coverage: float


@dataclass(slots=True)
class _Fit:
    ratio: float
    residual: float
    param: np.ndarray
    lo: float
    hi: float
    center: tuple[float, float] | None = None
    direction: np.ndarray | None = None


def _hex(rgb: np.ndarray) -> str:
    r, g, b = (int(np.clip(round(float(v)), 0, 255)) for v in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def _prepare(crop: np.ndarray, mask: np.ndarray | None) -> _Field | None:
    a = np.asarray(crop)
    if a.ndim == 2:
        a = cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
    elif a.ndim == 3 and a.shape[2] == 4:
        a = a[:, :, :3]
    if a.ndim != 3 or a.shape[2] != 3:
        raise ValueError("crop must be a BGR uint8 image (H, W, 3)")
    if a.dtype != np.uint8:
        a = np.clip(a, 0, 255).astype(np.uint8)

    h0, w0 = a.shape[:2]
    if min(h0, w0) < 8:
        return None
    factor = min(1.0, _WORK_MAX / float(max(h0, w0)))
    w, h = max(8, int(round(w0 * factor))), max(8, int(round(h0 * factor)))
    rgb = cv2.cvtColor(a, cv2.COLOR_BGR2RGB)
    if (w, h) != (w0, h0):
        rgb = cv2.resize(rgb, (w, h), interpolation=cv2.INTER_AREA)
    raw = rgb.astype(np.float32)
    smooth = cv2.GaussianBlur(raw, (0, 0), 0.8)

    if mask is None:
        valid = np.ones((h, w), dtype=bool)
    else:
        m = np.asarray(mask)
        if m.ndim != 2:
            raise ValueError("mask must be a 2-D array")
        if m.dtype == bool:
            valid0 = m.copy()
        else:
            mf = m.astype(np.float32)
            valid0 = mf > (0.5 if (mf.size and mf.max() <= 1.0) else 127.0)
        if valid0.shape != (h0, w0):
            valid0 = cv2.resize(
                valid0.astype(np.uint8), (w0, h0), interpolation=cv2.INTER_NEAREST
            ).astype(bool)
        valid = cv2.resize(
            valid0.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST
        ).astype(bool)
        if valid.sum() > 200:  # drop anti-aliased mask borders
            valid = cv2.erode(
                valid.astype(np.uint8), np.ones((3, 3), np.uint8)
            ).astype(bool)

    ys, xs = np.nonzero(valid)
    if ys.size < 64:
        return None
    colors = smooth[ys, xs]
    diff = raw[ys, xs] - colors
    return _Field(
        coords=np.stack((xs, ys), axis=1).astype(np.float32),
        colors=colors,
        shape=(h, w),
        width=w0,
        height=h0,
        scale_x=w0 / float(w),
        scale_y=h0 / float(h),
        hf=float(np.sqrt(np.mean(diff * diff))),
        coverage=ys.size / float(w * h),
    )


def _within_variance(
    param: np.ndarray, colors: np.ndarray, total_sse: float
) -> tuple[float, float]:
    """Equal-count binning along ``param``; return (SSE ratio, RMS residual)."""
    order = np.argsort(param, kind="stable")
    edges = np.linspace(0, param.size, _BINS + 1).astype(np.int64)
    within = 0.0
    for a, b in zip(edges[:-1], edges[1:]):
        seg = colors[order[a:b]]
        mu = seg.mean(axis=0)
        within += float(((seg - mu) ** 2).sum())
    return within / max(total_sse, 1e-9), sqrt(within / (3.0 * param.size))


def _profile(param: np.ndarray, colors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Equal-count bin means/medians: a robust 1-D colour profile."""
    order = np.argsort(param, kind="stable")
    edges = np.linspace(0, param.size, _BINS + 1).astype(np.int64)
    centers = np.empty(_BINS, dtype=np.float32)
    medians = np.empty((_BINS, 3), dtype=np.float32)
    for i, (a, b) in enumerate(zip(edges[:-1], edges[1:])):
        idx = order[a:b]
        centers[i] = param[idx].mean()
        medians[i] = np.median(colors[idx], axis=0)
    return centers, medians


def _regression_direction(
    coords: np.ndarray, colors: np.ndarray
) -> tuple[np.ndarray, float] | None:
    """Whitened rank-1 direction of colour change and its coherence."""
    x = coords - coords.mean(axis=0)
    c = colors - colors.mean(axis=0)
    try:
        b = np.linalg.solve(
            (x.T @ x) / x.shape[0] + np.eye(2) * 1e-6, (x.T @ c) / x.shape[0]
        )
        u, s, _ = np.linalg.svd(b, full_matrices=False)
    except np.linalg.LinAlgError:
        return None
    if s[0] < 1e-9:
        return None
    return u[:, 0].astype(np.float32), float(s[1] / s[0])


def _fit_linear(
    coords: np.ndarray, colors: np.ndarray, total_sse: float, w: int, h: int
) -> _Fit:
    def candidate(angle: float) -> tuple[float, float, float, np.ndarray, np.ndarray]:
        a = radians(angle)
        d = np.array([cos(a), sin(a)], dtype=np.float32)
        t = coords @ d
        ratio, residual = _within_variance(t, colors, total_sse)
        return residual, ratio, angle, d, t

    angles = np.arange(0.0, 180.0, 11.25)
    best = candidate(float(angles[0]))
    for angle in angles[1:]:
        cand = candidate(float(angle))
        if cand[0] < best[0] - 1e-9:
            best = cand

    step = 11.25
    for _ in range(3):
        step *= 0.25
        base = best[2]
        for k in (-2, -1, 1, 2):
            cand = candidate(float((base + k * step) % 180.0))
            if cand[0] < best[0] - 1e-9:
                best = cand

    # Binning makes the angular score flat; refine with the continuous,
    # rank-1 colour regression when it agrees with the coarse winner.
    regression = _regression_direction(coords, colors)
    if regression is not None and regression[1] < 0.35:
        d_pca = regression[0]
        if float(d_pca @ best[3]) < 0.0:
            d_pca = -d_pca
        diff = degrees(float(np.arccos(np.clip(d_pca @ best[3], -1.0, 1.0))))
        if diff < 20.0:
            t = coords @ d_pca
            ratio, residual = _within_variance(t, colors, total_sse)
            if residual <= best[0] * 1.05 + 1e-6:
                angle = degrees(float(np.arctan2(d_pca[1], d_pca[0]))) % 180.0
                best = (residual, ratio, angle, d_pca, t)

    residual, ratio, _, d, t = best
    corners = np.array(
        [[0, 0], [w - 1, 0], [0, h - 1], [w - 1, h - 1]], dtype=np.float32
    )
    proj = corners @ d
    return _Fit(
        ratio=ratio,
        residual=residual,
        param=t,
        lo=float(proj.min()),
        hi=float(proj.max()),
        direction=d,
    )


def _fit_radial(
    coords: np.ndarray, colors: np.ndarray, total_sse: float, w: int, h: int
) -> _Fit:
    def candidate(
        center: tuple[float, float]
    ) -> tuple[float, float, tuple[float, float], np.ndarray]:
        dist = np.hypot(coords[:, 0] - center[0], coords[:, 1] - center[1])
        ratio, residual = _within_variance(dist, colors, total_sse)
        return residual, ratio, center, dist

    coarse = [(float(fx * w), float(fy * h))
              for fx in np.linspace(-0.25, 1.25, 7)
              for fy in np.linspace(-0.25, 1.25, 7)]
    best = candidate(coarse[0])
    for center in coarse[1:]:
        cand = candidate(center)
        if cand[0] < best[0] - 1e-9:
            best = cand

    for step_x, step_y in (
        (0.125 * w, 0.125 * h),
        (0.03125 * w, 0.03125 * h),
        (0.0078125 * w, 0.0078125 * h),
    ):
        base = best[2]
        for ox in (-2, -1, 0, 1, 2):
            for oy in (-2, -1, 0, 1, 2):
                if ox or oy:
                    cand = candidate((base[0] + ox * step_x, base[1] + oy * step_y))
                    if cand[0] < best[0] - 1e-9:
                        best = cand

    residual, ratio, center, dist = best
    corners = np.array(
        [[0, 0], [w - 1, 0], [0, h - 1], [w - 1, h - 1]], dtype=np.float32
    )
    hi = float(np.hypot(corners[:, 0] - center[0], corners[:, 1] - center[1]).max())
    return _Fit(ratio, residual, dist, 0.0, max(hi, 1e-6), center=center)


def _edge_value(u: np.ndarray, rgb: np.ndarray, at: float) -> np.ndarray:
    """Evaluate a local linear fit at an end offset (robust extrapolation)."""
    k = min(4, int(u.size))
    if k < 2:
        return rgb[0].copy() if at < 0.5 else rgb[-1].copy()
    xs = u[:k] if at <= u[0] else u[-k:]
    ys = rgb[:k] if at <= u[0] else rgb[-k:]
    coef, *_ = np.linalg.lstsq(np.stack((np.ones(k), xs), axis=1), ys, rcond=None)
    return np.clip(coef[0] + coef[1] * at, 0.0, 255.0)


def _profile_points(u: np.ndarray, rgb: np.ndarray) -> list[tuple[float, np.ndarray]]:
    """Dense profile plus extrapolated 0/1 endpoints."""
    points = [(float(x), color) for x, color in zip(u, rgb)]
    if u[0] > 1e-4:
        points.insert(0, (0.0, _edge_value(u, rgb, 0.0)))
    if u[-1] < 1.0 - 1e-4:
        points.append((1.0, _edge_value(u, rgb, 1.0)))
    return points


def _simplify(
    points: list[tuple[float, np.ndarray]], tol: float, max_points: int
) -> list[int]:
    """Greedy max-error polyline simplification; endpoints are kept."""
    pts = np.array([[o, *rgb] for o, rgb in points], dtype=np.float32)
    keep = [0, len(pts) - 1]
    while len(keep) < max_points:
        worst_err, worst_idx = -1.0, None
        for a, b in zip(keep[:-1], keep[1:]):
            if b <= a + 1:
                continue
            idx = np.arange(a + 1, b)
            t = (pts[idx, 0] - pts[a, 0]) / max(float(pts[b, 0] - pts[a, 0]), 1e-9)
            pred = pts[a, 1:] + t[:, None] * (pts[b, 1:] - pts[a, 1:])
            err = np.sqrt(((pts[idx, 1:] - pred) ** 2).sum(axis=1))
            j = int(np.argmax(err))
            if float(err[j]) > worst_err:
                worst_err, worst_idx = float(err[j]), int(idx[j])
        if worst_idx is None or worst_err <= tol:
            break
        keep.append(worst_idx)
        keep.sort()
    return keep


def _confidence(
    ratio: float, residual: float, hf: float, rms: float, coverage: float
) -> float:
    fit = sqrt(max(0.0, 1.0 - min(ratio, 1.0)))
    rq = exp(-((residual / 3.0) ** 2))
    hq = exp(-((hf / 2.0) ** 2))
    vq = float(np.clip(rms / 12.0, 0.0, 1.0))
    cq = float(np.clip(coverage / 0.35, 0.0, 1.0))
    return float(fit * (0.25 + 0.75 * rq) * (0.65 + 0.35 * hq)
                 * (0.60 + 0.40 * vq) * (0.85 + 0.15 * cq))


def detect_gradient(
    crop: np.ndarray,
    mask: np.ndarray | None = None,
    *,
    min_confidence: float = _MIN_CONFIDENCE,
) -> Gradient | None:
    """Detect a clean linear or radial gradient in a BGR crop.

    Args:
        crop: ``uint8`` BGR image (H, W, 3).
        mask: optional boolean/0-255 mask of usable pixels (same H/W).
        min_confidence: rejection threshold in ``[0, 1]``.

    Returns:
        A :class:`Gradient` in crop-local pixels, or ``None`` for flat fills,
        text/icons, photos, mesh gradients and other unsafe regions.
    """
    field = _prepare(crop, mask)
    if field is None:
        return None

    colors = field.colors
    mean = colors.mean(axis=0)
    total_sse = float(((colors - mean) ** 2).sum())
    rms = sqrt(total_sse / (3.0 * colors.shape[0]))
    span = float(np.max(np.percentile(colors, 98, axis=0)
                        - np.percentile(colors, 2, axis=0)))
    if rms < 1.2 or span < 5.0 or field.hf > _MAX_HF:
        return None

    h, w = field.shape
    linear = _fit_linear(field.coords, colors, total_sse, w, h)
    radial: _Fit | None = None
    # A radial fill cannot look linear (colour varies in every direction), so
    # skip the expensive centre search when linear is already convincing.
    if linear.residual > 1.2 or linear.ratio > 0.02:
        radial = _fit_radial(field.coords, colors, total_sse, w, h)
    chosen = linear
    if radial is not None and radial.residual < linear.residual * 0.80:
        chosen = radial
    if chosen.residual > _MAX_RESIDUAL:
        return None

    lo, hi = chosen.lo, chosen.hi
    centers, medians = _profile(chosen.param, colors)
    u_profile = np.clip((centers - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    points = _profile_points(u_profile, medians)
    sample_span = float(np.max(np.ptp(np.stack([c for _, c in points]), axis=0)))
    keep = _simplify(points, max(_STOP_TOL, 0.01 * sample_span), _MAX_STOPS)
    offsets = np.array([points[i][0] for i in keep], dtype=np.float32)
    stop_rgb = np.stack([points[i][1] for i in keep])
    if len(keep) < 2 or float(np.max(np.ptp(stop_rgb, axis=0))) < 4.0:
        return None

    # Drop redundant flat tails: SVG pads beyond the first/last stop anyway.
    while len(offsets) > 2 and np.max(np.abs(stop_rgb[-1] - stop_rgb[-2])) < 1.5:
        offsets, stop_rgb = offsets[:-1], stop_rgb[:-1]
    while len(offsets) > 2 and np.max(np.abs(stop_rgb[0] - stop_rgb[1])) < 1.5:
        offsets, stop_rgb = offsets[1:], stop_rgb[1:]

    # A flat area with one soft divider is not a gradient: demand that the
    # meaningful colour changes occupy a non-trivial part of the axis.
    change_span = float(sum(
        offsets[i] - offsets[i - 1]
        for i in range(1, len(offsets))
        if np.max(np.abs(stop_rgb[i] - stop_rgb[i - 1])) > 2.0
    ))
    if change_span < 0.12:
        return None

    profile_err = float(np.max(np.sqrt(np.sum(
        (medians - np.stack([np.interp(u_profile, offsets, stop_rgb[:, c])
                             for c in range(3)], axis=1)) ** 2, axis=1))))
    if profile_err > _MAX_PROFILE_ERROR:
        return None

    u_all = np.clip((chosen.param - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    predicted = np.stack([np.interp(u_all, offsets, stop_rgb[:, c])
                          for c in range(3)], axis=1)
    residual = float(np.sqrt(np.mean(np.sum((colors - predicted) ** 2, axis=1))))
    confidence = _confidence(chosen.ratio, residual, field.hf, rms, field.coverage)
    if residual > _MAX_RESIDUAL or confidence < min_confidence:
        return None

    stops = [(round(float(o), 4), _hex(rgb)) for o, rgb in zip(offsets, stop_rgb)]
    common = {
        "stops": stops,
        "confidence": confidence,
        "residual": residual,
        "width": field.width,
        "height": field.height,
    }
    if chosen.direction is not None:
        d = chosen.direction
        p0, p1 = d * lo, d * hi
        x1, y1 = float(p0[0] * field.scale_x), float(p0[1] * field.scale_y)
        x2, y2 = float(p1[0] * field.scale_x), float(p1[1] * field.scale_y)
        return Gradient(kind="linear", angle_deg=degrees(np.arctan2(y2 - y1, x2 - x1)) % 180.0,
                        x1=x1, y1=y1, x2=x2, y2=y2, **common)

    cx, cy = chosen.center or (0.0, 0.0)
    scale = 0.5 * (field.scale_x + field.scale_y)
    return Gradient(kind="radial", cx=float(cx * field.scale_x),
                    cy=float(cy * field.scale_y), r=float(hi * scale), **common)


def to_svg_defs(
    grad_id: str, grad: Gradient, bbox: tuple[float, float, float, float]
) -> tuple[str, str]:
    """Emit an SVG gradient element and its ``fill`` reference.

    ``bbox`` is ``(x, y, w, h)`` where the crop is placed.  The gradient is
    defined in crop pixels and mapped with ``gradientTransform``, so the
    geometry stays correct for non-square placements.
    """
    bx, by, bw, bh = (float(v) for v in bbox)
    transform = (f'gradientTransform="matrix({bw / max(1.0, grad.width):.6g} 0 0 '
                 f'{bh / max(1.0, grad.height):.6g} {bx:.6g} {by:.6g})"')
    stops = "".join(f'<stop offset="{o:.4f}" stop-color="{c}"/>' for o, c in grad.stops)
    if grad.kind == "linear":
        if None in (grad.x1, grad.y1, grad.x2, grad.y2):
            raise ValueError("linear gradient is missing endpoints")
        body = (f'<linearGradient id="{grad_id}" gradientUnits="userSpaceOnUse" '
                f'x1="{grad.x1:.4f}" y1="{grad.y1:.4f}" x2="{grad.x2:.4f}" '
                f'y2="{grad.y2:.4f}" {transform}>{stops}</linearGradient>')
    else:
        if None in (grad.cx, grad.cy, grad.r):
            raise ValueError("radial gradient is missing centre/radius")
        body = (f'<radialGradient id="{grad_id}" gradientUnits="userSpaceOnUse" '
                f'cx="{grad.cx:.4f}" cy="{grad.cy:.4f}" r="{grad.r:.4f}" '
                f'{transform}>{stops}</radialGradient>')
    return body, f"url(#{grad_id})"
