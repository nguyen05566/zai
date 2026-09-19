#!/bin/bash
# Build ZaiQi: an independent cờ úp UCI engine written for this bot.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

g++ -std=c++17 -O3 -pthread -Wall -Wextra \
  -o "$SCRIPT_DIR/zaiqi" "$SCRIPT_DIR/zaiqi_engine.cpp"
chmod +x "$SCRIPT_DIR/zaiqi"

# Protocol smoke tests.
printf 'uci\nisready\nquit\n' | "$SCRIPT_DIR/zaiqi" > /tmp/zaiqi-uci.log
grep -q '^uciok$' /tmp/zaiqi-uci.log
grep -q '^readyok$' /tmp/zaiqi-uci.log

{
  echo uci
  echo isready
  echo 'position startpos moves c3c4R h9g7n'
  echo 'go infinite'
  sleep 1
  echo stop
  sleep 1
  echo quit
} | timeout 10 "$SCRIPT_DIR/zaiqi" > /tmp/zaiqi-game.log
grep -Eq '^bestmove [a-i][0-9][a-i][0-9]' /tmp/zaiqi-game.log

echo "ZaiQi built successfully"
ls -la "$SCRIPT_DIR/zaiqi"
