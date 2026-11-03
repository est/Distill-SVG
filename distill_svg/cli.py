"""Command-line entry point.

    python -m distill_svg tests/1.png -o out/1.svg

The intermediate scene JSON (--scene) is useful for debugging and for a
future manual-correction UI: OCR mistakes can be fixed without re-running CV.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from .flatten import prepare
from .fonts import default_candidates, fit_text_lines
from .images import extract_images_and_icons
from .ocr import clean_ocr_lines, load_ocr_json, ocr_image
from .panels import detect_panels
from .strokes import detect_strokes
from .svg import build_svg, write_svg


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
                   help="quick preview: no font voting, no icon tracing")
    p.add_argument("--quick", action="store_true",
                   help="preset: wireframe + fast + placeholder (smallest/fastest)")
    p.add_argument("--icons", action="store_true",
                   help="trace icons even with --fast")
    p.add_argument("--no-icons", action="store_true", help="omit traced icons")
    p.add_argument("--no-images", action="store_true", help="omit images entirely")
    p.add_argument("--keep-photo-text", action="store_true",
                   help="keep OCR text that lies inside an embedded photo (may double-draw it)")
    p.add_argument("--font-dir", action="append", default=[], help="extra font directory")
    p.add_argument("--debug", action="store_true", help="write debug overlays")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.quick:
        args.style = "wireframe"
        args.fast = True
        args.photos = "placeholder"
    src = Path(args.input)
    if not src.exists():
        print(f"input not found: {src}", file=sys.stderr)
        return 2

    out = Path(args.output) if args.output else src.with_suffix(".svg")
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.font_dir:
        import os

        os.environ["DISTILL_SVG_FONT_DIRS"] = os.pathsep.join(args.font_dir)

    timings = {}
    t0 = time.perf_counter()
    prep = prepare(str(src), analysis_scale=args.scale, k=args.k)
    timings["prepare"] = time.perf_counter() - t0
    print(f"image: {prep.width}x{prep.height}, analysis {prep.aw}x{prep.ah} (scale {prep.scale})")

    t0 = time.perf_counter()
    ocr_engine = args.ocr_engine
    if args.fast and ocr_engine == "auto":
        # Don't pay for two OCR engines in a quick preview.
        ocr_engine = "vision" if sys.platform == "darwin" and shutil.which("swiftc") else "rapidocr"
    lines = load_ocr_json(args.ocr_json) if args.ocr_json else ocr_image(
        str(src), args.languages, engine=ocr_engine
    )
    lines = clean_ocr_lines(lines)
    timings["ocr"] = time.perf_counter() - t0
    print(f"ocr: {len(lines)} lines after cleanup")

    t0 = time.perf_counter()
    texts, family = fit_text_lines(prep.original, lines, default_candidates(), fast=args.fast)
    timings["text"] = time.perf_counter() - t0
    print(f"text: {len(texts)} items, font family vote: {family}")

    # extract_images_and_icons / stroke detection expect (x, y, w, h),
    # while TextItem stores the recovered ink box as (x0, y0, x1, y1).
    text_boxes = [
        (t.ink_box[0], t.ink_box[1], t.ink_box[2] - t.ink_box[0], t.ink_box[3] - t.ink_box[1])
        for t in texts
    ]

    t0 = time.perf_counter()
    background, panels = detect_panels(prep)
    timings["panels"] = time.perf_counter() - t0
    print(f"panels: {len(panels)}, background {background}")
    t0 = time.perf_counter()
    trace_icons = not args.no_icons and (not args.fast or args.icons)
    images, icons = extract_images_and_icons(
        prep,
        panels,
        text_boxes,
        background,
        photo_mode=args.photos,
        photo_format=args.photo_format,
        photo_quality=args.photo_quality,
        photo_max_size=args.photo_max_size,
        photo_grayscale=args.photo_grayscale or args.style == "wireframe",
        photo_scrub=args.photo_scrub,
        trace_icons=trace_icons,
    )
    timings["images"] = time.perf_counter() - t0
    if args.no_images:
        images = []
    if args.no_icons:
        icons = []
    embedded = sum(1 for im in images if im.embedded)
    # Do not paint OCR text on top of an embedded photo crop that already
    # contains the same glyphs: it doubles the headline/price and looks blurry.
    if embedded and not args.keep_photo_text:
        kept = []
        removed = 0
        for t in texts:
            cx = (t.ink_box[0] + t.ink_box[2]) / 2.0
            cy = (t.ink_box[1] + t.ink_box[3]) / 2.0
            inside = False
            for im in images:
                if not im.embedded:
                    continue
                bx, by, bw, bh = im.render_box()
                if bx <= cx <= bx + bw and by <= cy <= by + bh:
                    inside = True
                    break
            if inside:
                removed += 1
            else:
                kept.append(t)
        texts = kept
        if removed:
            print(f"text: dropped {removed} lines already inside embedded photos")
    print(f"images: {len(images)} ({embedded} embedded), icon paths: {len(icons)}")

    t0 = time.perf_counter()
    strokes = []
    if args.strokes != "off":
        exclude = np.zeros((prep.height, prep.width), dtype=np.uint8)
        for im in images:
            bx, by, bw, bh = im.crop_box or (im.x, im.y, im.w, im.h)
            exclude[max(0, int(by)):min(prep.height, int(by + bh)),
                    max(0, int(bx)):min(prep.width, int(bx + bw))] = 255
        strokes = detect_strokes(
            prep,
            panels,
            text_boxes=text_boxes,
            exclude_mask=exclude,
            min_length=40,
            max_ambient_std=30,
        )
        if args.strokes == "auto":
            strokes = [
                s for s in strokes
                if s.kind in ("divider", "border") and s.length >= 40 and s.confidence >= 0.60
            ]
    timings["strokes"] = time.perf_counter() - t0
    print(f"strokes: {len(strokes)}")

    t0 = time.perf_counter()
    svg = build_svg(
        width=prep.width,
        height=prep.height,
        background=background,
        panels=panels,
        images=images,
        icons=icons,
        texts=texts,
        family=family,
        strokes=strokes,
        style=args.style,
        photo_mode=args.photos,
        # Do not write the source filename (or path) into the SVG: it is a
        # privacy leak and has nothing to do with the reconstructed content.
        title="Reconstructed screenshot",
    )
    timings["svg"] = time.perf_counter() - t0
    write_svg(out, svg)
    print(f"wrote {out} ({out.stat().st_size / 1024:.1f} KiB)")
    print("timing: " + ", ".join(f"{k} {v:.2f}s" for k, v in timings.items()))

    if args.scene:
        scene = {
            "width": prep.width,
            "height": prep.height,
            "background": background,
            "font_family": family,
            "style": args.style,
            "photos": args.photos,
            "panels": [p.to_dict() for p in panels],
            "images": [i.to_dict() for i in images],
            "icons": [i.to_dict() for i in icons],
            "strokes": [s.to_dict() for s in strokes],
            "texts": [t.to_dict() for t in texts],
        }
        Path(args.scene).write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {args.scene}")

    if args.debug:
        debug_dir = out.parent
        overlay = prep.original.copy()
        for p in panels:
            cv2.rectangle(
                overlay,
                (int(p.x), int(p.y)),
                (int(p.x + p.w), int(p.y + p.h)),
                (0, 0, 255),
                1,
            )
        for im in images:
            bx, by, bw, bh = im.render_box()
            cv2.rectangle(
                overlay,
                (int(bx), int(by)),
                (int(bx + bw), int(by + bh)),
                (255, 0, 255),
                1,
            )
        for t in texts:
            cv2.line(overlay, (int(t.x), int(t.baseline)), (int(t.x + t.size * 2), int(t.baseline)), (0, 180, 0), 1)
        cv2.imwrite(str(debug_dir / f"{out.stem}.debug.png"), overlay)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
