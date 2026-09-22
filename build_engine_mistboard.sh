#!/usr/bin/env bash
# Build a separate PikaJieQi native binary from the latest Mistboard branch commit.
# This does not overwrite pikajieqi-native used by cup_bot_jieqi.py.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BRANCH="$(head -1 "$ROOT/pikafish-jieqi.ref" | tr -d '[:space:]')"
WORK="${TMPDIR:-/tmp}/pikafish-jieqi-mistboard"
rm -rf "$WORK"
git clone --quiet --single-branch --branch "$BRANCH" https://github.com/brianhliou/pikafish-jieqi-wasm "$WORK"
COMMIT="$(git -C "$WORK" rev-parse HEAD)"
echo "Building PikaJieQi from branch $BRANCH at $COMMIT"
make -C "$WORK/src" -j"$(nproc)" ARCH=x86-64-sse41-popcnt build
cp "$WORK/src/PikaJieQi" "$ROOT/pikajieqi-mistboard"
chmod +x "$ROOT/pikajieqi-mistboard"

# Mistboard's `jieqi_old-mistboard` build is classical and does not require NNUE.
# Keep this workflow independent of the large Pikafish release archive.
echo "Built $ROOT/pikajieqi-mistboard using latest branch commit $COMMIT"
