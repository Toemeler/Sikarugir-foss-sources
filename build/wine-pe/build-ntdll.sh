#!/bin/bash
# Build the ARM64EC PE ntdll from the wine submodule and post-process it the way
# the app needs: strip, then pad with zeros to SizeOfImage + 0x50000 (the loader
# maps the file image; the padding is the slack the iOS mapping path relies on).
# Other PE modules: build/wine-pe/build-modules.sh (the same build tree; it
# strips them with --strip-debug and does not pad). Requires the llvm-mingw
# toolchain (docs/BUILDING.md).
# --enable-winegstreamer keeps winegstreamer's PE rules although GStreamer is
# absent (its unix side is build/ntdll-unix/winegstreamer_unixlib_ios.c); build
# it as `make dlls/winegstreamer/arm64ec-windows/winegstreamer.dll`, never as
# `make -C dlls/winegstreamer`, which would also try the GStreamer-based .so.
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TC="$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
export PATH="$TC:$PATH"
B="$R/wine/build-arm64ec"
# Madeira's own changes to the pinned Wine's ntdll, kept here rather than in the
# submodule (patches/, applied in name order; those already applied are left as they are):
#   0001-arm64ec-x64-debug-registers  the x64 debug registers a guest sets through a
#                       context come back in its exception contexts (tests/x64/tfstep-x64.c "drrt")
#   0002-arm64ec-guest-seh-log-dr6  log what an x64 frame handler returns and does to its context
#                       ([guest-seh]); Dr6 reads ffff0ff0 while Dr7 is armed, as on Windows 10
#   0003-arm64ec-x64-handler-returns-into-ntdll  an x64 frame handler's return address is in
#                       ntdll's image, not its JIT-pool copy (tests/x64/sehctx-x64.c "ret", "bt")
# How many of them the tree already has, in order: undo them newest first on a
# scratch copy of the files they touch until that works. Checking each patch on its
# own fails for 0001 once 0002 is applied (they touch neighbouring lines).
PATCHES=()
for P in "$R"/build/wine-pe/patches/*.patch; do [ -e "$P" ] && PATCHES+=("$P"); done
APPLIED=0
if [ ${#PATCHES[@]} -gt 0 ]; then
    FILES=$(sed -n 's#^diff --git a/\([^ ]*\) .*#\1#p' "${PATCHES[@]}" | sort -u)
    for ((K = ${#PATCHES[@]}; K > 0; K--)); do
        T=$(mktemp -d)
        for F in $FILES; do mkdir -p "$T/$(dirname "$F")"; cp "$R/wine/$F" "$T/$F"; done
        OK=1
        for ((J = K - 1; J >= 0; J--)); do
            (cd "$T" && git apply --reverse "${PATCHES[J]}" 2>/dev/null) || { OK=0; break; }
        done
        rm -rf "$T"
        if [ $OK = 1 ]; then APPLIED=$K; break; fi
    done
fi
for ((J = 0; J < ${#PATCHES[@]}; J++)); do
    if [ $J -lt $APPLIED ]; then
        echo "Wine patch already applied: $(basename "${PATCHES[J]}")"
    else
        git -C "$R/wine" apply "${PATCHES[J]}"
        echo "Wine patch applied: $(basename "${PATCHES[J]}")"
    fi
done
if [ ! -f "$B/config.status" ]; then
    mkdir -p "$B" && cd "$B" && ../configure --enable-archs=arm64ec --without-x --disable-tests --enable-winegstreamer
fi
cd "$B" && make -C dlls/ntdll
SRC="$B/dlls/ntdll/arm64ec-windows/ntdll.dll"; OUT="$R/app/Madeira/arm64ec-windows/ntdll.dll"
cp "$SRC" "$OUT.tmp"
"$TC/arm64ec-w64-mingw32-strip" "$OUT.tmp"
python3 - "$OUT.tmp" <<'PY'
import struct, sys
p = sys.argv[1]; d = open(p, 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
soi = struct.unpack_from('<I', d, pe + 24 + 56)[0]     # OptionalHeader.SizeOfImage
# ml1004: PAD UP TO the target, do not blindly append.
#
# The old code asserted `len(d) == soi` and then appended 0x50000. Stripping
# removes sections and debug data, so the stripped file is SMALLER than
# SizeOfImage (measured: 0x120000 stripped vs 0x140000 SizeOfImage) and the
# assert failed on every single run -- this script has never completed, and the
# file was being padded by hand instead. Appending a fixed 0x50000 would also
# have produced the wrong total even if the assert had passed.
#
# The header comment states the intent: pad to SizeOfImage + 0x50000. Compute
# that target and fill up to it, which reproduces the known-good hand-built
# size exactly (0x140000 + 0x50000 = 0x190000 = 1,638,400 bytes).
target = soi + 0x50000
if len(d) > target:
    raise SystemExit("stripped ntdll is %d bytes, already larger than the "
                     "target SizeOfImage(%d) + 0x50000 = %d -- the padding "
                     "assumption no longer holds, do not guess"
                     % (len(d), soi, target))
pad = target - len(d)
with open(p, 'ab') as f:
    f.write(b'\0' * pad)
final = len(open(p, 'rb').read())
if final != target:
    raise SystemExit("padding produced %d bytes, expected %d" % (final, target))
print("ntdll.dll: stripped %d + pad %d = %d bytes (SizeOfImage %d + 0x50000)"
      % (len(d), pad, final, soi))
PY
mv "$OUT.tmp" "$OUT"; ls -l "$OUT"
