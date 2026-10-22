#!/bin/bash
# Create and boot the simulator the harness runs in; prints its UDID and writes it
# to out/udid.
#
# Device: iPad Pro 13-inch (M4), 16 GB where the runtime offers it. Runtime: the
# newest installed iOS, which is iOS 27 once the runner image carries it
# (SIM_RUNTIME=<identifier> picks one explicitly).
set -euo pipefail
H="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$H/out"

RT="${SIM_RUNTIME:-}"
if [ -z "$RT" ]; then
    RT=$(xcrun simctl list runtimes -j | python3 -c '
import json, sys
rts = [r for r in json.load(sys.stdin)["runtimes"]
       if r.get("isAvailable") and r["identifier"].startswith("com.apple.CoreSimulator.SimRuntime.iOS-")]
rts.sort(key=lambda r: [int(x) for x in r["version"].split(".")])
print(rts[-1]["identifier"] if rts else "")')
fi
[ -n "$RT" ] || { echo "no iOS simulator runtime installed" >&2; exit 1; }
case "$RT" in *iOS-27*) ;; *) echo "::warning::no iOS 27 simulator runtime on this runner; using $RT" >&2 ;; esac

DT=""
for want in iPad-Pro-13-inch-M4-16GB iPad-Pro-13-inch-M4-8GB iPad-Pro-11-inch-M4-16GB iPad-Pro-11-inch-M4-8GB; do
    if xcrun simctl list devicetypes | grep -q "SimDeviceType.$want)"; then
        DT="com.apple.CoreSimulator.SimDeviceType.$want"; break
    fi
done
[ -n "$DT" ] || { echo "no iPad Pro (M4) device type on this runner" >&2; exit 1; }

UDID=$(xcrun simctl create madeira-sim "$DT" "$RT")
echo "device: $DT"  >&2
echo "runtime: $RT" >&2
xcrun simctl boot "$UDID"
xcrun simctl bootstatus "$UDID" -b >&2
echo "$UDID" > "$H/out/udid"
echo "$UDID"
