#!/usr/bin/env bash
# Build Madeira.app with code signing disabled.
#
# usage: build_app.sh <marketing version> <build number>
#
# Debug on purpose: docs/BUILDING.md records it as the configuration that runs
# the games ("Release builds have crashed the guest").
# Signing is forced off: the runner has no Apple ID, and the IPA is re-signed by
# SideStore/AltStore/Sideloadly on the way onto the device (package_ipa.sh
# ad-hoc signs it with Madeira's entitlements so the sideloader carries them).
# -target rather than -scheme, because the project ships no shared .xcscheme.
set -euo pipefail

VERSION=$1
BUILD=$2

xcodebuild \
  -project app/Madeira.xcodeproj \
  -target Madeira \
  -configuration Debug \
  -sdk iphoneos \
  SYMROOT="$PWD/build/xcode" \
  MARKETING_VERSION="$VERSION" \
  CURRENT_PROJECT_VERSION="$BUILD" \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGNING_REQUIRED=NO \
  CODE_SIGN_IDENTITY="" \
  CODE_SIGN_ENTITLEMENTS="" \
  CODE_SIGN_STYLE=Manual \
  DEVELOPMENT_TEAM="" \
  ONLY_ACTIVE_ARCH=NO \
  COMPILER_INDEX_STORE_ENABLE=NO \
  build
