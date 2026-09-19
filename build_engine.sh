#!/bin/bash
# build_engine.sh — Build ForgeQi (engine cờ úp độc lập, duy nhất của bot)
# Classical eval tích hợp sẵn — KHÔNG cần NNUE, KHÔNG phụ thuộc Pikafish.
set -ex

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

rm -rf /tmp/forgeqi
git clone --depth 1 https://github.com/nguyen05566/forgeqi /tmp/forgeqi
cd /tmp/forgeqi
bash scripts/build.sh
test -x forgeqi

# Smoke test: engine phải trả lời UCI cơ bản
printf 'uci\nisready\nquit\n' | ./forgeqi | grep -q uciok
printf 'uci\nisready\nquit\n' | ./forgeqi | grep -q readyok

# Smoke test thế cờ úp: FEN X/x + BAG + reveal suffix + go infinite/stop
{
  echo "uci"
  echo "position startpos moves c3c4R h9g7n"
  echo "go infinite"
  sleep 2
  echo "stop"
  sleep 1
  echo "quit"
} | timeout 20 ./forgeqi | tee /tmp/fq_smoke.log
grep -q '^bestmove ' /tmp/fq_smoke.log

cp forgeqi "$SCRIPT_DIR/forgeqi"
chmod +x "$SCRIPT_DIR/forgeqi"
echo "✅ ForgeQi built successfully"
ls -la "$SCRIPT_DIR/forgeqi"
