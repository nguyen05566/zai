#!/usr/bin/env bash
# Build a separate Mistboard-pinned PikaJieQi native binary.
# This does not overwrite pikajieqi-native used by cup_bot_jieqi.py.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
REF="$(head -1 "$ROOT/pikafish-jieqi.ref" | tr -d '[:space:]')"
WORK="${TMPDIR:-/tmp}/pikafish-jieqi-mistboard"
rm -rf "$WORK"
git clone --quiet https://github.com/brianhliou/pikafish-jieqi-wasm "$WORK"
git -C "$WORK" checkout --quiet "$REF"
echo "Building Mistboard PikaJieQi at $(git -C "$WORK" rev-parse HEAD)"
make -C "$WORK/src" -j"$(nproc)" ARCH=x86-64-sse41-popcnt build
cp "$WORK/src/PikaJieQi" "$ROOT/pikajieqi-mistboard"
chmod +x "$ROOT/pikajieqi-mistboard"

# Mistboard's pinned `jieqi_old` build is classical and does not require NNUE.
# Keep this workflow independent of the large Pikafish release archive.
echo "Built $ROOT/pikajieqi-mistboard using ref $REF"
