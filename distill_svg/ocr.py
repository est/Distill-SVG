"""OCR adapters.

Default backend: Apple Vision (macOS, on-device, strong Simplified Chinese).
The rest of the pipeline only needs line-level boxes, so swapping in
PaddleOCR/RapidOCR/Windows OCR later is straightforward.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OcrLine:
    text: str
    confidence: float
    x: float
    y: float
    w: float
    h: float

    @property
    def box(self):
        return (self.x, self.y, self.w, self.h)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _helper_path() -> Path:
    return _project_root() / "tools" / ".build" / "vision_ocr"


def _ensure_helper() -> Path:
    helper = _helper_path()
    source = _project_root() / "tools" / "vision_ocr.swift"
    if helper.exists() and helper.stat().st_mtime >= source.stat().st_mtime:
        return helper
    if shutil.which("swiftc") is None:
        raise RuntimeError(
            "macOS OCR helper needs swiftc, or pass --ocr-json from another OCR backend"
        )
    helper.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["swiftc", "-O", str(source), "-o", str(helper)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"failed to build OCR helper:\n{proc.stderr}")
    return helper


def ocr_vision(path: str, languages: str = "zh-Hans,en-US") -> list[OcrLine]:
    helper = _ensure_helper()
    proc = subprocess.run(
        [str(helper), path, languages],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "OCR helper failed")
    raw = json.loads(proc.stdout)
    return [
        OcrLine(
            text=item["text"],
            confidence=float(item["confidence"]),
            x=float(item["x"]),
            y=float(item["y"]),
            w=float(item["w"]),
            h=float(item["h"]),
        )
        for item in raw
    ]


def ocr_rapidocr(path: str) -> list[OcrLine]:
    """Optional cross-platform backend.  Install with:
    ``pip install rapidocr_onnxruntime``.
    """
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "RapidOCR is not installed; run `pip install rapidocr_onnxruntime` "
            "or pass --ocr-json boxes.json"
        ) from exc

    engine = RapidOCR()
    result, _elapse = engine(path)
    lines: list[OcrLine] = []
    for item in result or []:
        try:
            box, text, score = item[0], item[1], item[2]
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
        except Exception:
            continue
        lines.append(
            OcrLine(
                text=str(text),
                confidence=float(score),
                x=min(xs),
                y=min(ys),
                w=max(xs) - min(xs),
                h=max(ys) - min(ys),
            )
        )
    return lines




def _rapidocr_available() -> bool:
    return importlib.util.find_spec("rapidocr_onnxruntime") is not None


def _norm_for_match(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", "", text)
    return text.lower()




def _text_quality(text: str) -> float:
    """Cheap plausibility score used only when two engines disagree."""
    if not text:
        return -10.0
    q = 0.0
    pairs = (("“", "”"), ("（", "）"), ("(", ")"))
    for op, cl in pairs:
        if text.count(op) != text.count(cl):
            q -= 1.0
    if text[:1] in "”），,.;；":
        q -= 1.5
    if text[-1:] in "“（":
        q -= 1.0
    q -= 2.5 * _symbol_ratio(text)
    return q


def _overlap(a: OcrLine, b: OcrLine) -> float:
    ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    area = max(1.0, min(a.w * a.h, b.w * b.h))
    return inter / area


def fuse_ocr(vision: list[OcrLine], rapid: list[OcrLine]) -> list[OcrLine]:
    """Conservative cross-engine fusion.

    Vision tends to preserve spaces and punctuation; RapidOCR is often more
    confident on UI-specific Chinese/number strings.  Keep the best of both
    without inventing text.
    """
    used: set[int] = set()
    out: list[OcrLine] = []
    for v in vision:
        best = None
        for j, r in enumerate(rapid):
            if j in used:
                continue
            ov = _overlap(v, r)
            if ov > 0.35:
                if best is None or ov > best[0]:
                    best = (ov, j, r)
        if best is None:
            out.append(v)
            continue
        _ov, j, r = best
        used.add(j)
        if _norm_for_match(v.text) == _norm_for_match(r.text):
            # Same content: keep Vision's spacing/punctuation.
            out.append(v)
            continue
        vq, rq = _text_quality(v.text), _text_quality(r.text)
        if vq >= rq + 1.0 and v.confidence >= 0.45:
            chosen = v
        elif rq >= vq + 1.0 and r.confidence >= 0.45:
            chosen = r
        elif v.confidence < 0.65 and r.confidence >= 0.90:
            chosen = r
        elif r.confidence < 0.65 and v.confidence >= 0.90:
            chosen = v
        elif len(r.text) >= len(v.text) + 3 and r.confidence > v.confidence + 0.20:
            chosen = r
        else:
            chosen = v
        # Use the chosen engine's own box.  A union box is wrong when one
        # engine merged a star rating into "4.6", etc.
        out.append(
            OcrLine(
                text=chosen.text,
                confidence=max(v.confidence, r.confidence),
                x=chosen.x,
                y=chosen.y,
                w=chosen.w,
                h=chosen.h,
            )
        )

    for j, r in enumerate(rapid):
        if j in used:
            continue
        if r.confidence >= 0.92 and len(r.text.strip()) >= 2:
            out.append(r)

    out.sort(key=lambda l: (round(l.y, 1), l.x))
    return out


def ocr_image(
    path: str,
    languages: str = "zh-Hans,en-US",
    engine: str = "auto",
) -> list[OcrLine]:
    engine = (engine or "auto").lower()
    has_vision = sys.platform == "darwin" and shutil.which("swiftc") is not None
    has_rapid = _rapidocr_available()
    if engine == "auto":
        if has_vision and has_rapid:
            engine = "hybrid"
        elif has_vision:
            engine = "vision"
        else:
            engine = "rapidocr"
    if engine == "vision":
        return ocr_vision(path, languages)
    if engine == "rapidocr":
        return ocr_rapidocr(path)
    if engine == "hybrid":
        if not has_vision:
            return ocr_rapidocr(path)
        if not has_rapid:
            return ocr_vision(path, languages)
        return fuse_ocr(
            clean_ocr_lines(ocr_vision(path, languages)),
            clean_ocr_lines(ocr_rapidocr(path)),
        )
    raise RuntimeError(f"unknown OCR engine: {engine}")


def load_ocr_json(path: str) -> list[OcrLine]:
    """Load OCR lines from a simple JSON file (cross-platform escape hatch)."""
    raw = json.loads(Path(path).read_text())
    return [
        OcrLine(
            text=item["text"],
            confidence=float(item.get("confidence", 1.0)),
            x=float(item["x"]),
            y=float(item["y"]),
            w=float(item["w"]),
            h=float(item["h"]),
        )
        for item in raw
    ]


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _symbol_ratio(text: str) -> float:
    if not text:
        return 1.0
    symbols = sum(
        1
        for ch in text
        if unicodedata.category(ch)[0] in ("P", "S") and ch not in "，。！？：；、“”‘’\"'"
    )
    return symbols / len(text)


def clean_ocr_lines(lines: list[OcrLine]) -> list[OcrLine]:
    """Drop icon-shaped OCR artefacts and repair a few common box paddings.

    OCR engines happily "read" toolbar icons as EQ / 2Q / © / Aa.  Those
    become visible garbage in a text reconstruction, so they are removed by a
    conservative confidence/shape rule.  We do not rewrite real words: text
    corrections belong in the scene JSON / future editor, not in a heuristic.
    """
    out: list[OcrLine] = []
    for line in lines:
        raw = line.text
        text = unicodedata.normalize("NFC", raw)
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
        text = re.sub(r"[ \t]+", " ", text).strip()
        # Vision often appends the dropdown chevron/separator to a button.
        cleaned = re.sub(r"\s*[|｜丨]\s*[v▾▼▾]?\s*$", "", text).strip()
        cleaned = cleaned.strip("\u00a0")
        # OCR often turns a closing parenthesis into a trailing "l".
        cleaned = re.sub(r"（([A-Za-z0-9 ]+)l$", r"(\1)", cleaned)
        # Balance parentheses so the text does not look truncated.
        for op, cl in (("（", "）"), ("(", ")")):
            missing = cleaned.count(op) - cleaned.count(cl)
            if missing > 0:
                cleaned += cl * missing

        if not cleaned:
            continue
        if not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", cleaned):
            continue
        if line.confidence < 0.45 and len(cleaned) <= 4 and not _has_cjk(cleaned):
            continue
        if line.confidence < 0.40 and _symbol_ratio(text) > 0.30:
            continue
        if line.confidence < 0.35 and any(ch in text for ch in "•*") and not _has_cjk(text):
            continue
        if cleaned.isdigit() and len(cleaned) <= 3 and line.confidence < 0.70:
            continue
        if re.fullmatch(r"[.·•…\s]+", cleaned):
            continue

        out.append(
            OcrLine(
                text=cleaned,
                confidence=line.confidence,
                x=line.x,
                y=line.y,
                w=line.w,
                h=line.h,
            )
        )

    # Remove near-duplicate boxes (same string, heavily overlapping).
    deduped: list[OcrLine] = []
    for line in sorted(out, key=lambda l: -l.confidence):
        dup = False
        for other in deduped:
            if line.text != other.text:
                continue
            ix = max(0.0, min(line.x + line.w, other.x + other.w) - max(line.x, other.x))
            iy = max(0.0, min(line.y + line.h, other.y + other.h) - max(line.y, other.y))
            inter = ix * iy
            area = min(line.w * line.h, other.w * other.h)
            if area > 0 and inter / area > 0.6:
                dup = True
                break
        if not dup:
            deduped.append(line)
    return deduped
