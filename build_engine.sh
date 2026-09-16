#!/bin/bash
# build_engine.sh — Build PikaJieQi Linux native from source
set -ex

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REF=$(head -1 "$SCRIPT_DIR/pikafish-jieqi.ref" | tr -d '[:space:]')
echo "Building PikaJieQi from commit $REF..."

# Clone the fork (full clone to reach pinned commit)
git clone https://github.com/brianhliou/pikafish-jieqi-wasm /tmp/pikafish-jieqi
cd /tmp/pikafish-jieqi
git checkout "$REF"
echo "Checked out: $(git rev-parse HEAD)"

# Build
cd src
make -j$(nproc) ARCH=x86-64-sse41-popcnt build

# Copy binary and NNUE to repo root
cp PikaJieQi "$SCRIPT_DIR/pikajieqi-native"
chmod +x "$SCRIPT_DIR/pikajieqi-native"
cp pikafish.nnue "$SCRIPT_DIR/pikafish.nnue"

echo "✅ PikaJieQi built successfully"
ls -la "$SCRIPT_DIR/pikajieqi-native" "$SCRIPT_DIR/pikafish.nnue"
