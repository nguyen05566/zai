#!/usr/bin/env bash
# Smoke-test the exact engine/NNUE/protocol combination used by the Cờ Úp bot.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
ENGINE="$ROOT/pikajieqi-native"
NNUE="$ROOT/zai_jieqi_master.nnue"

[[ -x "$ENGINE" ]] || { echo "Missing executable: $ENGINE" >&2; exit 1; }
[[ -s "$NNUE" ]] || { echo "Missing NNUE: $NNUE" >&2; exit 1; }

uci_output="$(printf 'uci\nquit\n' | "$ENGINE")"
echo "$uci_output"
grep -q 'option name Threads type spin' <<< "$uci_output"
grep -q 'option name Hash type spin' <<< "$uci_output"
grep -q 'option name Ponder type check' <<< "$uci_output"
grep -q 'option name EvalFile type string' <<< "$uci_output"

log="$(mktemp)"
trap 'rm -f "$log"' EXIT

# Use the bot's real search flow and a reveal suffix used by mystery xiangqi.
{
  echo "uci"
  echo "setoption name Threads value 2"
  echo "setoption name Hash value 256"
  echo "setoption name Ponder value true"
  echo "setoption name EvalFile value $NNUE"
  echo "setoption name MultiPV value 1"
  echo "isready"
  echo "position startpos moves c3c4R"
  echo "go infinite"
  sleep 2
  echo "stop"
  sleep 1
  echo "quit"
} | timeout 30 "$ENGINE" 2>&1 | tee "$log"

grep -q '^readyok$' "$log"
grep -Eq '^info depth [1-9][0-9]* .* nodes [1-9][0-9]* ' "$log"
grep -Eq '^bestmove [a-i][0-9][a-i][0-9]' "$log"

echo "Cờ Úp engine smoke test passed with $NNUE"
