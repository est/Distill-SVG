# 蒸矢 macOS app

A small SwiftUI shell around the existing Python pipeline. Drop a screenshot,
pick a style, preview the reconstructed SVG, save it. Everything runs locally.

This is the v1 of the shell described in [`docs/APP_ARCH.md`](../docs/APP_ARCH.md).

## Build

```bash
./mac/build_app.sh
open dist/DistillSVG.app
```

Outputs:

- `dist/DistillSVG.app` — double-clickable app (ad-hoc signed)
- `dist/DistillSVG-0.2.0-<arch>-full.dmg` — full self-contained DMG
- `BUNDLE_PYTHON=0 ./mac/build_app.sh` — BYO-Python DMG
  `dist/DistillSVG-0.2.0-<arch>-python.dmg` (~424 KB, no runtime)

The build machine needs macOS 13+, Xcode command-line tools (`swiftc`),
`cmake`, `git`, and Python 3.11+ (3.14 tested). The script creates
`build/opencv-min/venv` from the pinned `mac/requirements-core.txt`, clones and
builds the minimal OpenCV (first run only), then uses that venv for PyInstaller.

Useful flags:

| flag | effect |
| --- | --- |
| `SKIP_CORE=1` | reuse `build/core/dist/distill-core` (fast UI iteration) |
| `BUNDLE_PYTHON=0` | BYO-Python build: bundle source, not a runtime; DMG gets a `-python` suffix |
| `USE_MIN_OPENCV=0` | use the official opencv-python wheel (larger fallback) |
| `OPENCV_VERSION=4.13.0` | OpenCV source tag; default 4.13.0 |
| `FORCE=1` | rebuild `build/opencv-min` from scratch |
| `PRUNE=0` | skip the Pillow plugin/dylib prune (default saves ~4.5 MB) |
| `STRIP=0` | skip `strip -x` (default is on; saves ~14 MB) |
| `DMG_FORMAT=ULFO` | faster DMG; default `ULMO` is smaller but slower to create/mount |
| `PYTHON=...` | choose the Python used for the frozen core (skips auto-build) |

## How it is put together

```
DistillSVG.app/
  Contents/MacOS/DistillSVG          SwiftUI shell (arm64)
  Contents/Resources/core/
    distill-core                     PyInstaller onedir launcher
    _internal/                       CPython + numpy + OpenCV + Pillow
  Contents/Resources/AppIcon.icns
```

- **UI** — SwiftUI single window: drag-and-drop/preview on the left, options on
  the right. The result is rendered in `WKWebView` with JavaScript disabled;
  the original image is shown natively. The default preview is fit-to-width
  with vertical scrolling, which keeps long mobile screenshots readable.
  Screenshots can also be pasted from the clipboard (⌘V; text fields keep
  their normal paste behavior), and ⌘N clears the current document without
  restarting the app.
- **OCR** — the shell runs `VNRecognizeTextRequest` (`VisionOCR.swift`) and
  passes line boxes to the core. Therefore the bundled core does **not** need
  `swiftc`, the compile-on-first-run helper, RapidOCR or ONNX Runtime.
- **Core** — `distill_svg/api.py` is the shared pipeline; `cli.py` and
  `worker.py` are two thin front ends. The app talks to the worker with
  NDJSON over stdio (`hello`, `convert`, `progress`, `log`, `result`,
  `cancel`, `shutdown`). One worker lives for the whole session, so the
  expensive `cv2` import happens once.
- **Minimal OpenCV** — `mac/build_opencv_min.sh` builds OpenCV 4.13.0 with
  only `core`, `imgproc`, `imgcodecs` and Python bindings. No video/FFmpeg,
  highgui, dnn, AVIF, TIFF or OpenEXR. Input support is deliberately limited
  to PNG/JPEG/WebP, which is what the app accepts. The resulting `cv2` is
  ~8.6 MB inside the app instead of ~97 MB. The generated OpenCV data-file
  lookup paths are rewritten to neutral `/opencv-min/...` values, and
  `build_app.sh` refuses to ship if a `/Users/` or `/home/` path remains in
  `cv2.abi3.so` (strip and codesign do not remove string constants).
- **Pillow prune** — `mac/prune_core.py` removes Pillow image-codec plugins and
  private dylibs that only the unused image I/O would need. Font rendering
  (`_imaging`/`_imagingft`) and its dependency closure are protected.
- **Notices** — the custom OpenCV build is Apache-2.0; its bundled image codecs
  retain their own licenses. `mac/build_app.sh` copies OpenCV's `LICENSE` into
  the app; review the third-party notices before public distribution.
- **Saving** — the worker writes into a per-session temp directory; “保存 SVG…”
  copies that file wherever the user picks. The scene JSON is kept as well and
  can be exported from the menu. The temp directory is removed on normal quit;
  leftovers from a crash are deleted after 24 hours on the next launch.

## Two build flavours

| | full (default) | BYO Python (`BUNDLE_PYTHON=0`) |
| --- | --- | --- |
| app / DMG | ~41 MB / ~14 MB | ~1.0 MB / ~424 KB |
| runtime | bundled CPython + minimal OpenCV | user-supplied Python 3.10+ |
| packages | pinned in `mac/requirements-core.txt` | `mac/requirements-python.txt`, installed by the user |
| UI | ready to run | `转换 → Python 环境…` selects the interpreter |

The BYO build ships `distill_svg/` and `requirements.txt` under
`Contents/Resources/core/python/`. It starts:

```bash
PYTHONPATH=... /path/to/python3 -m distill_svg.worker
```

