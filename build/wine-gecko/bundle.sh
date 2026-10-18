#!/bin/bash
# Copy the Wine-pinned Gecko MSI packages to the app's <WINEDATADIR>/gecko.
# Wine installs the matching MSI into each prefix before opening its downloader.
set -euo pipefail

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
D="$R/build/wine-gecko"
. "$D/pin.sh"
DST="${1:?usage: bundle.sh <destination folder>}"

for arch in x86 x86_64; do
    name="wine-gecko-$WINE_GECKO_VER-$arch.msi"
    src="$D/$name"
    [ -s "$src" ] || { echo "error: missing $src; run build/wine-gecko/fetch.sh" >&2; exit 1; }
    if [ "$arch" = x86 ]; then want="$WINE_GECKO_X86_SHA256"; else want="$WINE_GECKO_X86_64_SHA256"; fi
    echo "$want  $src" | shasum -a 256 -c -
done

mkdir -p "$DST"
cp "$D/wine-gecko-$WINE_GECKO_VER-x86.msi" "$DST/"
cp "$D/wine-gecko-$WINE_GECKO_VER-x86_64.msi" "$DST/"
echo "Bundled Wine Gecko $WINE_GECKO_VER in $DST"
