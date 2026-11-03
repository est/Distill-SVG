# 蒸矢 macOS app

A small SwiftUI shell around the existing Python pipeline. Drop a screenshot,
pick a style, preview the reconstructed SVG, save it. Everything runs locally.

This is the v1 of the shell described in [`docs/APP_ARCH.md`](../docs/APP_ARCH.md).

## Build

```bash
python3 -m pip install -r requirements.txt pyinstaller
./mac/build_app.sh
```

Outputs:

- `dist/DistillSVG.app` — double-clickable app (ad-hoc signed)
- `dist/DistillSVG-0.2.0-<arch>.dmg` — compressed copy + install notes

The build machine needs Xcode command-line tools (`swiftc`) and a Python with
`numpy`, `opencv-python`, `Pillow` and `PyInstaller` available as `python3`
(override with `PYTHON=/path/to/python ./mac/build_app.sh`).

Useful flags:

| flag | effect |
| --- | --- |
| `SKIP_CORE=1` | reuse `build/core/dist/distill-core` (fast UI iteration) |
| `STRIP=0` | skip `strip -x` (default is on; saves ~14 MB) |
| `PYTHON=...` | choose the Python used for the frozen core |

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
- **OCR** — the shell runs `VNRecognizeTextRequest` (`VisionOCR.swift`) and
  passes line boxes to the core. Therefore the bundled core does **not** need
  `swiftc`, the compile-on-first-run helper, RapidOCR or ONNX Runtime.
- **Core** — `distill_svg/api.py` is the shared pipeline; `cli.py` and
  `worker.py` are two thin front ends. The app talks to the worker with
  NDJSON over stdio (`hello`, `convert`, `progress`, `log`, `result`,
  `cancel`, `shutdown`). One worker lives for the whole session, so the
  expensive `cv2` import happens once.
- **Saving** — the worker writes into a per-session temp directory; “保存 SVG…”
  copies that file wherever the user picks. The scene JSON is kept as well and
  can be exported from the menu. The temp directory is removed on normal quit;
  leftovers from a crash are deleted after 24 hours on the next launch.

## CLI ↔ GUI options

| GUI | CLI |
| --- | --- |
| 扁平色 / 线框 | `--style flat|wireframe` |
| 嵌入 / 占位 / 轮廓 | `--photos embed|placeholder|outline` |
| 快速 / 完整 | `--fast` (on/off) |
| 分隔线 | `--strokes off|auto|all` |
| 描摹图标 | `--no-icons` |
| 照片灰度 / 去除低位像素 | `--photo-grayscale` / `--photo-scrub` |
| 保留照片内文字 | `--keep-photo-text` |
| 调色板 / 分析精度 | `--k` / `--scale` |
| 识别语言 | `--languages` |
| PNG/JPEG + 质量 + 最大边长 | `--photo-format` / `--photo-quality` / `--photo-max-size` |

The one intentional difference: the GUI always uses on-device Vision OCR, so
there is no `--ocr-engine hybrid` control. If both engines are wanted, use the
CLI.

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
| `.app` | ~138 MB |
| DMG (UDZO) | ~66 MB |
| OpenCV (`cv2`) | ~109 MB (79% of the app) |
| CPython dylib | 17 MB |
| Pillow | 12 MB |
| numpy | 6.6 MB |

Native Vision OCR removed RapidOCR + ONNX Runtime (roughly 50 MB) from the
bundle; `strip -x` removes another ~14 MB. The remaining lever is a
purpose-built OpenCV (no video/FFmpeg/text modules) — worth roughly 60 MB, but
it needs a custom build and CI, so it is not part of v1.

## Known limitations

- The bundle is arm64-only when built on an Apple Silicon Mac (PyInstaller
  cannot cross-compile). Build on an Intel Mac for an x86_64 app; universal2
  would require universal Python/OpenCV wheels.
- No batch queue and no scene-text editor yet. The scene JSON is exported so
  edits can be made externally.
- Vision OCR quality is good but differs from CLI `--ocr-engine hybrid`.
- Not sandboxed and not notarized; see Distribution.
