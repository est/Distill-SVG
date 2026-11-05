#!/usr/bin/env bash
# Build the minimal OpenCV used by the frozen core.
#
# The app needs only core + imgproc + imgcodecs.  The official opencv-python
# wheel bundles a ~75 MB FFmpeg/tesseract/video stack; this build removes it.
# A full build takes roughly 2 minutes after the source/venv are warm.
#
#   ./mac/build_opencv_min.sh          # idempotent; skips if cv2 4.13 exists
#   FORCE=1 ./mac/build_opencv_min.sh  # rebuild
#   OPENCV_VERSION=4.13.0 ./mac/build_opencv_min.sh
#
# It creates build/opencv-min/venv and installs the module into that venv,
# along with the pinned build requirements.  Then use that venv for the core:
#
#   PYTHON="$PWD/build/opencv-min/venv/bin/python" ./mac/build_app.sh
#
# build_app.sh does the above automatically when USE_MIN_OPENCV=1 (default).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build/opencv-min"
SRC="$BUILD/opencv"
VENV="$BUILD/venv"
INSTALL="$BUILD/install"
OPENCV_VERSION="${OPENCV_VERSION:-4.13.0}"
PYTHON_BASE="${PYTHON_BASE:-python3}"

# This developer shell runs under Rosetta (x86_64) on Apple Silicon.  CMake
# would then generate SSE/AVX flags and produce an x86_64 module that cannot
# load in the arm64 app.  Re-exec the whole script natively.
if [[ "$(uname -m)" == "x86_64" ]] \
   && [[ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" == "1" ]] \
   && [[ "${OCV_MIN_NATIVE:-0}" != "1" ]]; then
    export OCV_MIN_NATIVE=1
    exec arch -arm64 /bin/bash "$0" "$@"
fi

ARCH="$(uname -m)"
"$PYTHON_BASE" -c 'import sys; assert sys.version_info >= (3, 11), "the app build needs Python >= 3.11 (numpy 2.4 pin)"'
echo "==> OpenCV $OPENCV_VERSION ($ARCH), Python $("$PYTHON_BASE" -c 'import platform; print(platform.python_version())')"
command -v git >/dev/null || { echo "git not found" >&2; exit 1; }
command -v cmake >/dev/null || { echo "cmake not found" >&2; exit 1; }

READY_MARKER="$VENV/.cv2-min-ready"
if [[ -x "$VENV/bin/python" ]] && [[ -f "$READY_MARKER" ]] && [[ "${FORCE:-0}" != "1" ]]; then
    if "$VENV/bin/python" -c 'import cv2,os,sys; p=os.path.dirname(cv2.__file__); sys.exit(0 if cv2.__version__ == "4.13.0" and os.path.exists(os.path.join(p,"cv2.abi3.so")) else 1)' 2>/dev/null; then
        echo "==> already built: $VENV"
        "$VENV/bin/python" -c 'import cv2; print("cv2", cv2.__version__, cv2.__file__)'
        exit 0
    fi
fi

if [[ "${FORCE:-0}" == "1" ]]; then
    rm -rf "$VENV" "$INSTALL" "$SRC"
fi
rm -f "$VENV/.cv2-min-ready"

if [[ ! -x "$VENV/bin/python" ]]; then
    echo "==> creating venv"
    "$PYTHON_BASE" -m venv "$VENV"
fi
echo "==> installing build requirements"
"$VENV/bin/pip" install -q -r "$ROOT/mac/requirements-core.txt"
export PATH="$VENV/bin:$PATH"

if [[ ! -d "$SRC/.git" ]]; then
    echo "==> cloning OpenCV $OPENCV_VERSION"
    mkdir -p "$BUILD"
    git clone --depth 1 --branch "$OPENCV_VERSION" https://github.com/opencv/opencv.git "$SRC"
fi

PY_INCLUDE="$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["include"])')"
PY_LIBDIR="$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')"
PY_LDLIB="$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_config_var("LDLIBRARY"))')"
PY_LIB="$PY_LIBDIR/$PY_LDLIB"
PY_SITE="$("$VENV/bin/python" -c 'import site; print(site.getsitepackages()[0])')"
NUMPY_INC="$("$VENV/bin/python" -c 'import numpy; print(numpy.get_include())')"
command -v ninja >/dev/null 2>&1 || echo "warning: ninja not on PATH, using Makefiles" >&2

GEN=()
if command -v ninja >/dev/null 2>&1; then
    GEN=(-G Ninja)
fi

echo "==> configuring"
# Keep build-machine paths out of __FILE__ strings (privacy/reproducibility).
SOURCE_MAP="-ffile-prefix-map=$SRC=/opencv -ffile-prefix-map=$BUILD=/opencv-build"
cmake -S "$SRC" -B "$BUILD/opencv-build" "${GEN[@]}" \
    -DCMAKE_C_FLAGS="$SOURCE_MAP" \
    -DCMAKE_CXX_FLAGS="$SOURCE_MAP" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_OSX_ARCHITECTURES="$ARCH" \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0 \
    -DCMAKE_INSTALL_PREFIX="$INSTALL" \
    -DBUILD_LIST=core,imgproc,imgcodecs,python3 \
    -DBUILD_SHARED_LIBS=OFF \
    -DBUILD_opencv_python3=ON \
    -DPYTHON3_EXECUTABLE="$VENV/bin/python" \
    -DPYTHON3_INCLUDE_DIR="$PY_INCLUDE" \
    -DPYTHON3_LIBRARY="$PY_LIB" \
    -DPYTHON3_LIMITED_API=ON \
    -DPYTHON3_PACKAGES_PATH="$PY_SITE" \
    -DPYTHON3_NUMPY_INCLUDE_DIRS="$NUMPY_INC" \
    -DWITH_FFMPEG=OFF -DWITH_GSTREAMER=OFF -DWITH_AVFOUNDATION=OFF \
    -DWITH_OPENCL=OFF -DWITH_IPP=OFF -DWITH_TBB=OFF -DWITH_OPENMP=OFF \
    -DWITH_PNG=ON -DWITH_JPEG=ON -DWITH_WEBP=ON \
    -DWITH_TIFF=OFF -DWITH_OPENEXR=OFF -DWITH_AVIF=OFF \
    -DWITH_JPEGXL=OFF -DWITH_JASPER=OFF -DWITH_OPENJPEG=OFF \
    -DWITH_PROTOBUF=OFF -DWITH_QUIRC=OFF -DWITH_ADE=OFF \
    -DWITH_1394=OFF -DWITH_GTK=OFF -DWITH_QT=OFF -DWITH_COCOA=OFF \
    -DWITH_LAPACK=OFF -DWITH_EIGEN=OFF -DWITH_ITT=OFF \
    -DWITH_IMGCODEC_GIF=OFF -DWITH_IMGCODEC_HDR=OFF \
    -DWITH_IMGCODEC_SUNRASTER=OFF -DWITH_IMGCODEC_PXM=OFF \
    -DWITH_IMGCODEC_PFM=OFF -DWITH_IMGCODEC_AVIF=OFF \
    -DBUILD_TESTS=OFF -DBUILD_PERF_TESTS=OFF -DBUILD_EXAMPLES=OFF \
    -DBUILD_opencv_apps=OFF -DBUILD_JAVA=OFF -DBUILD_opencv_world=OFF

# OpenCV normally embeds its full CMake configuration (absolute source, venv
# and install paths) into `cv::getBuildInformation()`.  Replace it with a
# neutral string; the app never calls it, and this keeps build paths out of the
# shipped binary.
VERSION_INC="$BUILD/opencv-build/modules/core/version_string.inc"
mkdir -p "$(dirname "$VERSION_INC")"
cat > "$VERSION_INC" <<'EOF'
"OpenCV 4.13.0 (minimal distill-svg build)\n"
EOF

echo "==> building (this is the long part)"
cmake --build "$BUILD/opencv-build" --target install --parallel "$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"

# OpenCV's source install keeps the extension in cv2/python-3/ and hard-codes
# the build-time path in config-3.py.  Both break PyInstaller's cv2 hook.  Move
# the extension to the package root (wheel layout) and make config-3.py point
# at the package directory.
CV2_DIR="$PY_SITE/cv2"
if [[ -f "$CV2_DIR/python-3/cv2.abi3.so" ]]; then
    mv "$CV2_DIR/python-3/cv2.abi3.so" "$CV2_DIR/cv2.abi3.so"
    rmdir "$CV2_DIR/python-3" 2>/dev/null || true
fi
CFG="$CV2_DIR/config-3.py"
if [[ -f "$CFG" ]]; then
    cat > "$CFG" <<'PYEOF'
import os
_here = os.path.dirname(os.path.abspath(__file__))
PYTHON_EXTENSIONS_PATHS = [_here] + PYTHON_EXTENSIONS_PATHS
PYEOF
fi
CFG2="$CV2_DIR/config.py"
if [[ -f "$CFG2" ]]; then
    cat > "$CFG2" <<'PYEOF'
import os
BINARIES_PATHS = [] + BINARIES_PATHS
PYEOF
fi

if [[ ! -d "$PY_SITE/cv2" ]]; then
    FOUND="$(find "$INSTALL" -path '*/cv2/__init__.py' -print -quit 2>/dev/null || true)"
    if [[ -n "$FOUND" ]]; then
        cp -R "$(dirname "$FOUND")" "$PY_SITE/"
    fi
fi
[[ -d "$PY_SITE/cv2" ]] || { echo "cv2 package not installed into $PY_SITE" >&2; exit 1; }

echo "==> verifying"
"$VENV/bin/python" - <<'PY'
import cv2
print("cv2", cv2.__version__, cv2.__file__)
assert cv2.__version__ == "4.13.0", cv2.__version__
for name in ("imread", "imencode", "imwrite", "resize", "cvtColor", "kmeans",
             "connectedComponentsWithStats", "findContours", "morphologyEx",
             "GaussianBlur", "medianBlur", "blur", "approxPolyDP", "contourArea",
             "convexHull", "line", "rectangle", "transpose", "dilate", "erode"):
    assert hasattr(cv2, name), name
PY
du -sh "$PY_SITE/cv2"
"$VENV/bin/python" -c 'import cv2; print("ready:", cv2.__file__)'

touch "$READY_MARKER"
