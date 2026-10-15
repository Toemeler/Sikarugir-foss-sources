#!/usr/bin/env bash
# Commit generated files onto the current tip of the target branch.
#
# usage: commit_generated.sh "<commit message>" <path>...
#
# These are build output, not authored content: when another run has already
# pushed its own version, the newest one simply wins.
set -euo pipefail

BRANCH=${TARGET_BRANCH:-main}
MESSAGE=$1
shift

STAGING=$(mktemp -d)
for path in "$@"; do
  [ -e "$path" ] || continue
  mkdir -p "$STAGING/$(dirname "$path")"
  cp -R "$path" "$STAGING/$(dirname "$path")/"
done

# FETCH_HEAD, not origin/$BRANCH: actions/checkout configures a narrow refspec,
# so the remote-tracking ref can be stale and resetting to it would drop a
# commit another job just pushed.
git fetch -q origin "$BRANCH"
git reset -q --hard FETCH_HEAD

for path in "$@"; do
  rm -rf "$path"
  if [ -e "$STAGING/$path" ]; then
    mkdir -p "$(dirname "$path")"
    cp -R "$STAGING/$path" "$(dirname "$path")/"
  fi
done
rm -rf "$STAGING"

# Only paths that exist or are tracked: git add fails on a path that is
# neither (an optional output this run did not produce).
ADD=()
for path in "$@"; do
  if [ -e "$path" ] || git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
    ADD+=("$path")
  fi
done
git add -A -- "${ADD[@]}"
if git diff --cached --quiet; then
  echo "nothing to commit"
  exit 0
fi
git commit -q -m "$MESSAGE"
git push origin "HEAD:$BRANCH"
