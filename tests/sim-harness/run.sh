#!/bin/bash
# Run one guest exe in the booted harness simulator (boot.sh) and keep its log.
#
#   tests/sim-harness/run.sh <exe> [args...]
#
# The exe must already be in out/MadeiraSim.app/arm64ec-windows (x64) or
# i386-windows (x86). Output: out/logs/<exe>.log (the harness's stdout, which
# carries the whole Madeira session log) plus every *.log the guest wrote into
# the prefix. Exit status: the harness's (0 = guest exited, 124 = timed out).
set -uo pipefail
H="$(cd "$(dirname "$0")" && pwd)"
APP="$H/out/MadeiraSim.app"
UDID="${SIM_UDID:-$(cat "$H/out/udid")}"
EXE="$1"; shift
T="${SIM_TIMEOUT:-300}"
LOGS="$H/out/logs"; mkdir -p "$LOGS"
LOG="$LOGS/$EXE.log"

echo "=== $EXE $* (simulator $UDID, timeout ${T}s) ==="
# simctl hands SIMCTL_CHILD_* to the spawned process without the prefix.
export SIMCTL_CHILD_SIM_TIMEOUT="$T"
[ -n "${SIM_JIT_POOL_MB:-}" ] && export SIMCTL_CHILD_SIM_JIT_POOL_MB="$SIM_JIT_POOL_MB"
[ -n "${MADEIRA_DEBUG_VERBOSE:-}" ] && export SIMCTL_CHILD_MADEIRA_DEBUG_VERBOSE="$MADEIRA_DEBUG_VERBOSE"
[ -n "${SIM_TXM:-}" ] && export SIMCTL_CHILD_SIM_TXM="$SIM_TXM"

xcrun simctl spawn "$UDID" "$APP/madeira-sim" "$EXE" "$@" > "$LOG" 2>&1 &
PID=$!
# Watchdog past the harness's own deadline: a wedged process must not hold the job.
( sleep $((T + 90)); kill -9 $PID 2>/dev/null && echo "[run.sh] killed after $((T + 90))s" >> "$LOG" ) &
WD=$!
wait $PID; RC=$?
kill $WD 2>/dev/null; wait $WD 2>/dev/null
echo "[run.sh] exit status $RC" >> "$LOG"

# The guest's own log files (tfstep-x64.log and friends) from the prefix.
DATA="$HOME/Library/Developer/CoreSimulator/Devices/$UDID/data"
find "$DATA" -path '*/wine/drive_c/*' -name '*.log' -newer "$H/out/udid" 2>/dev/null | while read -r f; do
    cp "$f" "$LOGS/guest-$(basename "$f")"
done
# A crash report, when the process died.
find "$HOME/Library/Logs/DiagnosticReports" -name 'madeira-sim*' -newer "$H/out/udid" 2>/dev/null | while read -r f; do
    cp "$f" "$LOGS/"
done
exit $RC
