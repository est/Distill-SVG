"""Library entry point shared by the CLI and the GUI worker.

The GUI talks to :func:`convert` through ``distill_svg.worker`` (NDJSON over
stdio).  Keeping one implementation means the CLI, the app and the tests all
produce the same SVG for the same options.

Nothing in here parses command-line arguments or writes to stdout; progress
and human-readable log lines are delivered through callbacks so the caller can
format them for a terminal, a JSON protocol or a test.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .flatten import prepare
from .fonts import default_candidates, fit_text_lines
from .images import extract_images_and_icons
from .ocr import OcrLine, clean_ocr_lines, load_ocr_json, ocr_image
from .panels import detect_panels
from .strokes import detect_strokes
from .svg import build_svg, write_svg


class ConversionCancelled(RuntimeError):
    """Raised between pipeline stages when the user cancels a conversion."""


@dataclass
class ConvertOptions:
    input: str
    output: str
    scene: str | None = None
    scale: int = 2
    k: int = 14
    ocr_engine: str = "auto"
    ocr_json: str | None = None
    ocr_lines: list[dict] | None = None
    languages: str = "zh-Hans,en-US"
    style: str = "flat"
    strokes: str = "auto"
    photos: str = "embed"
    photo_format: str = "png"
    photo_quality: int = 92
    photo_max_size: int = 2000
    photo_grayscale: bool = False
    photo_scrub: bool = False
    fast: bool = False
    quick: bool = False
    icons: bool = False
    no_icons: bool = False
    no_images: bool = False
    keep_photo_text: bool = False
    font_dir: list[str] = field(default_factory=list)
    debug: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "ConvertOptions":
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{key: value for key, value in data.items() if key in known})

    def to_dict(self) -> dict:
        return {key: getattr(self, key) for key in self.__dataclass_fields__}  # type: ignore[attr-defined]


ProgressCallback = Callable[[str, float, str], None]

# Stage boundaries used for a smooth 0..1 progress bar.  The OCR span is
# skipped (and the following stages stretched) when the caller supplies OCR.
_STAGES = (
    ("prepare", 0.02, 0.18, "分析图像"),
    ("ocr", 0.18, 0.55, "识别文字"),
    ("text", 0.55, 0.64, "拟合字体"),
    ("panels", 0.64, 0.74, "检测面板"),
    ("images", 0.74, 0.91, "提取图像与图标"),
    ("strokes", 0.91, 0.96, "检测分隔线"),
    ("svg", 0.96, 0.99, "生成 SVG"),
)

_PROGRESS = {name: (start, end, label) for name, start, end, label in _STAGES}


def _emit(progress: ProgressCallback | None, stage: str, pct: float | None = None, message: str | None = None):
    if progress is None:
        return
    start, _end, label = _PROGRESS.get(stage, (0.0, 1.0, stage))
    progress(stage, start if pct is None else pct, message or label)


def _cancelled(cancel: Callable[[], bool] | None):
    if cancel is not None and cancel():
        raise ConversionCancelled("conversion cancelled")


def convert(
    options: ConvertOptions,
    *,
    progress: ProgressCallback | None = None,
    cancel: Callable[[], bool] | None = None,
) -> dict:
    """Run the whole reconstruction pipeline for one screenshot.

    Returns a JSON-serialisable result dict.  Raises on invalid input or when
    ``cancel`` turns true between stages.
    """
    opts = options
    if opts.quick:
        opts.style = "wireframe"
        opts.fast = True
        opts.photos = "placeholder"

    src = Path(opts.input)
    if not src.exists():
        raise FileNotFoundError(f"input not found: {src}")

    out = Path(opts.output) if opts.output else src.with_suffix(".svg")
    out.parent.mkdir(parents=True, exist_ok=True)

    if opts.font_dir:
        os.environ["DISTILL_SVG_FONT_DIRS"] = os.pathsep.join(opts.font_dir)

    log: list[str] = []
    timings: dict[str, float] = {}

    # Let the GUI cancel before the expensive stages start.
    _cancelled(cancel)

    t0 = time.perf_counter()
    _emit(progress, "prepare")
    prep = prepare(str(src), analysis_scale=opts.scale, k=opts.k)
    timings["prepare"] = time.perf_counter() - t0
    log.append(f"image: {prep.width}x{prep.height}, analysis {prep.aw}x{prep.ah} (scale {prep.scale})")
    _cancelled(cancel)

    t0 = time.perf_counter()
    if opts.ocr_lines is not None:
        # The macOS shell performs Vision OCR in-process and passes the lines
        # here, so the shipped core does not need swiftc, RapidOCR or ONNX.
        lines = [
            OcrLine(
                text=str(item.get("text", "")),
                confidence=float(item.get("confidence", 1.0)),
                x=float(item.get("x", 0.0)),
                y=float(item.get("y", 0.0)),
                w=float(item.get("w", 0.0)),
                h=float(item.get("h", 0.0)),
            )
            for item in opts.ocr_lines
        ]
    elif opts.ocr_json:
        _emit(progress, "ocr", None, "读取 OCR 结果")
        lines = load_ocr_json(opts.ocr_json)
    else:
        _emit(progress, "ocr")
        ocr_engine = opts.ocr_engine
        if opts.fast and ocr_engine == "auto":
            # Don't pay for two OCR engines in a quick preview.
            ocr_engine = "vision" if sys.platform == "darwin" and shutil.which("swiftc") else "rapidocr"
        lines = ocr_image(str(src), opts.languages, engine=ocr_engine)
    lines = clean_ocr_lines(lines)
    timings["ocr"] = time.perf_counter() - t0
    log.append(f"ocr: {len(lines)} lines after cleanup")
    _cancelled(cancel)

    t0 = time.perf_counter()
    _emit(progress, "text")
    texts, family = fit_text_lines(prep.original, lines, default_candidates(), fast=opts.fast)
    timings["text"] = time.perf_counter() - t0
    log.append(f"text: {len(texts)} items, font family vote: {family}")

    # extract_images_and_icons / stroke detection expect (x, y, w, h),
    # while TextItem stores the recovered ink box as (x0, y0, x1, y1).
    text_boxes = [
        (t.ink_box[0], t.ink_box[1], t.ink_box[2] - t.ink_box[0], t.ink_box[3] - t.ink_box[1])
        for t in texts
    ]

    t0 = time.perf_counter()
    _emit(progress, "panels")
    background, panels = detect_panels(prep)
    timings["panels"] = time.perf_counter() - t0
    log.append(f"panels: {len(panels)}, background {background}")
    _cancelled(cancel)

    t0 = time.perf_counter()
    _emit(progress, "images")
    trace_icons = not opts.no_icons and (not opts.fast or opts.icons)
    images, icons = extract_images_and_icons(
        prep,
        panels,
        text_boxes,
        background,
        photo_mode=opts.photos,
        photo_format=opts.photo_format,
        photo_quality=opts.photo_quality,
        photo_max_size=opts.photo_max_size,
        photo_grayscale=opts.photo_grayscale or opts.style == "wireframe",
        photo_scrub=opts.photo_scrub,
        trace_icons=trace_icons,
    )
    timings["images"] = time.perf_counter() - t0
    if opts.no_images:
        images = []
    if opts.no_icons:
        icons = []
    embedded = sum(1 for im in images if im.embedded)
    # Do not paint OCR text on top of an embedded photo crop that already
    # contains the same glyphs: it doubles the headline/price and looks blurry.
    if embedded and not opts.keep_photo_text:
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
            log.append(f"text: dropped {removed} lines already inside embedded photos")
    log.append(f"images: {len(images)} ({embedded} embedded), icon paths: {len(icons)}")
    _cancelled(cancel)

    t0 = time.perf_counter()
    _emit(progress, "strokes")
    strokes = []
    if opts.strokes != "off":
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
        if opts.strokes == "auto":
            strokes = [
                s for s in strokes
                if s.kind in ("divider", "border") and s.length >= 40 and s.confidence >= 0.60
            ]
    timings["strokes"] = time.perf_counter() - t0
    log.append(f"strokes: {len(strokes)}")
    _cancelled(cancel)

    t0 = time.perf_counter()
    _emit(progress, "svg")
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
        style=opts.style,
        photo_mode=opts.photos,
        # Do not write the source filename (or path) into the SVG: it is a
        # privacy leak and has nothing to do with the reconstructed content.
        title="Reconstructed screenshot",
    )
    timings["svg"] = time.perf_counter() - t0
    write_svg(out, svg)
    log.extend(
        [
            f"wrote {out} ({out.stat().st_size / 1024:.1f} KiB)",
            "timing: " + ", ".join(f"{k} {v:.2f}s" for k, v in timings.items()),
        ]
    )

    scene_path = None
    if opts.scene:
        scene = {
            "width": prep.width,
            "height": prep.height,
            "background": background,
            "font_family": family,
            "style": opts.style,
            "photos": opts.photos,
            "panels": [p.to_dict() for p in panels],
            "images": [i.to_dict() for i in images],
            "icons": [i.to_dict() for i in icons],
            "strokes": [s.to_dict() for s in strokes],
            "texts": [t.to_dict() for t in texts],
        }
        Path(opts.scene).write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
        scene_path = str(opts.scene)
        log.append(f"wrote {opts.scene}")

    if opts.debug:
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
        cv2.imwrite(str(out.parent / f"{out.stem}.debug.png"), overlay)

    if progress is not None:
        progress("done", 1.0, "完成")

    return {
        "svg_path": str(out),
        "scene_path": scene_path,
        "width": prep.width,
        "height": prep.height,
        "background": background,
        "font_family": family,
        "stats": {
            "panels": len(panels),
            "texts": len(texts),
            "images": len(images),
            "embedded_photos": embedded,
            "icons": len(icons),
            "strokes": len(strokes),
            "svg_bytes": out.stat().st_size,
            "ocr_lines": len(lines),
        },
        "timings": timings,
        "log": log,
    }
