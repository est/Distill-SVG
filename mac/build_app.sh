#!/usr/bin/env bash
# Build 蒸矢 DistillSVG.app (SwiftUI shell + frozen Python core) and a DMG.
#
#   ./mac/build_app.sh            # build + ad-hoc sign + DMG
#   SKIP_CORE=1 ./mac/build_app.sh   # reuse build/core/dist/distill-core
#   STRIP=0 ./mac/build_app.sh   # skip stripping (larger, slower to sign)
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
PYTHON="${PYTHON:-python3}"
VERSION="0.2.0"

cd "$ROOT"

echo "==> checking toolchain"
command -v swiftc >/dev/null || { echo "swiftc not found; install Xcode command line tools" >&2; exit 1; }
ARCH="$("$PYTHON" -c 'import platform; print(platform.machine())')"
echo "python arch: $ARCH"
if [[ "$ARCH" != "arm64" ]]; then
    echo "warning: Python is $ARCH; the app will only run on that architecture." >&2
fi
MIN_MACOS="13.0"
TARGET="${ARCH}-apple-macos${MIN_MACOS}"

if [[ "${SKIP_CORE:-0}" != "1" ]]; then
    echo "==> building frozen core (PyInstaller onedir)"
    "$PYTHON" -m PyInstaller \
        --noconfirm --clean --onedir --name distill-core \
        --distpath "$BUILD/core/dist" \
        --workpath "$BUILD/core/work" \
        --specpath "$BUILD/core" \
        --paths "$ROOT" \
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
        "$MAC/core_entry.py"
fi

CORE="$BUILD/core/dist/distill-core"
[[ -x "$CORE/distill-core" ]] || { echo "core missing: $CORE" >&2; exit 1; }

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
mkdir -p "$APP/Contents/Resources/core"
cp -R "$CORE/." "$APP/Contents/Resources/core/"

if [[ "${STRIP:-1}" == "1" ]]; then
    echo "==> stripping Mach-O binaries"
    # Never strip the PyInstaller bootloader: the PKG archive is appended to
    # the executable and `strip` would truncate it.  Shared libraries and
    # extension modules are safe; they are re-signed below.
    while IFS= read -r -d '' file; do
        if file "$file" | grep -q "Mach-O"; then
            strip -x "$file" 2>/dev/null || true
        fi
    done < <(find "$APP/Contents/Resources/core/_internal" -type f -print0)
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
DMG="$DIST/DistillSVG-${VERSION}-${ARCH}.dmg"
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
  3. 要求 macOS 13+，Apple Silicon（arm64）。

说明
  · 转换全程在本机完成，不联网、无遥测。
  · 本构建未使用 Apple Developer ID 签名/公证，适合自用和小范围测试；
    公开分发前需要签名与公证，否则 Gatekeeper 会拦截。
  · 照片“嵌入”模式会把照片像素写进 SVG，分享前请确认内容。
TXT
hdiutil create -quiet -volname "蒸矢 DistillSVG" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
( cd "$DIST" && shasum -a 256 "$(basename "$DMG")" > SHA256SUMS )

echo
echo "built: $APP"
echo "dmg:   $DMG ($(du -h "$DMG" | cut -f1))"
