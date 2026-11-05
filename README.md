# Distill-SVG 蒸矢

>  Distill png/jpg screenshots to copy-pastable SVG 把截图蒸馏成可编辑的 SVG。

Screenshot → **flat-color or wireframe SVG**. Panels become rectangles, text
stays real selectable text, icons/emoji are cut out or traced depending on
style, and photos/avatars are embedded as cropped images (or replaced with
placeholders in privacy mode).

This is *semantic reconstruction*, not a bitmap trace: the output is a redrawn
UI, not a compressed copy of the screenshot. Geometry, text and colours are
rebuilt; the full-frame raster is never embedded. Photo/avatar crops can be
embedded on demand.


## Features

- **Two styles**
  - `--style flat`: flat colours from the screenshot.
  - `--style wireframe`: white fills, grey outlines, monochrome text/icons.
- **Photos/avatars**
  - `--photos embed` (default): crop from the original and embed as a data URI.
  - `--photos placeholder`: neutral block + glyph (privacy mode).
  - `--photos outline`: empty outlined box.
- **Selectable text**: every line is a real `<text>` element, not a path.
- **High-fidelity icon cutouts**: `--icon-mode auto` (the default) keeps
  icons/emoji as alpha-masked PNGs for flat colours and traces vector paths
  for wireframe. Force either with `--icon-mode embed|vector`; the cutouts are
  closer to the screenshot but are no longer pure vector.
- **Fast preview**: `--fast` skips font voting and icon tracing.
- **Local/offline OCR**: Apple Vision on macOS; optional RapidOCR for
  Windows/Linux; `auto` fuses both when available (`hybrid`).
- **Thin strokes**: optional separator/border detection (`--strokes`).
- **Editable scene**: `--scene out.json` contains boxes, styles and text.
- **No gradients / filters / full-frame bitmap**.

## Quick start

Requires Python 3.10+, `numpy`, `opencv-python`, `Pillow`, and macOS + Xcode
command-line tools for the default OCR helper (`swiftc`).

```bash
python3 -m pip install -r requirements.txt

# optional: open-source CJK fonts for better Android/Source Han matching
./tools/fetch_fonts.sh

# full quality, flat colours, embedded photos
python3 -m distill_svg tests/2.jpg -o out/2.svg --scene out/2.json

# quickest preview: wireframe + no photo pixels + no icon tracing (~7 KiB)
python3 -m distill_svg tests/2.jpg -o out/2-quick.svg --quick

# wireframe with embedded grayscale photos
python3 -m distill_svg tests/1.png -o out/1-wire.svg --style wireframe --fast

# privacy mode: no photo pixels at all
python3 -m distill_svg tests/2.jpg -o out/2-private.svg --photos placeholder
```

For Linux/Windows OCR (optional):

```bash
python3 -m pip install rapidocr_onnxruntime
python3 -m distill_svg shot.png --ocr-engine rapidocr
# or cross-check Vision + RapidOCR:
python3 -m distill_svg shot.png --ocr-engine hybrid
```

Or supply boxes from any OCR engine as JSON:
`[{"text":"...","confidence":0.9,"x":0,"y":0,"w":10,"h":10}]`
with `--ocr-json boxes.json`.

Useful options:

| option | meaning |
| --- | --- |
| `--style flat\|wireframe` | colour reconstruction or monochrome wireframe |
| `--photos embed\|placeholder\|outline` | photo/avatar handling; `outline` = fully line-only |
| `--photo-format png\|jpeg` | embedded crop format |
| `--photo-quality 92` | JPEG quality |
| `--photo-max-size 2000` | max embedded crop dimension |
| `--photo-grayscale` | desaturate embedded photos (auto in wireframe) |
| `--photo-scrub` | drop 2 LSBs per channel in embedded photos (anti-LSB-stego) |
| `--keep-photo-text` | keep OCR lines inside embedded photos (may double-draw) |
| `--strokes off\|auto\|all` | detect thin separators/borders (`auto` = long dividers/borders only) |
| `--fast` | quick preview; no font voting/icon tracing (embedded icons still apply) |
| `--quick` | preset: wireframe + fast + placeholder (smallest/fastest) |
| `--scale 2` | analysis downscale |
| `--k 14` | palette size |
| `--font-dir DIR` | extra `NotoSansSC*.ttf` / `SourceHanSans*` directory |
| `--icon-mode auto\|vector\|embed` | icon handling: `auto` = cut out for flat, trace for wireframe (default) |
| `--ocr-engine auto\|vision\|rapidocr\|hybrid` | OCR backend (`auto` = hybrid if both installed) |
| `--scene out.json` | editable intermediate scene graph |
| `--debug` | panel/image/baseline overlay |

