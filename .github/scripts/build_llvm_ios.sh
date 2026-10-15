#!/usr/bin/env bash
# Build LLVM 15.0.7 as static iOS arm64 libraries for DXMT's shader compiler
# (build/dxmt-ios/build.sh). It is the one Madeira input that takes hours and
# is not in the repository (docs/BUILDING.md, build/dxmt-ios/README.md), so the
# workflow builds it in its own job and caches the result.
#
# Produces, under toolchains/ (what build/dxmt-ios/build.sh reads):
#   llvm-project/llvm/include   LLVM's source headers
#   llvm-ios-build/include      generated headers (llvm/Config/*.h, *.inc)
#   llvm-ios-build/lib/*.a      the static libraries
# Everything else is deleted afterwards, so the cache stays small.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TC="$ROOT/toolchains"
VERSION=15.0.7
TARBALL="llvm-project-$VERSION.src.tar.xz"
SHA256=8b5fcb24b4128cf04df1b0b9410ce8b1a729cb3c544e6da885d234280dedeac6
JOBS=$(sysctl -n hw.ncpu)

mkdir -p "$TC"
cd "$TC"
rm -rf llvm-project llvm-host-build llvm-ios-build

curl -fsSL --retry 3 -o "$TARBALL" \
  "https://github.com/llvm/llvm-project/releases/download/llvmorg-$VERSION/$TARBALL"
echo "$SHA256  $TARBALL" | shasum -a 256 -c -
# llvm/ and cmake/ are all that is needed: LLVM 15 reads its shared CMake
# modules from ../cmake.
tar -xJf "$TARBALL" "llvm-project-$VERSION.src/llvm" "llvm-project-$VERSION.src/cmake"
mv "llvm-project-$VERSION.src" llvm-project
rm "$TARBALL"

# The one source edit build/dxmt-ios/README.md records: Apple's linker wants
# -dead_strip, and CMake names the platform "iOS", not "Darwin".
python3 - "$TC/llvm-project/llvm/cmake/modules/AddLLVM.cmake" <<'PY'
import sys
path = sys.argv[1]
text = open(path).read()
old = 'if(${CMAKE_SYSTEM_NAME} MATCHES "Darwin")\n        # ld64\'s implementation of -dead_strip'
new = 'if(${CMAKE_SYSTEM_NAME} MATCHES "Darwin|iOS")\n        # ld64\'s implementation of -dead_strip'
if old not in text:
    sys.exit("AddLLVM.cmake: the -dead_strip check to patch was not found")
open(path, "w").write(text.replace(old, new, 1))
print("patched AddLLVM.cmake (-dead_strip for iOS)")
PY

COMMON=(
  -G Ninja
  -DCMAKE_BUILD_TYPE=Release
  -DLLVM_TARGETS_TO_BUILD=
  -DLLVM_ENABLE_PROJECTS=
  -DLLVM_INCLUDE_TESTS=Off
  -DLLVM_INCLUDE_BENCHMARKS=Off
  -DLLVM_INCLUDE_EXAMPLES=Off
  -DLLVM_INCLUDE_DOCS=Off
  -DLLVM_ENABLE_ZLIB=Off
  -DLLVM_ENABLE_ZSTD=Off
  -DLLVM_ENABLE_LIBXML2=Off
  -DLLVM_ENABLE_TERMINFO=Off
)

echo "=== host llvm-tblgen ==="
cmake -S llvm-project/llvm -B llvm-host-build "${COMMON[@]}"
ninja -C llvm-host-build -j"$JOBS" llvm-tblgen

echo "=== iOS libraries ==="
# The options docs/BUILDING.md read back from the development CMakeCache.
cmake -S llvm-project/llvm -B llvm-ios-build "${COMMON[@]}" \
  -DCMAKE_SYSTEM_NAME=iOS \
  -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_OSX_SYSROOT=iphoneos \
  -DCMAKE_OSX_DEPLOYMENT_TARGET=17.0 \
  -DLLVM_HOST_TRIPLE=arm64-apple-ios17.0 \
  -DLLVM_DEFAULT_TARGET_TRIPLE=arm64-apple-ios17.0 \
  -DLLVM_TARGET_ARCH=host \
  -DLLVM_BUILD_TOOLS=Off \
  -DLLVM_BUILD_UTILS=Off \
  -DLLVM_TABLEGEN="$TC/llvm-host-build/bin/llvm-tblgen"
# Only the static libraries (llvm-libraries): the default target also builds
# the tools, fuzzers and the LLVMHello plugin module, which Apple's linker
# cannot link for iOS.
ninja -C llvm-ios-build -j"$JOBS" llvm-libraries

echo "=== pruning to what DXMT reads ==="
rm -rf llvm-host-build
find llvm-ios-build -mindepth 1 -maxdepth 1 ! -name include ! -name lib -exec rm -rf {} +
find llvm-ios-build/lib -mindepth 1 -maxdepth 1 ! -name '*.a' -exec rm -rf {} +
find llvm-project -mindepth 1 -maxdepth 1 ! -name llvm -exec rm -rf {} +
find llvm-project/llvm -mindepth 1 -maxdepth 1 ! -name include -exec rm -rf {} +

ls llvm-ios-build/lib
test -f llvm-ios-build/include/llvm/Config/llvm-config.h
du -sh llvm-project llvm-ios-build
