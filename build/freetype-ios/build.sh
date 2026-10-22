#!/bin/bash
# Build freetype static for iOS arm64 — consumed by build/win32u-unix/build.sh,
# which compiles freetype_ios.c against these headers and merges
# build/libfreetype.a into libwin32u_unix.a (no Xcode project changes).
#
# Source: shallow clone of freetype 2.13.3 in research/freetype
#   git clone --depth 1 --branch VER-2-13-3 https://github.com/freetype/freetype.git research/freetype
# All optional deps disabled — fonts are plain TTFs from wine/fonts/.
set -e
# MAD_SDK picks the iOS SDK: iphoneos (the app) or iphonesimulator (the
# simulator test harness, .github/workflows/sim-seh.yml).
MAD_SDK="${MAD_SDK:-iphoneos}"
case "$MAD_SDK" in
    iphonesimulator) MAD_MIN=-mios-simulator-version-min=17.0 ;;
    *) MAD_MIN=-miphoneos-version-min=17.0 ;;
esac

BUILD_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$BUILD_DIR/../.." && pwd)"
SRC="$REPO_ROOT/research/freetype"

[ -d "$SRC" ] || { echo "ERROR: clone freetype first (see header)"; exit 1; }

cmake -S "$SRC" -B "$BUILD_DIR/build" -G "Unix Makefiles" \
  -DCMAKE_SYSTEM_NAME=iOS \
  -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_OSX_DEPLOYMENT_TARGET=17.0 \
  -DCMAKE_OSX_SYSROOT="$(xcrun --sdk "$MAD_SDK" --show-sdk-path)" \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF \
  -DFT_DISABLE_ZLIB=ON -DFT_DISABLE_BZIP2=ON -DFT_DISABLE_PNG=ON \
  -DFT_DISABLE_HARFBUZZ=ON -DFT_DISABLE_BROTLI=ON \
  -DCMAKE_C_FLAGS="-fno-stack-protector"

cmake --build "$BUILD_DIR/build" -j8
echo "Done: $BUILD_DIR/build/libfreetype.a"