## macOS app (preview)

A SwiftUI shell lives in [`mac/`](mac/README.md). It drags-and-drops
screenshots, calls the same Python pipeline through a bundled `distill-core`
worker, runs Vision OCR in-process and previews the SVG in `WKWebView`.

### Build from source

Requirements: macOS 13+, Xcode Command Line Tools (`swiftc`), `cmake`, `git`,
and Python 3.11+. The app build creates its own pinned Python venv; you do not
need to install this project's CLI dependencies into your daily environment.

```bash
cd distill-svg
./mac/build_app.sh
open dist/DistillSVG.app
```

On the first run the script clones OpenCV 4.13.0 and builds a minimal
`core+imgproc+imgcodecs` module (about 2 minutes, cached under
`build/opencv-min/`). It then freezes the Python core with PyInstaller
(onedir), compiles the SwiftUI shell, prunes unused Pillow codecs, strips the
shared libraries, ad-hoc signs every Mach-O file and writes both
`dist/DistillSVG.app` and `dist/DistillSVG-<version>-<arch>-full.dmg` (ULMO).

Useful build switches:

| switch | effect |
| --- | --- |
| `SKIP_CORE=1 ./mac/build_app.sh` | reuse the existing frozen core (fast UI iteration) |
| `BUNDLE_PYTHON=0 ./mac/build_app.sh` | BYO-Python build: no bundled runtime, ~1 MB app |
| `USE_MIN_OPENCV=0 ./mac/build_app.sh` | fall back to the official opencv-python wheel (larger) |
| `OPENCV_VERSION=4.13.0 ./mac/build_app.sh` | pin a different OpenCV source tag |
| `PRUNE=0 ./mac/build_app.sh` | skip the Pillow plugin/dylib prune |
| `STRIP=0 ./mac/build_app.sh` | skip `strip -x` (larger app) |
| `DMG_FORMAT=ULFO ./mac/build_app.sh` | faster DMG; default `ULMO` is smaller |
| `PYTHON=/path/to/python ./mac/build_app.sh` | use an explicit core Python |

The first OpenCV build needs network access and roughly 500 MB under
`build/opencv-min/`; later builds reuse the cache. Build artifacts (`build/`,
`dist/`, `mac/AppIcon.icns`) and local test screenshots (`tests/`, `out/`) are
git-ignored; the app icon is generated from `mac/make_icon.py` when missing.

The build is arm64 (when built on Apple Silicon) and **ad-hoc signed, not
notarized**; recipients must right-click → Open once. The bundle is
self-contained (no Python required): **~41 MB app / ~14 MB DMG**. A second
build mode, `BUNDLE_PYTHON=0`, produces a ~1 MB app / ~424 KB DMG that uses a
user-supplied Python 3.10+; install dependencies into it with the command
shown in the app's `转换 → Python 环境…` panel. GitHub Actions builds both
DMGs when a `v*` tag is pushed and publishes them as release assets. The app
itself does not use the network; only the first full build clones OpenCV
source. See [`mac/README.md`](mac/README.md) for the architecture, option
mapping, CI, signing/notarization and size breakdown.

## Output

The SVG contains:

- `<rect>` for background and flat panels/bubbles;
- `<text>` for all OCR text (selectable/copyable);
- `<path>` for traced icons/emoji/logos;
- `<line>` for detected separators/borders;
- `<image href="data:...">` for embedded photo crops (and, with
  `--icon-mode embed`, alpha-masked icon crops).

