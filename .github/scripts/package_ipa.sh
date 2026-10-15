#!/usr/bin/env bash
# Wrap a built .app into a sideloadable IPA.
#
# usage: package_ipa.sh <app path> <entitlements> <output ipa>
#
# Ad-hoc signed from the inside out, the app itself with Madeira's
# entitlements: SideStore/AltStore re-sign with the user's Apple ID and take the
# entitlements they request from this signature (increased-memory-limit is what
# Madeira's "Memory+" check looks for).
set -euo pipefail

APP=$1
ENTITLEMENTS=$2
OUTPUT=$3

case "$(basename "$APP")" in
  *.app) ;;
  *) echo "::error::$APP is not a .app bundle"; exit 1 ;;
esac
ls -la "$APP"
file "$APP/$(basename "$APP" .app)" || true

# Nested code first (deepest paths first), so each outer signature seals the
# signed inner one.
find "$APP" -mindepth 1 \( -name '*.dylib' -o -name '*.framework' -o -name '*.appex' \) \
  | awk -F/ '{ print NF "\t" $0 }' | sort -rn | cut -f2- \
  | while IFS= read -r item; do
      codesign --force --sign - --timestamp=none "$item"
    done
codesign --force --sign - --timestamp=none --entitlements "$ENTITLEMENTS" "$APP"
codesign -d --entitlements - "$APP" 2>/dev/null | head -20 || true

WORK=$(mktemp -d)
mkdir -p "$WORK/Payload"
cp -R "$APP" "$WORK/Payload/"
# Drop dangling symlinks, then zip while DEREFERENCING the rest (no -y), so the
# IPA holds zero symlink entries - Windows signing tools otherwise fail with
# "os error 1314 / A required privilege is not held".
find "$WORK/Payload" -type l ! -exec test -e {} \; -delete || true
echo "Symlinks left in Payload: $(find "$WORK/Payload" -type l | wc -l | tr -d ' ')"
(cd "$WORK" && zip -qr payload.ipa Payload)
mv "$WORK/payload.ipa" "$OUTPUT"
rm -rf "$WORK"
ls -lh "$OUTPUT"
