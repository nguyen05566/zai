#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
ENGINE="${1:-$ROOT/jieqi-core}"
[[ -x "$ENGINE" ]] || { echo "Missing JieqiCore executable: $ENGINE" >&2; exit 1; }

uci_output="$(printf 'uci\nquit\n' | "$ENGINE")"
grep -q '^id name JieqiCore 0\.1\.0$' <<<"$uci_output"
grep -q '^uciok$' <<<"$uci_output"

search_output="$({
  echo 'uci'
  echo 'setoption name Threads value 1'
  echo 'setoption name Hash value 16'
  echo 'isready'
  echo 'position startpos moves a3a4R'
  echo 'go nodes 3000'
  echo 'quit'
} | timeout 30 "$ENGINE")"
grep -q '^readyok$' <<<"$search_output"
grep -Eq '^bestmove [a-i][0-9][a-i][0-9]' <<<"$search_output"

echo "JieqiCore verification passed"
