"""Command-line entry point.

    python -m distill_svg tests/1.png -o out/1.svg

The intermediate scene JSON (--scene) is useful for debugging and for a
future manual-correction UI: OCR mistakes can be fixed without re-running CV.
The pipeline itself lives in :mod:`distill_svg.api` and is shared with the
native GUI worker.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .api import ConvertOptions, convert


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Reconstruct a screenshot as a flat-colour or wireframe SVG.")
    p.add_argument("--version", action="version", version="distill-svg 0.2.0")
    p.add_argument("input", help="screenshot (png/jpg)")
    p.add_argument("-o", "--output", help="output .svg (default: alongside input)")
    p.add_argument("--scene", help="also write the intermediate scene JSON")
    p.add_argument("--scale", type=int, default=2, help="analysis downscale factor (default 2)")
    p.add_argument("--k", type=int, default=14, help="palette size (default 14)")
    p.add_argument("--ocr-engine", choices=["auto", "vision", "rapidocr", "hybrid"], default="auto",
                   help="OCR backend (auto: hybrid when both are available, else Vision/RapidOCR)")
    p.add_argument("--ocr-json", help="use OCR boxes from JSON instead of a live OCR engine")
    p.add_argument("--languages", default="zh-Hans,en-US", help="OCR language order")
    p.add_argument("--style", choices=["flat", "wireframe"], default="flat",
                   help="flat colour reconstruction or monochrome wireframe")
    p.add_argument("--strokes", choices=["off", "auto", "all"], default="auto",
                   help="detect thin separators/borders (auto = long dividers/borders only)")
    p.add_argument("--photos", choices=["embed", "placeholder", "outline"], default="embed",
                   help="photo/avatar handling (default: embed crops)")
    p.add_argument("--photo-format", choices=["png", "jpeg"], default="png",
                   help="embedded photo format (png = lossless, jpeg = smaller)")
    p.add_argument("--photo-quality", type=int, default=92,
                   help="JPEG quality for embedded photos (40-100)")
    p.add_argument("--photo-max-size", type=int, default=2000,
                   help="maximum embedded photo dimension in pixels")
    p.add_argument("--photo-grayscale", action="store_true",
                   help="desaturate embedded photos (implied by --style wireframe)")
    p.add_argument("--photo-scrub", action="store_true",
                   help="drop 2 LSBs per channel in embedded photos (anti-LSB-stego)")
    p.add_argument("--fast", action="store_true",
                   help="quick preview: no font voting, no icon tracing (embed icons still apply)")
    p.add_argument("--quick", action="store_true",
                   help="preset: wireframe + fast + placeholder (smallest/fastest)")
    p.add_argument("--icons", action="store_true",
                   help="trace icons even with --fast")
    p.add_argument("--icon-mode", choices=["auto", "vector", "embed"], default="auto",
                   help="auto: cut out icons for flat, trace for wireframe (default); "
                        "vector: trace paths; embed: cut icon pixels out as PNG "
                        "(embed is ignored with --photos placeholder/outline or --quick)")
    p.add_argument("--no-icons", action="store_true", help="omit traced icons")
    p.add_argument("--no-images", action="store_true",
                   help="omit photo/avatar images (use --no-icons for icons)")
    p.add_argument("--keep-photo-text", action="store_true",
                   help="keep OCR text that lies inside an embedded photo (may double-draw it)")
    p.add_argument("--font-dir", action="append", default=[], help="extra font directory")
    p.add_argument("--debug", action="store_true", help="write debug overlays")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    src = Path(args.input)
    if not src.exists():
        print(f"input not found: {src}", file=sys.stderr)
        return 2

    out = Path(args.output) if args.output else src.with_suffix(".svg")
    options = ConvertOptions(
        input=str(src),
        output=str(out),
        scene=args.scene,
        scale=args.scale,
        k=args.k,
        ocr_engine=args.ocr_engine,
        ocr_json=args.ocr_json,
        languages=args.languages,
        style=args.style,
        strokes=args.strokes,
        photos=args.photos,
        photo_format=args.photo_format,
        photo_quality=args.photo_quality,
        photo_max_size=args.photo_max_size,
        photo_grayscale=args.photo_grayscale,
        photo_scrub=args.photo_scrub,
        fast=args.fast,
        quick=args.quick,
        icons=args.icons,
        icon_mode=args.icon_mode,
        no_icons=args.no_icons,
        no_images=args.no_images,
        keep_photo_text=args.keep_photo_text,
        font_dir=list(args.font_dir),
        debug=args.debug,
    )
    try:
        result = convert(options)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for line in result.get("log", []):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
