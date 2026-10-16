#!/bin/bash
# Build madeira-oodle's oo2core_6_win64.dll (ARM64EC) as a Wine builtin: a game
# config with WINEDLLOVERRIDES=...;oo2core_6_win64=b loads it instead of the game's
# own DLL (madeira-oodle/src/oo2core_shim.cpp). Without the override nothing loads it.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DIR/../.." && pwd)"
MINGW="${MINGW_BIN:-$REPO_ROOT/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
SRC="$REPO_ROOT/madeira-oodle/src"
OUT="${OUT:-$REPO_ROOT/build/madeira-oodle/out-pe}"
mkdir -p "$OUT"

echo "=== oo2core_6_win64.dll (arm64ec, madeira-oodle) ==="
# No C++ runtime: no exceptions, RTTI, new or delete in the decoder or the shim.
# The portable LZ copy intrinsics require the load/store order tested by the
# host decoder vectors. Keep SLP vectorization disabled for this implementation.
"$MINGW/arm64ec-w64-mingw32-clang++" -shared -O2 -std=gnu++14 -fno-exceptions -fno-rtti -fno-threadsafe-statics \
    -fno-strict-aliasing -fno-slp-vectorize -Wno-c++11-narrowing -nostdlib++ \
    -o "$OUT/oo2core_6_win64.dll" "$SRC/oo2core_shim.cpp" "$SRC/kraken.cpp" "$SRC/oo2core_6_win64.def"

# Mark it the way winebuild marks Wine's own PE builtins: "Wine builtin DLL" right
# after the DOS header. Wine's loader takes a builtin only with this mark.
python3 - "$OUT/oo2core_6_win64.dll" <<'PY'
import struct, sys
p = sys.argv[1]
d = bytearray(open(p, 'rb').read())
lfanew = struct.unpack_from('<I', d, 0x3c)[0]
mark = b'Wine builtin DLL\0'
assert lfanew >= 0x40 + len(mark), hex(lfanew)
d[0x40:0x40 + len(mark)] = mark
open(p, 'wb').write(d)
print('  builtin mark written (e_lfanew %#x)' % lfanew)
PY
python3 "$REPO_ROOT/tests/host/check-oodle-pe.py" "$OUT/oo2core_6_win64.dll"
{
    printf '%s\n' 'Madeira native Oodle decoder' \
        'Kraken decoder: Copyright (C) 2016, Powzix; GNU GPL version 3 or later.' \
        'Source: https://github.com/powzix/ooz at 05038060aa68f9187ae9923b2388ca8db40e58d1' \
        'Port and shim source: madeira-oodle/ in the corresponding Madeira source release.' \
        'No proprietary Oodle implementation is included.' ''
    cat "$REPO_ROOT/COPYING"
} > "$OUT/oodle-notices.txt"
echo "  built $(ls -l "$OUT/oo2core_6_win64.dll" | awk '{print $5}') bytes"
"$MINGW/llvm-readobj" --coff-exports "$OUT/oo2core_6_win64.dll" | awk '$1 == "Name:" { print "  export " $2 }'