The selected path is stored in UserDefaults; `DISTILL_SVG_PYTHON` is also
honoured. The app copies the exact pip command to the clipboard; it does not
silently install packages or fetch a runtime. PNG/JPEG/WebP only, same as the
full build.

## GitHub Actions

[`.github/workflows/macos.yml`](../.github/workflows/macos.yml) builds **only
when a `v*` tag is pushed**, on `macos-15` (arm64):

1. full self-contained DMG (`...-full.dmg`)
2. BYO-Python DMG (`...-python.dmg`)
3. verifies both DMGs and writes `SHA256SUMS`
4. uploads **two separate artifacts** (`...-full`, `...-python`); each
   artifact is a zip containing its `.dmg` and `SHA256SUMS`
5. creates a GitHub release with the two raw `.dmg` files as direct assets

A normal push to `main` does not run this workflow. To release:

```bash
git tag v0.2.0
git push origin v0.2.0
```

The workflow caches the OpenCV source only; the venv/build tree is rebuilt to
avoid caching absolute paths. The full build needs network for the first
OpenCV clone; the BYO build does not download Python packages.

## CLI ↔ GUI options

| GUI | CLI |
| --- | --- |
| 扁平色 / 线框 | `--style flat|wireframe` |
| 嵌入 / 占位 / 轮廓 | `--photos embed|placeholder|outline` |
| 快速 / 完整 | `--fast` (on/off) |
| 分隔线 | `--strokes off|auto|all` |
| 图标：自动 / 矢量化 / 抠图嵌入 / 不输出 | `--icon-mode auto\|vector\|embed`, `--icons`, `--no-icons` |
| 照片灰度 / 去除低位像素 | `--photo-grayscale` / `--photo-scrub` |
| 保留照片内文字 | `--keep-photo-text` |
| 调色板 / 分析精度 | `--k` / `--scale` |
| 识别语言 | `--languages` |
| PNG/JPEG + 质量 + 最大边长 | `--photo-format` / `--photo-quality` / `--photo-max-size` |

The one intentional difference: the GUI always uses on-device Vision OCR, so
there is no `--ocr-engine hybrid` control. If both engines are wanted, use the
CLI.

## Icon mode (flat vs wireframe)

`--icon-mode auto` is the default:

- **flat** → icons/emoji are cut out of the original screenshot with a
  colour-distance alpha matte and embedded as PNG. Visually much closer than
  tracing, especially for thin outline icons and emoji.
- **wireframe** → vector tracing, preserving the line-art look.

Measured on the local test images (flat, 24 and 16 icon components):

| image | vector size | cutout size | icon-region SSIM | icon-region MAE |
| --- | --- | --- | --- | --- |
| `1.png` | 89 KiB | 57 KiB | 0.70 → 0.97 | 10.1 → 3.3 |
| `2.jpg` | 127 KiB | 167 KiB | 0.72 → 0.98 | 16.9 → 1.5 |

Use `--icon-mode vector` for pure-vector icons, or `--icon-mode embed` to force
cutouts even in wireframe (`--photos embed` required). `--photos placeholder`
or `outline` forces vector icons so the privacy modes never carry raster icon
pixels. The extra PNG payload is large on emoji-heavy screenshots; that is the
main trade-off.

## Distribution

The build is **ad-hoc signed, not notarized**. Locally built apps open
normally; once the DMG is downloaded by someone else Gatekeeper will block the
first launch. The DMG includes instructions:

- right-click the app → Open → Open, or
- `xattr -dr com.apple.quarantine /Applications/DistillSVG.app`

For public distribution, use a Developer ID Application certificate, sign every
Mach-O file (the build script already signs nested binaries explicitly),
`xcrun notarytool submit --wait`, then `xcrun stapler staple`. The app does not
request network access and has no telemetry; if sandboxing is enabled later,
the sandbox inherited-entitlement rules for the helper still apply.

## Measured size (arm64 build, 2026-11)

| component | size |
| --- | --- |
| `.app` | **~41 MB** |
| DMG (ULMO) | **~14 MB** |
| BYO `.app` / DMG | **~1.0 MB / ~424 KB** |
| OpenCV (`cv2`) | 8.6 MB (20% of the app; was 97 MB) |
| CPython dylib | 16 MB |
| numpy | 6.2 MB |
| Pillow (after prune) | 5.8 MB |
| PyInstaller bootloader | 5.5 MB |
| base_library + other | ~2 MB |

The size reduction came from three independent steps:

1. Native Vision removed RapidOCR + ONNX Runtime (~50 MB).
2. A purpose-built OpenCV without video/FFmpeg/dnn removed ~88 MB from `cv2`.
3. `strip -x` (~14 MB), the Pillow prune (~4.5 MB), and ULMO DMG compression
   (66 → 14 MB) handle the rest.

`strip -S` adds nothing; `strip -N` breaks code signatures and startup. It is
not used.

## Known limitations

- The bundle is arm64-only when built on an Apple Silicon Mac (PyInstaller
  cannot cross-compile). Build on an Intel Mac for an x86_64 app; universal2
  would require universal Python/OpenCV wheels.
- No batch queue and no scene-text editor yet. The scene JSON is exported so
  edits can be made externally.
- Flat output embeds icon cutouts by default (`--icon-mode auto`); use
  `--icon-mode vector` for pure-vector icons. Placeholder/outline photo modes
  force vector icons so privacy mode never carries raster icon pixels.
- Vision OCR quality is good but differs from CLI `--ocr-engine hybrid`.
- The minimal OpenCV intentionally lacks video, AVIF, TIFF, OpenEXR and other
  codecs. If a future feature needs them, re-enable the relevant flags in
  `mac/build_opencv_min.sh` and expect the bundle to grow.
- Not sandboxed and not notarized; see Distribution.
