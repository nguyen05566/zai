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

# Use the same evaluation net as the original bot, but keep the engine binary
# separate so cup_bot_jieqi.py remains untouched.
if [ ! -s "$ROOT/pikafish.nnue" ]; then
  TMP_ARCHIVE="${TMPDIR:-/tmp}/pikafish.nnue.7z"
  TMP_EXTRACT="${TMPDIR:-/tmp}/pikafish-nnue"
  curl -fsSL "https://github.com/official-pikafish/Pikafish/releases/download/Pikafish-2026-09-06/Pikafish.2026-09-06.7z" -o "$TMP_ARCHIVE"
  rm -rf "$TMP_EXTRACT" && mkdir -p "$TMP_EXTRACT"
  # The release archive format has varied; do not let an unsuccessful first
  # extractor abort the workflow. Try both tools, then locate the net.
  unrar x -y "$TMP_ARCHIVE" "$TMP_EXTRACT/" >/dev/null 2>&1 || true
  if [ ! -s "$TMP_EXTRACT/pikafish.nnue" ]; then
    7z x -y "$TMP_ARCHIVE" "-o$TMP_EXTRACT" >/dev/null 2>&1 || true
  fi
  NNUE_FOUND=$(find "$TMP_EXTRACT" -type f -name 'pikafish.nnue' -print -quit)
  if [ -z "$NNUE_FOUND" ]; then
    echo "Could not extract pikafish.nnue from $TMP_ARCHIVE" >&2
    exit 1
  fi
  cp "$NNUE_FOUND" "$ROOT/pikafish.nnue"
fi

echo "Built $ROOT/pikajieqi-mistboard using ref $REF"
