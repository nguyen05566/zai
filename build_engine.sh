#!/bin/bash
# build_engine.sh — Build PikaJieQi Linux native + download NNUE
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

# Copy binary to repo root
cp PikaJieQi "$SCRIPT_DIR/pikajieqi-native"
chmod +x "$SCRIPT_DIR/pikajieqi-native"

# Download NNUE from Pikafish releases (not included in jieqi repo)
if [ ! -f "$SCRIPT_DIR/pikafish.nnue" ]; then
    echo "Downloading pikafish.nnue..."
    cd /tmp
    curl -sL "https://github.com/official-pikafish/Pikafish/releases/download/Pikafish-2026-09-06/Pikafish.2026-09-06.7z" -o pk.7z
    mkdir -p pk_extract
    unrar x -y pk.7z pk_extract/ > /dev/null 2>&1 || true
    # Try 7z if unrar not available
    if [ ! -f pk_extract/pikafish.nnue ]; then
        7z x -y pk.7z -opk_extract/ > /dev/null 2>&1 || true
    fi
    cp pk_extract/pikafish.nnue "$SCRIPT_DIR/pikafish.nnue"
fi

echo "✅ PikaJieQi built successfully"
ls -la "$SCRIPT_DIR/pikajieqi-native" "$SCRIPT_DIR/pikafish.nnue"
