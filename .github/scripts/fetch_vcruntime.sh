#!/usr/bin/env bash
# Put Microsoft's Visual C++ runtime DLLs into the app bundle, as
# tools/fetch-vcruntime.md describes:
#
#   app/Madeira/x86_64-vcruntime/   from VC_redist.x64.exe (64-bit x86 programs)
#   app/Madeira/i386-vcruntime/     from VC_redist.x86.exe (32-bit programs, WoW64)
#
# Many programs need them. Madeira's repository does not carry them, so they
# come unmodified from Microsoft's official redistributables at build time.
#
# The redistributable is a WiX Burn bundle: its payloads (MSIs and the cabinets
# holding the DLLs) sit in a cabinet appended to the exe, which 7-Zip does not
# open as part of the PE. So every embedded cabinet is carved out by its MSCF
# header, unpacked, and unpacked again until nothing new appears. Inside, the
# files are named like msvcp140.dll_amd64 or F_CENTRAL_msvcp140_x86; each
# candidate is checked by its PE machine, so the arm64 copies the x64 bundle
# also carries are never picked.
#
# Exit status: 0 when every wanted DLL was staged for both architectures,
# 1 otherwise (with a ::warning:: naming what is missing).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SEVENZIP="${SEVENZIP:-7zz}"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

command -v "$SEVENZIP" >/dev/null || { echo "$SEVENZIP not found (brew install sevenzip)" >&2; exit 1; }

status=0
for arch in x64 x86; do
  case "$arch" in
    x64) DEST="$ROOT/app/Madeira/x86_64-vcruntime"; MACHINE=0x8664
         WANT=(concrt140 msvcp140 msvcp140_1 msvcp140_2 msvcp140_atomic_wait
               msvcp140_codecvt_ids vcamp140 vccorlib140 vcomp140 vcruntime140
               vcruntime140_1 vcruntime140_threads mfc140 mfc140u) ;;
    # vcruntime140_1 exists for x64 only (its __CxxFrameHandler4).
    x86) DEST="$ROOT/app/Madeira/i386-vcruntime"; MACHINE=0x14c
         WANT=(concrt140 msvcp140 msvcp140_1 msvcp140_2 msvcp140_atomic_wait
               msvcp140_codecvt_ids vcamp140 vccorlib140 vcomp140 vcruntime140
               vcruntime140_threads mfc140 mfc140u) ;;
  esac
  W="$WORK/$arch"
  mkdir -p "$W/x0" "$DEST"
  curl -fsSL --retry 3 -o "$W/vc_redist.$arch.exe" "https://aka.ms/vs/17/release/vc_redist.$arch.exe"

  # Carve the Burn bundle's embedded cabinets (UX container, attached container).
  python3 - "$W/vc_redist.$arch.exe" "$W/x0" <<'PY'
import struct, sys
data = open(sys.argv[1], "rb").read()
i = n = 0
while (i := data.find(b"MSCF\0\0\0\0", i)) >= 0:
    size = struct.unpack_from("<I", data, i + 8)[0]
    if size >= 36 and i + size <= len(data):
        open(f"{sys.argv[2]}/carved{n}.cab", "wb").write(data[i:i + size])
        n += 1
        i += size
    else:
        i += 4
print(f"carved {n} cabinets from {sys.argv[1].replace(chr(92), '/').rsplit('/', 1)[-1]}")
if n == 0:
    sys.exit(1)
PY

  # Unpack every level (cabinets, MSIs and the Burn payloads a0, a1, ...).
  for level in 1 2 3 4; do
    found=0
    while IFS= read -r f; do
      out="$W/x$level/$(basename "$f").d"
      mkdir -p "$out"
      "$SEVENZIP" x -y -o"$out" "$f" >/dev/null 2>&1 && found=1 || true
    done < <(find "$W/x$((level - 1))" -type f \( -iname '*.cab' -o -iname '*.msi' -o -iname 'a[0-9]' -o -iname 'a[0-9][0-9]' \))
    [ "$found" = 1 ] || break
  done

  python3 - "$W" "$DEST" "$MACHINE" "$arch" "${WANT[@]}" <<'PY' || status=1
import os, re, shutil, struct, sys

work, dest, machine, arch, want = sys.argv[1], sys.argv[2], int(sys.argv[3], 16), sys.argv[4], sys.argv[5:]

def pe_machine(path):
    try:
        with open(path, "rb") as fh:
            data = fh.read(4096)
        if data[:2] != b"MZ":
            return None
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            return None
        return struct.unpack_from("<H", data, pe + 4)[0]
    except (OSError, struct.error):
        return None

ARCH = r"_(amd64|x64|x86|arm64|arm64ec)$"

def stem_of(name):
    s = name.lower()
    s = re.sub(r"^f_central_", "", s)
    s = re.sub(ARCH, "", s)          # msvcp140.dll_amd64
    s = re.sub(r"\.dll$", "", s)
    s = re.sub(ARCH, "", s)          # msvcp140_x86
    return s

candidates = {}
for base, _, files in os.walk(work):
    for name in files:
        stem = stem_of(name)
        path = os.path.join(base, name)
        if stem in want and pe_machine(path) == machine:
            # Several MSIs can carry one DLL; keep the largest copy.
            if stem not in candidates or os.path.getsize(path) > os.path.getsize(candidates[stem]):
                candidates[stem] = path

missing = []
for stem in want:
    if stem in candidates:
        shutil.copyfile(candidates[stem], os.path.join(dest, stem + ".dll"))
        print(f"  {arch}: {stem}.dll")
    else:
        missing.append(stem + ".dll")
print(f"{arch}: {len(want) - len(missing)} of {len(want)} runtime DLLs staged in {os.path.basename(dest)}")
if missing:
    print(f"::warning::Visual C++ runtime ({arch}): not found in Microsoft's redistributable: {', '.join(missing)}.")
    sys.exit(1)
PY
done
exit "$status"
