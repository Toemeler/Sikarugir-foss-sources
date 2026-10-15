#!/usr/bin/env bash
# Put Microsoft's Visual C++ runtime DLLs into app/Madeira/x86_64-vcruntime/,
# as tools/fetch-vcruntime.md describes. Many 64-bit games need them. Madeira's
# repository does not carry them, so they come unmodified from Microsoft's
# official redistributable at build time.
#
# Best effort: the redistributable's internal layout changes between versions.
# Whatever cannot be found is reported and left out; the app builds either way
# (the folder only has to exist).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DEST="$ROOT/app/Madeira/x86_64-vcruntime"
WANT=(concrt140 msvcp140 msvcp140_1 msvcp140_2 msvcp140_atomic_wait
      msvcp140_codecvt_ids vcamp140 vccorlib140 vcomp140 vcruntime140
      vcruntime140_1 vcruntime140_threads mfc140 mfc140u)

mkdir -p "$DEST"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

curl -fsSL --retry 3 -o "$WORK/vc_redist.x64.exe" https://aka.ms/vs/17/release/vc_redist.x64.exe
7zz x -y -o"$WORK/x0" "$WORK/vc_redist.x64.exe" >/dev/null

# The bundle nests cabinets (and, in some versions, MSIs holding more
# cabinets). Unpack every level until nothing new appears.
for level in 1 2 3; do
  found=0
  while IFS= read -r f; do
    out="$WORK/x$level/$(basename "$f").d"
    mkdir -p "$out"
    7zz x -y -o"$out" "$f" >/dev/null 2>&1 && found=1 || true
  done < <(find "$WORK/x$((level - 1))" -type f \( -iname '*.cab' -o -iname '*.msi' -o -iname 'a[0-9]' -o -iname 'a[0-9][0-9]' \))
  [ "$found" = 1 ] || break
done

python3 - "$WORK" "$DEST" "${WANT[@]}" <<'PY'
import os, re, shutil, struct, sys

work, dest, want = sys.argv[1], sys.argv[2], sys.argv[3:]

def is_x64_pe(path):
    try:
        with open(path, "rb") as fh:
            data = fh.read(4096)
        if data[:2] != b"MZ":
            return False
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        return data[pe:pe + 4] == b"PE\0\0" and struct.unpack_from("<H", data, pe + 4)[0] == 0x8664
    except (OSError, struct.error):
        return False

# Plain names (vcruntime140.dll) or MSI file keys (F_CENTRAL_vcruntime140_x64).
candidates = {}
for base, _, files in os.walk(work):
    for name in files:
        stem = name.lower()
        stem = re.sub(r"^f_central_", "", stem)
        stem = re.sub(r"\.dll$", "", stem)
        stem = re.sub(r"_x64$", "", stem)
        path = os.path.join(base, name)
        if stem in want and is_x64_pe(path):
            candidates.setdefault(stem, path)

missing = []
for stem in want:
    if stem in candidates:
        shutil.copyfile(candidates[stem], os.path.join(dest, stem + ".dll"))
        print(f"  {stem}.dll")
    else:
        missing.append(stem + ".dll")
if missing:
    print(f"::warning::Visual C++ runtime: not found in Microsoft's redistributable: {', '.join(missing)}. Games that need them will not start.")
print(f"{len(want) - len(missing)} of {len(want)} runtime DLLs staged")
PY
