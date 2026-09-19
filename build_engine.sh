#!/bin/bash
# Build official Pikafish jieqi_old with NNUE enabled for Cờ Úp bot.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REF="$(head -1 "$SCRIPT_DIR/pikafish-jieqi.ref" | tr -d '[:space:]')"

rm -rf /tmp/pikafish-jieqi

git clone --quiet https://github.com/official-pikafish/Pikafish.git /tmp/pikafish-jieqi
cd /tmp/pikafish-jieqi
git checkout --quiet "$REF"
git apply "$SCRIPT_DIR/engine_king_guard.patch"

# jieqi_old ships with classical-only eval. Enable NNUE so trained nets are used.
sed -i 's/#define USE_NNUEEVAL .*/#define USE_NNUEEVAL 1/' src/types.h

cd src
make -j"$(nproc)" ARCH=x86-64-sse41-popcnt build
cp PikaJieQi "$SCRIPT_DIR/pikajieqi-native"
chmod +x "$SCRIPT_DIR/pikajieqi-native"

# Prefer trained Cờ Úp net bundled in the repo.
rm -f "$SCRIPT_DIR/pikafish.nnue"
if [ -f "$SCRIPT_DIR/zai_cup_boost_v1.nnue" ]; then
  cp "$SCRIPT_DIR/zai_cup_boost_v1.nnue" "$SCRIPT_DIR/pikafish.nnue"
  echo "Using trained net: zai_cup_boost_v1.nnue"
elif [ -f "$SCRIPT_DIR/zai_jieqi_master.nnue" ]; then
  cp "$SCRIPT_DIR/zai_jieqi_master.nnue" "$SCRIPT_DIR/pikafish.nnue"
  echo "Using trained net: zai_jieqi_master.nnue"
else
  curl --fail --silent --show-error -L \
    "https://github.com/official-pikafish/Networks/releases/download/master-net/pikafish.nnue" \
    -o "$SCRIPT_DIR/pikafish.nnue"
  echo "WARNING: no trained net found; downloaded upstream master-net (may not match jieqi_old arch)"
fi

echo "Official Pikafish jieqi_old engine built successfully (NNUE enabled)"
ls -la "$SCRIPT_DIR/pikajieqi-native" "$SCRIPT_DIR/pikafish.nnue"
ls -la "$SCRIPT_DIR"/zai_*.nnue 2>/dev/null || true
