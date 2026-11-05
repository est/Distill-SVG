#!/usr/bin/env bash
# Build 蒸矢 DistillSVG.app (SwiftUI shell + frozen Python core) and a DMG.
#
#   ./mac/build_app.sh            # build + ad-hoc sign + DMG
#   SKIP_CORE=1 ./mac/build_app.sh   # reuse build/core/dist/distill-core
#   STRIP=0 ./mac/build_app.sh   # skip stripping (larger, slower to sign)
#   PRUNE=0 ./mac/build_app.sh   # skip Pillow/plugin pruning
#   DMG_FORMAT=ULFO ./mac/build_app.sh  # fastest DMG (default: ULMO, smallest)
#   USE_MIN_OPENCV=0 ./mac/build_app.sh # use the official opencv-python wheel
#   BUNDLE_PYTHON=0 ./mac/build_app.sh  # BYO Python (no bundled runtime)
#   PYTHON=... ./mac/build_app.sh       # explicit core Python (skips auto-build)
#
# The result is unsigned (ad-hoc) and therefore suitable for local testing and
# direct sharing only; Gatekeeper will warn recipients until the app is signed
# with a Developer ID certificate and notarized.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAC="$ROOT/mac"
BUILD="$ROOT/build"
DIST="$ROOT/dist"
APP="$DIST/DistillSVG.app"
VERSION="0.2.0"
DMG_FORMAT="${DMG_FORMAT:-ULMO}"
USE_MIN_OPENCV="${USE_MIN_OPENCV:-1}"
BUNDLE_PYTHON="${BUNDLE_PYTHON:-1}"

# Prefer the purpose-built OpenCV venv (core+imgproc+imgcodecs only, ~10 MB).
# Set PYTHON=... to use an explicit environment, or USE_MIN_OPENCV=0 to fall
# back to the official opencv-python wheel.
if [[ -z "${PYTHON:-}" ]]; then
    MIN_PY="$BUILD/opencv-min/venv/bin/python"
    if [[ "$BUNDLE_PYTHON" == "0" ]]; then
        PYTHON=python3
    elif [[ "$USE_MIN_OPENCV" == "1" ]]; then
        if [[ "${SKIP_CORE:-0}" == "1" && ! -x "$MIN_PY" ]]; then
            PYTHON=python3
        else
            if ! "$MIN_PY" -c 'import cv2,sys; sys.exit(0 if cv2.__version__ == "4.13.0" else 1)' 2>/dev/null; then
                "$MAC/build_opencv_min.sh"
            fi
            PYTHON="$MIN_PY"
        fi
    else
        PYTHON=python3
    fi
fi

cd "$ROOT"

echo "==> checking toolchain"
command -v swiftc >/dev/null || { echo "swiftc not found; install Xcode command line tools" >&2; exit 1; }
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "the app core needs Python >= 3.11"'
ARCH="$("$PYTHON" -c 'import platform; print(platform.machine())')"
echo "python arch: $ARCH"
if [[ "$ARCH" != "arm64" ]]; then
    echo "warning: Python is $ARCH; the app will only run on that architecture." >&2
fi
MIN_MACOS="13.0"
TARGET="${ARCH}-apple-macos${MIN_MACOS}"

if [[ "$BUNDLE_PYTHON" == "1" ]] && [[ "${SKIP_CORE:-0}" != "1" ]]; then
    echo "==> building frozen core (PyInstaller onedir)"
    "$PYTHON" -m PyInstaller \
        --noconfirm --clean --onedir --name distill-core \
        --distpath "$BUILD/core/dist" \
        --workpath "$BUILD/core/work" \
        --specpath "$BUILD/core" \
        --paths "$ROOT" \
        --optimize 2 \
        --hidden-import secrets \
        --exclude-module tkinter \
        --exclude-module rapidocr_onnxruntime \
        --exclude-module onnxruntime \
        --exclude-module matplotlib \
        --exclude-module scipy \
        --exclude-module pandas \
        --exclude-module PyQt5 \
        --exclude-module PySide6 \
        --exclude-module IPython \
        --exclude-module pytest \
        --exclude-module yaml \
        --exclude-module defusedxml \
        --exclude-module olefile \
        --exclude-module typing_extensions \
        --exclude-module charset_normalizer \
        --exclude-module asyncio \
        --exclude-module concurrent \
        --exclude-module email \
        --exclude-module http \
        --exclude-module xml \
        --exclude-module xmlrpc \
        --exclude-module urllib \
        --exclude-module unittest \
        --exclude-module doctest \
        --exclude-module pydoc \
        --exclude-module pydoc_data \
        --exclude-module pdb \
        --exclude-module numpy.f2py \
        --exclude-module numpy.testing \
        --exclude-module numpy._core._multiarray_tests \
        --exclude-module PIL.ImageTk \
        --exclude-module PIL.ImageQt \
        --exclude-module PIL.ImageShow \
        --exclude-module PIL.PdfParser \
        --exclude-module PIL.PdfImagePlugin \
        --exclude-module PIL.AvifImagePlugin \
        --exclude-module PIL.ImageCms \
        --exclude-module PIL.ImageMath \
        "$MAC/core_entry.py"
