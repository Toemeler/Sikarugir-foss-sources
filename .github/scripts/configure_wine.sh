#!/usr/bin/env bash
# Configure the Wine submodule so the iOS unix-side builds can compile.
#
# build/ntdll-unix, build/wineserver and build/win32u-unix compile Wine's own
# sources with the iOS SDK, but read include/config.h and the generated headers
# from a configured tree at wine/build-macos (plus wine/build-arm64ec/include for
# dwrite). docs/BUILDING.md does not record how that tree was configured, so this
# uses the flags it records for build-arm64ec - the ARM64EC PE modules are
# committed, so only the configure results and headers are needed, never the
# DLLs - and points build-arm64ec at the same tree.
#
# GnuTLS must be found here: the crypto unix libraries (TLS for Steam sign-in
# among them) are only compiled when config.h defines SONAME_LIBGNUTLS.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WINE="$ROOT/wine"
B="$WINE/build-macos"
JOBS=$(sysctl -n hw.ncpu)
BREW=$(brew --prefix)

# Homebrew's bison/flex are keg-only (macOS ships bison 2.3; Wine needs 3).
export PATH="$BREW/opt/bison/bin:$BREW/opt/flex/bin:$ROOT/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin:$PATH"
export PKG_CONFIG_PATH="$BREW/opt/gnutls/lib/pkgconfig:$BREW/opt/freetype/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
bison --version | head -1
command -v arm64ec-w64-mingw32-clang

mkdir -p "$B"
cd "$B"
if [ ! -f config.status ]; then
  ../configure --enable-archs=arm64ec --without-x --disable-tests --enable-winegstreamer \
    > configure.out 2>&1 || { tail -50 configure.out; exit 1; }
fi
grep -E 'SONAME_LIBGNUTLS|HAVE_FT2BUILD_H' include/config.h || true
grep -q 'SONAME_LIBGNUTLS' include/config.h || {
  echo "::error::Wine's configure did not find GnuTLS; the crypto unix libraries would be left out."
  exit 1
}

# Generated headers (IDL and friends) the unix sides include from the tree.
make -j"$JOBS" include > make-include.out 2>&1 || { tail -50 make-include.out; exit 1; }

ln -sfn build-macos "$WINE/build-arm64ec"
echo "configured: $B"
