"""Text fitting: turn OCR boxes into SVG text with a plausible font, size,
baseline and colour.

OCR boxes are padded and their height says more about the OCR engine than the
font.  We therefore:

  1. recover the *ink box* from the pixels (not the OCR box),
  2. fit the font size from the ink width,
  3. derive the baseline from the font's ascent/ink-bottom relationship,
  4. pick a font/weight by comparing rendered glyph masks with the screenshot.

The last step is optional when no candidate fonts are available; the geometry
still works with a sensible fallback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .ocr import OcrLine


@dataclass
class FontCandidate:
    family: str
    weight: int
    path: str
    index: int = 0

    @property
    def key(self) -> str:
        return f"{self.family}:{self.weight}"


@dataclass
class TextItem:
    text: str
    x: float
    baseline: float
    size: float
    family: str
    weight: int
    fill: str
    score: float = 0.0
    confidence: float = 1.0
    ink_box: tuple[float, float, float, float] = (0, 0, 0, 0)

    def to_dict(self):
        return {
            "text": self.text,
            "x": round(self.x, 2),
            "y": round(self.baseline, 2),
            "size": round(self.size, 2),
            "family": self.family,
            "weight": self.weight,
            "fill": self.fill,
            "confidence": round(self.confidence, 2),
        }


def _font_dirs() -> list[Path]:
    dirs = []
    env = os.environ.get("CAPTION_SVG_FONT_DIRS")
    if env:
        dirs.extend(Path(p) for p in env.split(os.pathsep) if p)
    root = Path(__file__).resolve().parent.parent
    dirs.append(root / "assets" / "fonts")
    return [d for d in dirs if d.exists()]


def _find_font(*patterns: str) -> str | None:
    for base in ("/System/Library/Fonts", "/System/Library/Fonts/Supplemental",
                 "/Library/Fonts", str(Path.home() / "Library/Fonts")):
        for pattern in patterns:
            hits = sorted(Path(base).glob(pattern))
            if hits:
                return str(hits[0])
    return None


def default_candidates() -> list[FontCandidate]:
    candidates: list[FontCandidate] = []

    # macOS: PingFang SC is the system Simplified Chinese UI font.
    ping = None
    asset_root = Path("/System/Library/AssetsV2")
    if asset_root.exists():
        hits = sorted(asset_root.glob("com_apple_MobileAsset_Font*/*/AssetData/PingFang.ttc"))
        if hits:
            ping = str(hits[-1])
    if ping is None:
        ping = _find_font("PingFang.ttc")
    if ping:
        candidates += [
            FontCandidate("PingFang SC", 400, ping, 3),
            FontCandidate("PingFang SC", 500, ping, 7),
            FontCandidate("PingFang SC", 600, ping, 11),
        ]

    # Open-source CJK fonts: put them in assets/fonts or CAPTION_SVG_FONT_DIRS.
    for d in _font_dirs():
        for path in sorted(list(d.glob("*NotoSans*SC*.ttf")) + list(d.glob("*NotoSans*SC*.otf"))):
            name = path.stem.lower()
            weight = (
                700 if "700" in name or "bold" in name
                else 600 if "600" in name or "semibold" in name
                else 500 if "500" in name or "medium" in name
                else 400
            )
            candidates.append(FontCandidate("Noto Sans SC", weight, str(path), 0))
        for path in sorted(d.glob("*SourceHanSans*SC*")):
            candidates.append(FontCandidate("Source Han Sans SC", 400, str(path), 0))

    # Linux distributions.
    for path in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
    ):
        if os.path.exists(path):
            candidates.append(FontCandidate("Noto Sans CJK SC", 500 if "Medium" in path else 400, path, 0))

    # Windows.
    win = Path("C:/Windows/Fonts")
    if win.exists():
        for name, weight in (("msyh.ttc", 400), ("msyhbd.ttc", 700)):
            if (win / name).exists():
                candidates.append(FontCandidate("Microsoft YaHei", weight, str(win / name), 0))

    # Latin-only fallback.
    for family, path in (("Helvetica", _find_font("Helvetica.ttc")), ("Arial", _find_font("Arial.ttf"))):
        if path:
            candidates.append(FontCandidate(family, 400, path, 0))

    # De-duplicate by (family, weight), prefer first.
    seen = set()
    out = []
    for c in candidates:
        if c.key not in seen:
            seen.add(c.key)
            out.append(c)
    return out


def _hex(rgb) -> str:
    r, g, b = (int(round(float(v))) for v in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def _ink_box(img: np.ndarray, box: tuple[float, float, float, float]) -> tuple[np.ndarray, tuple[int, int, int, int], str, tuple[int, int]]:
    """Return (mask, (x0,y0,x1,y1), text colour) in image coordinates."""
    h, w = img.shape[:2]
    x, y, bw, bh = box
    pad = 2
    x0 = max(0, int(np.floor(x)) - pad)
    y0 = max(0, int(np.floor(y)) - pad)
    x1 = min(w, int(np.ceil(x + bw)) + pad)
    y1 = min(h, int(np.ceil(y + bh)) + pad)
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return np.zeros((1, 1), bool), (int(x), int(y), int(x + bw), int(y + bh)), "#111111", (0, 0)

    a = crop.astype(np.float32)
    bg = np.median(a.reshape(-1, 3), axis=0)
    dist = np.sqrt(((a - bg) ** 2).sum(axis=2))
    lum = a.mean(axis=2)
    bg_lum = float(bg.mean())
    mask = (dist > 38) | (lum < bg_lum - 32)
    # Remove single-pixel noise from dither/JPEG, then keep reasonably sized
    # components.  Text strokes at 1x can be one pixel wide, so only remove
    # isolated pixels, not thin strokes.
    m8 = mask.astype(np.uint8)
    n, cc, stats, _ = cv2.connectedComponentsWithStats(m8, 8)
    clean = np.zeros_like(mask)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 2:
            clean[cc == i] = True
    mask = clean

    # Restrict to the OCR box plus one pixel: expanded border may contain a
    # card edge, which is not text.
    ix0 = max(0, int(np.floor(x)) - 1 - x0)
    iy0 = max(0, int(np.floor(y)) - 1 - y0)
    ix1 = min(crop.shape[1], int(np.ceil(x + bw)) + 1 - x0)
    iy1 = min(crop.shape[0], int(np.ceil(y + bh)) + 1 - y0)
    inner = np.zeros_like(mask)
    inner[iy0:iy1, ix0:ix1] = True
    mask &= inner

    ys, xs = np.where(mask)
    if len(xs) == 0:
        return mask, (int(x), int(y), int(x + bw), int(y + bh)), "#111111", (x0, y0)

    ix0, iy0, ix1, iy1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    px = crop[ys, xs]
    # Colour from the darkest/most saturated core of the ink.
    lum_px = px.mean(axis=1)
    order = np.argsort(lum_px)
    core = px[order[: max(3, len(order) // 5)]]
    color = _hex(np.median(core, axis=0)[::-1])
    return mask, (x0 + ix0, y0 + iy0, x0 + ix1 + 1, y0 + iy1 + 1), color, (x0, y0)


def _render_mask(text: str, font: ImageFont.FreeTypeFont, pad: int = 4):
    width = max(8, int(font.getlength(text)) + 2 * pad)
    ascent, descent = font.getmetrics()
    height = ascent + descent + 2 * pad
    canvas = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(canvas)
    draw.text((pad, pad + ascent), text, font=font, fill=255, anchor="ls")
    arr = np.array(canvas) > 127
    ys, xs = np.where(arr)
    if len(xs) == 0:
        return arr, (0, 0, 0, 0)
    return arr, (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def _fit_candidate(
    text: str,
    crop: np.ndarray,
    target_mask: np.ndarray,
    target_box: tuple[int, int, int, int],
    crop_origin: tuple[int, int],
    cand: FontCandidate,
) -> tuple[float, float, float, float] | None:
    """Return (score, size, x, baseline) for one font candidate."""
    tx0, ty0, tx1, ty1 = target_box
    cx0, cy0 = crop_origin
    tw = tx1 - tx0
    th = ty1 - ty0
    if tw <= 0 or th <= 0:
        return None

    try:
        f100 = ImageFont.truetype(cand.path, 100, index=cand.index)
        bbox100 = f100.getbbox(text)
    except Exception:
        return None
    if bbox100[2] <= bbox100[0]:
        return None
    size = tw * 100.0 / (bbox100[2] - bbox100[0])
    if not (4 <= size <= 400):
        return None
    size_i = max(4, int(round(size)))
    try:
        font = ImageFont.truetype(cand.path, size_i, index=cand.index)
    except Exception:
        return None
    ascent, _ = font.getmetrics()
    bbox = font.getbbox(text)
    if bbox[2] <= bbox[0]:
        return None

    # Map rendered ink box to the screenshot ink box.
    pad = 4
    full_mask, (rx0, ry0, rx1, ry1) = _render_mask(text, font, pad=pad)
    if rx1 <= rx0 or ry1 <= ry0:
        return None
    x = tx0 - (rx0 - pad)  # render origin is 0; ink starts at rx0
    baseline = ty1 - (ry1 - pad - ascent)

    # Compare masks after aligning ink boxes, with one pixel of tolerance.
    rmask = full_mask[ry0:ry1, rx0:rx1]
    if rmask.size == 0:
        return None
    r_density = float(rmask.mean())
    target = target_mask[ty0 - cy0:ty1 - cy0, tx0 - cx0:tx1 - cx0].astype(bool)
    t_density = float(target.mean())
    if target.size == 0:
        return None
    rmask = cv2.resize(rmask.astype(np.uint8), (tw, th), interpolation=cv2.INTER_AREA) > 0.35
    if target.shape != rmask.shape:
        return None
    k = np.ones((3, 3), np.uint8)
    a = cv2.dilate(rmask.astype(np.uint8), k).astype(bool)
    b = cv2.dilate(target.astype(np.uint8), k).astype(bool)
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    iou = float(inter / union) if union else 0.0
    # The screenshot mask has an antialiasing halo, so its density runs a
    # little high.  Matching density prevents the IoU from always choosing the
    # boldest candidate.
    score = iou - 1.8 * abs(r_density - t_density * 0.84)
    return score, float(size_i), float(x), float(baseline)


def fit_text_lines(
    img: np.ndarray,
    lines: list[OcrLine],
    candidates: list[FontCandidate] | None = None,
    fast: bool = False,
) -> tuple[list[TextItem], str]:
    candidates = candidates or default_candidates()
    if not candidates:
        return [], "sans-serif"

    if fast:
        # Quick preview: no per-line font voting / mask IoU.  Geometry is the
        # same (ink width + font metrics); weight is a conservative default.
        cand = candidates[0]
        items: list[TextItem] = []
        for line in lines:
            text = line.text.strip()
            if not text:
                continue
            mask, ibox, color, _origin = _ink_box(img, line.box)
            tw = ibox[2] - ibox[0]
            th = ibox[3] - ibox[1]
            if tw < 2 or th < 2:
                continue
            try:
                f100 = ImageFont.truetype(cand.path, 100, index=cand.index)
                b100 = f100.getbbox(text)
                if b100[2] <= b100[0]:
                    continue
                size = max(4, int(round(tw * 100.0 / (b100[2] - b100[0]))))
                font = ImageFont.truetype(cand.path, size, index=cand.index)
                ascent, _ = font.getmetrics()
                bbox = font.getbbox(text)
            except Exception:
                continue
            x = ibox[0] - bbox[0]
            baseline = ibox[3] - (bbox[3] - ascent)
            weight = 500 if size >= 32 else 400
            items.append(
                TextItem(
                    text=text,
                    x=x,
                    baseline=baseline,
                    size=float(size),
                    family=cand.family,
                    weight=weight,
                    fill=color,
                    score=0.0,
                    confidence=line.confidence,
                    ink_box=(ibox[0], ibox[1], ibox[2], ibox[3]),
                )
            )
        return items, cand.family

    # First pass: per-line candidate scores.  Long lines are more informative
    # for font identification, so weight the family vote by ink area.
    line_results = []
    family_votes: dict[str, float] = {}
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        mask, ibox, color, crop_origin = _ink_box(img, line.box)
        tw = ibox[2] - ibox[0]
        th = ibox[3] - ibox[1]
        if tw < 2 or th < 2:
            continue
        scored = []
        for cand in candidates:
            r = _fit_candidate(text, img, mask, ibox, crop_origin, cand)
            if r is None:
                continue
            score, size, x, baseline = r
            scored.append((score, cand, size, x, baseline))
            family_votes[cand.family] = family_votes.get(cand.family, 0.0) + score * tw
        if not scored:
            continue
        scored.sort(key=lambda z: z[0], reverse=True)
        line_results.append((line, ibox, color, scored))

    if not line_results:
        return [], "sans-serif"

    family = max(family_votes.items(), key=lambda kv: kv[1])[0]

    items: list[TextItem] = []
    for line, ibox, color, scored in line_results:
        # Restrict to the chosen document family, then prefer the lighter
        # weight when scores are close: OCR/AA masks often make text look bold.
        fam = [s for s in scored if s[1].family == family] or scored
        fam.sort(key=lambda z: (-round(z[0], 2), z[1].weight))
        best = fam[0]
        score, cand, size, x, baseline = best
        # Small UI text is rarely bold; the ink mask's antialiasing halo makes
        # density matching prefer heavier weights at 10-12 px.  Cap it.
        weight = cand.weight
        if size < 15.0:
            weight = min(weight, 500)
        else:
            weight = min(weight, 600)
        items.append(
            TextItem(
                text=line.text.strip(),
                x=x,
                baseline=baseline,
                size=size,
                family=cand.family,
                weight=weight,
                fill=color,
                score=score,
                confidence=line.confidence,
                ink_box=(ibox[0], ibox[1], ibox[2], ibox[3]),
            )
        )
    return items, family
