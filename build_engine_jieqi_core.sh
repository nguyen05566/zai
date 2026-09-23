#!/usr/bin/env bash
# Build the standalone JieqiCore engine at the exact commit pinned in jieqi-core.ref.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
REF="$(head -1 "$ROOT/jieqi-core.ref" | tr -d '[:space:]')"
REPO="https://github.com/nguyen05566/jieqi-core.git"
WORK="${TMPDIR:-/tmp}/jieqi-core-build"
OUTPUT="$ROOT/jieqi-core"

[[ "$REF" =~ ^[0-9a-f]{40}$ ]] || {
  echo "Invalid JieqiCore commit in jieqi-core.ref: $REF" >&2
  exit 2
}

rm -rf "$WORK"
git clone --quiet "$REPO" "$WORK"
git -C "$WORK" checkout --quiet --detach "$REF"
ACTUAL="$(git -C "$WORK" rev-parse HEAD)"
[[ "$ACTUAL" == "$REF" ]] || {
  echo "JieqiCore checkout mismatch: expected $REF, got $ACTUAL" >&2
  exit 3
}

echo "Building JieqiCore at pinned commit $REF"
"$WORK/scripts/build-linux.sh" "$OUTPUT"
echo "Built $OUTPUT"
