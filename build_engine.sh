#!/bin/bash
# build_engine.sh — Build engine cho bot
# Engine chính: OpenJieqi (clone + make ~3s, 85KB, KHÔNG cần NNUE)
# Fallback:     official Pikafish jieqi_old + KING-GUARD + NNUE tự train
#               (dùng khi OpenJieqi clone/build thất bại)
set -ex

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

build_openjieqi() {
    echo "=== Building OpenJieqi (engine chính) ==="
    rm -rf /tmp/open-jieqi
    git clone --depth 1 https://github.com/nguyen05566/open-jieqi /tmp/open-jieqi
    cd /tmp/open-jieqi
    make -j"$(nproc)"
    test -x open-jieqi
    # Smoke test cơ bản
    printf 'uci\nisready\nquit\n' | ./open-jieqi | grep -q uciok
    printf 'uci\nisready\nquit\n' | ./open-jieqi | grep -q readyok
    cp open-jieqi "$SCRIPT_DIR/pikajieqi-native"
    chmod +x "$SCRIPT_DIR/pikajieqi-native"
    echo "OpenJieqi 1.0" > "$SCRIPT_DIR/engine_brand.txt"
}

build_jieqi_old() {
    echo "=== Fallback: official Pikafish jieqi_old + trained NNUE ==="
    REF=$(head -1 "$SCRIPT_DIR/pikafish-jieqi.ref" | tr -d '[:space:]')
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
    echo "PikaJieQi jieqi_old (KING-GUARD)" > "$SCRIPT_DIR/engine_brand.txt"

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
    ls -la "$SCRIPT_DIR"/zai_*.nnue 2>/dev/null || true
}

if build_openjieqi; then
    echo "✅ Engine chính: OpenJieqi"
else
    echo "⚠️ OpenJieqi build thất bại — chuyển sang jieqi_old + NNUE fallback"
    build_jieqi_old
    echo "✅ Engine fallback: PikaJieQi jieqi_old (KING-GUARD) + trained NNUE"
fi

echo "✅ Engine built: $(cat "$SCRIPT_DIR/engine_brand.txt")"
ls -la "$SCRIPT_DIR/pikajieqi-native"
