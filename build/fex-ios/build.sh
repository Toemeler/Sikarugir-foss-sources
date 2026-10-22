#!/bin/bash
# Configure (first time) and build the FEXCore static libraries the app links
# (FEX/build-ios/FEXCore/Source/*.a and External/*). Options mirror the
# development build's CMakeCache, plus the target processor and TUNE_CPU:
# FEX's default, "native", probes the build host (/proc/cpuinfo) and aborts
# the configure on macOS; cortex-a78 is what build/fex-arm64ec uses too.
set -eu
# MAD_SDK picks the iOS SDK: iphoneos (the app) or iphonesimulator (the
# simulator test harness, .github/workflows/sim-seh.yml).
MAD_SDK="${MAD_SDK:-iphoneos}"
case "$MAD_SDK" in
    iphonesimulator) MAD_MIN=-mios-simulator-version-min=17.0 ;;
    *) MAD_MIN=-miphoneos-version-min=17.0 ;;
esac
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
B="$R/FEX/build-ios"
if [ ! -f "$B/CMakeCache.txt" ]; then
    cmake -S "$R/FEX" -B "$B" -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_SYSTEM_PROCESSOR=arm64 -DCMAKE_OSX_ARCHITECTURES=arm64 \
        -DCMAKE_OSX_SYSROOT="$MAD_SDK" -DCMAKE_OSX_DEPLOYMENT_TARGET=17.0 -DCMAKE_BUILD_TYPE=Release -DTUNE_CPU=cortex-a78 \
        -DBUILD_TESTING=OFF -DBUILD_THUNKS=OFF -DBUILD_FEXCONFIG=OFF -DBUILD_FEX_LINUX_TESTS=OFF \
        -DENABLE_FEX_ALLOCATOR=OFF -DENABLE_ASSERTIONS=OFF -DENABLE_CLANG_THUNKS=ON -DENABLE_CCACHE=ON
fi
# JemallocLibs only feeds FEXCore_shared, so it is named: the app links it.
cmake --build "$B" --target FEXCore FEXCore_Base JemallocLibs
ls "$B/FEXCore/Source/"*.a
