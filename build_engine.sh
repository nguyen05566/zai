#!/bin/bash
# Build official Pikafish jieqi_old and download the matching NNUE network.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REF="$(head -1 "$SCRIPT_DIR/pikafish-jieqi.ref" | tr -d '[:space:]')"

rm -rf /tmp/pikafish-jieqi

git clone --quiet https://github.com/official-pikafish/Pikafish.git /tmp/pikafish-jieqi
cd /tmp/pikafish-jieqi
git checkout --quiet "$REF"
git apply "$SCRIPT_DIR/engine_king_guard.patch"

cd src
make -j"$(nproc)" ARCH=x86-64-sse41-popcnt build
cp PikaJieQi "$SCRIPT_DIR/pikajieqi-native"
chmod +x "$SCRIPT_DIR/pikajieqi-native"

# Always replace the local network so repeated local builds and CI runs cannot
# accidentally use an older NNUE file.
rm -f "$SCRIPT_DIR/pikafish.nnue"
curl --fail --silent --show-error -L \
  "https://github.com/official-pikafish/Networks/releases/download/master-net/pikafish.nnue" \
  -o "$SCRIPT_DIR/pikafish.nnue"

echo "Official Pikafish jieqi_old engine built successfully"
ls -la "$SCRIPT_DIR/pikajieqi-native"
if [ -f "$SCRIPT_DIR/zai_jieqi_master.nnue" ]; then
  echo "Trained Cờ Úp NNUE found: $SCRIPT_DIR/zai_jieqi_master.nnue"
fi
ls -la "$SCRIPT_DIR/pikafish.nnue"
