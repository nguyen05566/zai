#!/bin/bash
# build_engine.sh — Build PikaJieQi Linux native + download NNUE
set -ex

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REF=$(head -1 "$SCRIPT_DIR/pikafish-jieqi.ref" | tr -d '[:space:]')
echo "Building PikaJieQi from commit $REF..."

# Clone the fork (full clone to reach pinned commit)
rm -rf /tmp/pikafish-jieqi
git clone https://github.com/brianhliou/pikafish-jieqi-wasm /tmp/pikafish-jieqi
cd /tmp/pikafish-jieqi
git checkout "$REF"
echo "Checked out: $(git rev-parse HEAD)"

# ★ KING-GUARD: vá engine không sinh/không nhận nước ĂN VUA.
# Trên thế cờ úp, quân úp được phỏng đoán loại theo ô chuẩn — khi phỏng đoán
# sai có thể tạo "chiếu bóng" (vua đối thủ tưởng đang bị chiếu) → search sinh
# nước ăn vua → crash engine (assert type_of(captured) != KING) → bot chết
# giữa ván, hết quota restart. Chi tiết: engine_king_guard.patch
git apply "$SCRIPT_DIR/engine_king_guard.patch"
echo "Applied engine_king_guard.patch"

# Build
cd src
make -j$(nproc) ARCH=x86-64-sse41-popcnt build

# Copy binary to repo root
cp PikaJieQi "$SCRIPT_DIR/pikajieqi-native"
chmod +x "$SCRIPT_DIR/pikajieqi-native"

# Download NNUE from Pikafish releases (not included in jieqi repo)
if [ ! -f "$SCRIPT_DIR/pikafish.nnue" ]; then
    echo "Downloading pikafish.nnue..."
    tmp_dir=$(mktemp -d)
    trap 'rm -rf "$tmp_dir"' EXIT
    curl --fail --silent --show-error -L \
      "https://github.com/official-pikafish/Pikafish/releases/download/Pikafish-2026-09-06/Pikafish.2026-09-06.7z" \
      -o "$tmp_dir/pikafish-package.7z"
    7z x -y "$tmp_dir/pikafish-package.7z" "-o$tmp_dir/extract" >/dev/null
    test -s "$tmp_dir/extract/pikafish.nnue"
    cp "$tmp_dir/extract/pikafish.nnue" "$SCRIPT_DIR/pikafish.nnue"
fi

echo "✅ PikaJieQi built successfully"
ls -la "$SCRIPT_DIR/pikajieqi-native" "$SCRIPT_DIR/pikafish.nnue"
