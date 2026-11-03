#!/usr/bin/env python3
"""Generate the macOS app icon (no external assets or fonts required).

Run manually with ``python3 mac/make_icon.py mac/AppIcon.icns``; the build
script calls it when the .icns is missing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


SIZE = 1024
SCALE = 4
CANVAS = SIZE * SCALE


def _rounded(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", (1, size))
    pixels = image.load()
    for y in range(size):
        t = y / max(1, size - 1)
        pixels[0, y] = (
            round(top[0] + (bottom[0] - top[0]) * t),
            round(top[1] + (bottom[1] - top[1]) * t),
            round(top[2] + (bottom[2] - top[2]) * t),
        )
    return image.resize((size, size), Image.Resampling.BILINEAR)


def render_master() -> Image.Image:
    s = SCALE
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))

    margin = 62 * s
    radius = 224 * s
    body = (margin, margin, CANVAS - margin, CANVAS - margin)

    # Drop shadow under the squircle.
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        (body[0] + 8 * s, body[1] + 18 * s, body[2] + 8 * s, body[3] + 18 * s),
        radius=radius,
        fill=(8, 12, 24, 120),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(28 * s))
    canvas.alpha_composite(shadow)

    # Gradient body, clipped to a rounded square.
    gradient = _gradient(CANVAS, (36, 52, 85), (12, 16, 30))
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(body, radius=radius, fill=255)
    canvas.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(canvas)

    # Subtle top highlight.
    draw.rounded_rectangle(
        (body[0] + 2 * s, body[1] + 2 * s, body[2] - 2 * s, body[3] - 2 * s),
        radius=radius - 2 * s,
        outline=(255, 255, 255, 34),
        width=3 * s,
    )

    # Screenshot card.
    card = (218 * s, 232 * s, 806 * s, 640 * s)
    draw.rounded_rectangle(card, radius=46 * s, fill=(250, 251, 253, 255))
    draw.rounded_rectangle(
        (card[0], card[1], card[2], card[1] + 64 * s),
        radius=46 * s,
        fill=(226, 231, 240, 255),
    )
    draw.rectangle(
        (card[0], card[1] + 34 * s, card[2], card[1] + 64 * s),
        fill=(226, 231, 240, 255),
    )
    for i, color in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        cx = card[0] + (44 + i * 34) * s
        cy = card[1] + 32 * s
        r = 8 * s
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)

    # Flat UI blocks inside the screenshot.
    bars = [
        (258, 348, 742, 416, (255, 122, 89)),
        (258, 448, 610, 500, (78, 205, 196)),
        (258, 532, 700, 566, (214, 221, 232)),
        (258, 588, 560, 610, (222, 228, 238)),
    ]
    for x0, y0, x1, y1, color in bars:
        draw.rounded_rectangle(
            (x0 * s, y0 * s, x1 * s, y1 * s),
            radius=14 * s,
            fill=color,
        )

    # Vector path leaving the card: "screenshot → curve".
    path_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    pd = ImageDraw.Draw(path_layer)
    # Stamp overlapping discs instead of a polyline: at this scale the
    # polyline joints show up as a ladder, while discs stay smooth.
    points = []
    steps = 420
    for i in range(steps + 1):
        t = i / steps
        # Cubic-ish curve from inside the card to the bottom-right corner.
        x = (640 + (900 - 640) * t) * s
        y = (600 + (790 - 600) * t - 150 * (t ** 2) + 40 * (t ** 3)) * s
        points.append((x, y))
    stroke_r = 13 * s
    for x, y in points:
        pd.ellipse(
            (x - stroke_r, y - stroke_r, x + stroke_r, y + stroke_r),
            fill=(255, 174, 74, 255),
        )
    for point in (points[0], points[-1]):
        r = 22 * s
        pd.ellipse((point[0] - r, point[1] - r, point[0] + r, point[1] + r), fill=(255, 174, 74, 255))
        r2 = 10 * s
        pd.ellipse(
            (point[0] - r2, point[1] - r2, point[0] + r2, point[1] + r2),
            fill=(18, 24, 40, 255),
        )
    canvas.alpha_composite(path_layer)

    return canvas.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "mac/AppIcon.icns")
    master = render_master()

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        specs = [
            ("icon_16x16.png", 16),
            ("icon_16x16@2x.png", 32),
            ("icon_32x32.png", 32),
            ("icon_32x32@2x.png", 64),
            ("icon_128x128.png", 128),
            ("icon_128x128@2x.png", 256),
            ("icon_256x256.png", 256),
            ("icon_256x256@2x.png", 512),
            ("icon_512x512.png", 512),
            ("icon_512x512@2x.png", 1024),
        ]
        for name, size in specs:
            master.resize((size, size), Image.Resampling.LANCZOS).save(iconset / name)
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
