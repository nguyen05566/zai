#!/bin/bash
# build_engine.sh — Build PikaJieQi Linux native from source
# Used by GitHub Actions workflow

set -e

REF=$(head -1 pikafish-jieqi.ref | tr -d '[:space:]')
echo "Building PikaJieQi from commit $REF..."

# Clone the fork
git clone -b jieqi_old-mistboard --single-branch https://github.com/brianhliou/pikafish-jieqi-wasm /tmp/pikafish-jieqi
cd /tmp/pikafish-jieqi
git checkout "$REF"
echo "Checked out: $(git rev-parse HEAD)"

# Build
cd src
make -j ARCH=x86-64-sse41-popcnt build

# Copy binary and NNUE to repo root
cp PikaJieQi "$OLDPWD/pikajieqi-native"
chmod +x "$OLDPWD/pikajieqi-native"
cp pikafish.nnue "$OLDPWD/pikafish.nnue"

echo "✅ PikaJieQi built successfully"
ls -la "$OLDPWD/pikajieqi-native" "$OLDPWD/pikafish.nnue"
