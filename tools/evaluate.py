#!/usr/bin/env python3
"""Reproduce the text/layout check from docs/RESEARCH.md.

    python3 tools/evaluate.py tests/2.jpg out/2.svg --render /tmp/2.png

If --render is omitted, the script only compares the original screenshot with
itself (useful to sanity-check OCR).  For SVG comparison, rasterise it first
with tools/render_svg.js.
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from distill_svg.ocr import clean_ocr_lines, ocr_image  # noqa: E402


def match(original, rendered):
    used: set[int] = set()
    rows = []
    for a in original:
        best = None
        for j, b in enumerate(rendered):
            if j in used:
                continue
            dist = math.hypot(
                (a.x + a.w / 2) - (b.x + b.w / 2),
                (a.y + a.h / 2) - (b.y + b.h / 2),
            )
            if dist < max(30.0, a.h * 2.0):
                sim = difflib.SequenceMatcher(None, a.text, b.text).ratio()
                score = sim - dist / 1000.0
                if best is None or score > best[0]:
                    best = (score, j, b, dist, sim)
        if best:
            used.add(best[1])
            rows.append((a, best[2], best[3], best[4]))
    return rows


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("original")
    p.add_argument("render", nargs="?")
    p.add_argument("--engine", default="auto", choices=["auto", "vision", "rapidocr", "hybrid"])
    args = p.parse_args(argv)

    original = clean_ocr_lines(ocr_image(args.original, engine=args.engine))
    render_path = args.render or args.original
    rendered = clean_ocr_lines(ocr_image(render_path, engine=args.engine))
    rows = match(original, rendered)
    distances = sorted(r[2] for r in rows)
    result = {
        "original_lines": len(original),
        "rendered_lines": len(rendered),
        "matched": len(rows),
        "median_centre_px": round(distances[len(distances) // 2], 2) if distances else None,
        "mean_text_similarity": round(sum(r[3] for r in rows) / len(rows), 3) if rows else None,
        "unmatched": [r.text for r in original if not any(r is m[0] for m in rows)],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