fi

if [[ "$BUNDLE_PYTHON" == "1" ]]; then
    CORE="$BUILD/core/dist/distill-core"
    [[ -x "$CORE/distill-core" ]] || { echo "core missing: $CORE" >&2; exit 1; }

    if [[ "${PRUNE:-1}" == "1" ]]; then
        echo "==> pruning unused Pillow plugins/codecs"
        "$PYTHON" "$MAC/prune_core.py" "$CORE"
    fi
fi

echo "==> compiling SwiftUI shell ($TARGET)"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$MAC/Info.plist" "$APP/Contents/Info.plist"

if [[ ! -f "$MAC/AppIcon.icns" ]]; then
    "$PYTHON" "$MAC/make_icon.py" "$MAC/AppIcon.icns"
fi
cp "$MAC/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

swiftc \
    -O -whole-module-optimization \
    -swift-version 5 \
    -target "$TARGET" \
    -parse-as-library \
    "$MAC"/Sources/*.swift \
    -o "$APP/Contents/MacOS/DistillSVG"

echo "==> bundling core"
APPCORE="$APP/Contents/Resources/core"
mkdir -p "$APPCORE"
if [[ "$BUNDLE_PYTHON" == "1" ]]; then
    cp -R "$CORE/." "$APPCORE/"
    if [[ -f "$BUILD/opencv-min/opencv/LICENSE" ]]; then
        cp "$BUILD/opencv-min/opencv/LICENSE" "$APPCORE/LICENSE-opencv.txt"
    fi
    # Do not ship build-machine paths in the OpenCV binary.  `strip` and code
    # signing do not remove string constants; fail the build if one slips in.
    CV2_SO="$APPCORE/_internal/cv2/cv2.abi3.so"
    if [[ -f "$CV2_SO" ]] && grep -aEq '/(Users|home)/' "$CV2_SO"; then
        echo "error: build-machine path found in $CV2_SO (refusing to ship)" >&2
        exit 1
    fi
else
    echo "==> BYO Python: bundling source instead of a frozen runtime"
    mkdir -p "$APPCORE/python"
    rsync -a --exclude '__pycache__' --exclude '*.pyc' "$ROOT/distill_svg" "$APPCORE/python/"
    cp "$ROOT/mac/requirements-python.txt" "$APPCORE/python/requirements.txt"
    cat > "$APPCORE/core.json" <<'JSON'
{"mode":"external","source":"python","requirements":"python/requirements.txt"}
JSON
    cat > "$APPCORE/README-python.txt" <<'TXT'
This is the BYO Python build of DistillSVG.

The app does not bundle Python. Install the dependencies into any Python 3.10+
environment you want to use:

  /path/to/python3 -m pip install -r "<App>/Contents/Resources/core/python/requirements.txt"

Then choose that interpreter in the app:
  menu 转换 → Python 环境…  (or the error banner's "设置 Python…")
The path is remembered in UserDefaults.
TXT
fi

if [[ "${STRIP:-1}" == "1" ]]; then
    echo "==> stripping Mach-O binaries"
    # Never strip the PyInstaller bootloader: the PKG archive is appended to
    # the executable and `strip` would truncate it.  Shared libraries and
    # extension modules are safe; they are re-signed below.
    if [[ "$BUNDLE_PYTHON" == "1" ]]; then
        while IFS= read -r -d '' file; do
            if file "$file" | grep -q "Mach-O"; then
                strip -x "$file" 2>/dev/null || true
            fi
        done < <(find "$APP/Contents/Resources/core/_internal" -type f -print0)
    fi
    strip -x "$APP/Contents/MacOS/DistillSVG" 2>/dev/null || true
fi

echo "==> ad-hoc signing"
# Sign inner Mach-O files first: --deep is convenient but deprecated and
# notoriously incomplete; explicit nested signing is what a future Developer
# ID build will need anyway.
while IFS= read -r -d '' file; do
    if file "$file" | grep -q "Mach-O"; then
        codesign --force --sign - --timestamp=none "$file" >/dev/null 2>&1 || true
    fi
done < <(find "$APP/Contents/Resources/core" -type f -print0)
codesign --force --sign - --timestamp=none "$APP/Contents/MacOS/DistillSVG"
codesign --force --sign - --timestamp=none "$APP"
codesign --verify --deep --strict "$APP" && echo "signature: ok (ad-hoc)"

echo "==> app size"
du -sh "$APP"

echo "==> packaging DMG"
mkdir -p "$DIST"
if [[ "$BUNDLE_PYTHON" == "1" ]]; then
    DMG_SUFFIX="-full"
else
    DMG_SUFFIX="-python"
fi
DMG="$DIST/DistillSVG-${VERSION}-${ARCH}${DMG_SUFFIX}.dmg"
rm -f "$DMG"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
cat > "$STAGE/安装说明.txt" <<'TXT'
蒸矢 DistillSVG 0.2.0（未签名测试版）

安装
  1. 把 DistillSVG.app 拖进「应用程序」文件夹。
  2. 首次打开若提示“无法验证开发者”：
     在 Finder 中右键 App →「打开」→ 再点「打开」。
     或执行：xattr -dr com.apple.quarantine /Applications/DistillSVG.app
  3. 要求 macOS 13+。
TXT
if [[ "$BUNDLE_PYTHON" == "0" ]]; then
    cat >> "$STAGE/安装说明.txt" <<'TXT'

这个 DMG 是 BYO Python 版本（不打包 Python）
  · 需要一个 Python 3.10+，并在其中安装依赖：
      /path/to/python3 -m pip install -r <App>/Contents/Resources/core/python/requirements.txt
  · 打开 App 后使用「转换 → Python 环境…」选择该解释器。
  · 也可以在启动前设置 DISTILL_SVG_PYTHON=/path/to/python3。
TXT
else
    cat >> "$STAGE/安装说明.txt" <<'TXT'

这个 DMG 是全自带版本（无需本机 Python）
  · 转换全程在本机完成，不联网、无遥测。
  · 照片“嵌入”模式会把照片像素写进 SVG，分享前请确认内容。
TXT
fi
cat >> "$STAGE/安装说明.txt" <<'TXT'

本构建未使用 Apple Developer ID 签名/公证，适合自用和小范围测试；
公开分发前需要签名与公证，否则 Gatekeeper 会拦截。
TXT
# ULMO (LZMA) gives the smallest download but direct creation is unstable;
# create a deterministic UDZO master and convert it.  ULFO is a faster,
# slightly larger option; UDZO remains available for maximum compatibility.
if [[ "$DMG_FORMAT" == "ULMO" ]]; then
    MASTER="$DIST/.DistillSVG-master.dmg"
    rm -f "$MASTER"
    hdiutil create -quiet -volname "蒸矢 DistillSVG" -srcfolder "$STAGE" -ov -format UDZO "$MASTER"
    hdiutil convert -quiet "$MASTER" -format ULMO -o "$DMG"
    rm -f "$MASTER"
else
    hdiutil create -quiet -volname "蒸矢 DistillSVG" -srcfolder "$STAGE" -ov -format "$DMG_FORMAT" "$DMG"
fi
rm -rf "$STAGE"
( cd "$DIST" && shasum -a 256 *.dmg > SHA256SUMS )
hdiutil verify "$DMG" >/dev/null && echo "dmg: verified"

echo
echo "built: $APP"
echo "dmg:   $DMG ($(du -h "$DMG" | cut -f1))"
