#!/bin/bash
# Fetch Wine's exact pinned Gecko MSI packages. Wine's appwiz.cpl searches
# WINEDATADIR/gecko before showing its online downloader.
set -euo pipefail

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
D="$R/build/wine-gecko"
. "$D/pin.sh"
URL="https://dl.winehq.org/wine/wine-gecko/$WINE_GECKO_VER"

get() {
    local name="$1" want="$2"
    if [ ! -f "$D/$name" ]; then
        curl -fL --retry 3 -o "$D/$name.part" "$URL/$name"
        mv "$D/$name.part" "$D/$name"
    fi
    echo "$want  $D/$name" | shasum -a 256 -c -
}

get "wine-gecko-$WINE_GECKO_VER-x86.msi" "$WINE_GECKO_X86_SHA256"
get "wine-gecko-$WINE_GECKO_VER-x86_64.msi" "$WINE_GECKO_X86_64_SHA256"
echo "Wine Gecko $WINE_GECKO_VER x86 and x86_64 ready in build/wine-gecko"
