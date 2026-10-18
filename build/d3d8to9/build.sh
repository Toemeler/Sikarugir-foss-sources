#!/bin/bash
# Build d3d8to9 (https://github.com/crosire/d3d8to9, BSD-2-Clause) for i386
# and install it as the WoW64 farm's d3d8.dll:
#
#   build/d3d8to9/build.sh <output d3d8.dll>
#
# Wine's own d3d8 is a wined3d frontend, and wined3d has no backend in this
# port (no OpenGL, --without-vulkan), so a D3D8 program could not draw at all.
# d3d8to9 implements D3D8 on top of D3D9 (Direct3DCreate9 from the farm's
# d3d9.dll, DXMT's shim) and loads d3dx9_43.dll at run time for shader
# assembly, which the farm carries as Wine's builtin.
#
# Statically linked against libc++/libunwind so that it imports only system
# DLLs. The source is fetched at the pinned commit below into
# build/d3d8to9/src (git-ignored); its licence ships in app/Madeira/licenses.
set -euo pipefail

D3D8TO9_COMMIT=255338f698c8270b537f0a91a13f795f4f988250

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TC="${TC:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
OUT="${1:?usage: build.sh <output d3d8.dll>}"
S="$R/build/d3d8to9/src"
B="$R/build/d3d8to9/build-i386"

if [ "$(git -C "$S" rev-parse HEAD 2>/dev/null || true)" != "$D3D8TO9_COMMIT" ]; then
    rm -rf "$S"
    git init -q "$S"
    git -C "$S" fetch -q --depth 1 https://github.com/crosire/d3d8to9 "$D3D8TO9_COMMIT"
    git -C "$S" checkout -q FETCH_HEAD
fi

rm -rf "$B"
mkdir -p "$B"
"$TC/i686-w64-mingw32-windres" -I "$S/res" -O coff -o "$B/d3d8to9.res.o" "$S/res/d3d8to9.rc"
"$TC/i686-w64-mingw32-clang++" -std=c++17 -O2 -DNDEBUG -DD3D8TO9NOLOG -DNOMINMAX \
    -Wall -Wno-delete-non-virtual-dtor -Wno-unknown-pragmas \
    -shared -static -o "$B/d3d8.dll" \
    "$S"/source/*.cpp "$S/res/d3d8.def" "$B/d3d8to9.res.o" \
    -ld3d9 -lgdi32 -Wl,--enable-stdcall-fixup -Wl,--kill-at
"$TC/i686-w64-mingw32-strip" -o "$OUT" "$B/d3d8.dll"
echo "d3d8to9 $D3D8TO9_COMMIT -> $OUT"