It never embeds the whole original screenshot, gradients, filters or external
URLs. Embedded photo crops are small rasters, so the file size and privacy
properties depend on `--photos`.

## Prototype results

Local test images (not committed):

| image | mode | SVG size | re-OCR median centre error | text similarity |
| --- | --- | --- | --- | --- |
| `1.png` 602×842 | hybrid + flat + embed | ~76 KiB | 1.5 px | 0.988 |
| `2.jpg` 1080×2306 | hybrid + flat + embed | ~111 KiB | 3.6 px | 0.980 |
| `2.jpg` 1080×2306 | wireframe | ~92 KiB | (same geometry) | — |
| `2.jpg` 1080×2306 | fast + placeholder | ~7 KiB | (same geometry) | — |
| `2.jpg` 1080×2306 | placeholder | ~39 KiB | (same geometry) | — |

Full-quality run on `2.jpg` takes about 3–4 s on an M-series laptop; `--fast`
takes roughly 1.5 s (and less if photos are placeholders). Most of the time is
OCR + font matching + icon tracing/cutouts + photo crop encoding.

> The flat-mode sizes above predate the icon-cutout default; current flat
> output is about 57 KiB for `1.png` and 167 KiB for `2.jpg`, with wireframe
> still using vector icons.

Reproduce the text/layout check and the visual diff:

```bash
node tools/render_svg.js out/2.flat.svg /tmp/render2.png 1080
python3 tools/evaluate.py tests/2.jpg /tmp/render2.png
python3 tools/compare.py tests/2.jpg /tmp/render2.png /tmp/cmp
```

## Known limitations

- OCR errors are kept rather than guessed around (missing letters, digit/
  button-label confusions; hybrid OCR usually improves them). The scene JSON
  exists so a human/editor can fix them without re-running CV.
- Icons are either traced from low-resolution pixels (recognisable, not
  pixel-perfect) or cut out with `--icon-mode embed` / the flat `auto` default.
  Cutouts are visually closer but add raster content and are not pure vector.
- Photos/avatars are cropped by a heuristic box; a party hat or badge may make
  the crop a little tight/loose.
- Logos may be traced, embedded as photo crops, or cut out as icons depending
  on their size and colour complexity.
- Gradients are not reconstructed yet (prototype only); shadows/blur are approximated with flat fills/1px strokes.
- Fonts differ across platforms. The SVG uses a font stack; outline/embedded
  font modes are future work.
- **Privacy**: embedded photo crops and icon cutouts retain the original
  pixels (including any hidden pixel-level data). Use `--photos placeholder`
  or `outline` (these force vector icons) if that matters, or `--photo-scrub`
  to drop low bits while keeping the visual content.

## Local / offline

Both the CLI and the macOS app are local: no network, no telemetry, OCR and
models on-device. The macOS SwiftUI shell is implemented in [`mac/`](mac/README.md);
Windows (WinUI 3) remains a design in [`docs/APP_ARCH.md`](docs/APP_ARCH.md);
Linux stays CLI-only. The shared core communicates through the scene JSON /
CLI contract and an NDJSON worker, so native shells do not reimplement the CV
pipeline.

## Documentation

- [`docs/RESEARCH.md`](docs/RESEARCH.md) — background, test-image findings,
  algorithms, OCR/font/SVG research, privacy, evaluation, roadmap.
- `docs/SOTA.md` — frontier research and tool landscape (subagent report).
- `docs/APP_ARCH.md` — local/native app architecture (macOS/Windows/Linux).
- `docs/ICON_MATCHING.md` — icon-library matching design and benchmark plan.

## Privacy note

The full-frame raster is never embedded; flat areas are quantised and rebuilt
from geometry. That destroys most full-frame pixel-level channels (including
the dither observed in the sample). It does **not** anonymise the content:
text, names, prices, logos and embedded photo crops are still reproduced.
Review the SVG before sharing.
