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
  # Replay public mover reveals, an open-mover dark capture (5 chars), and a
  # dark-mover dark capture (6 chars). This is the protocol the fair adapter
  # uses to keep JieqiCore's rest-piece pools synchronized.
  echo 'position startpos moves a3a4P c6c5p a4a5 e6e5p a5b5 g6g5p b2b7Rn'
  echo 'go nodes 3000'
  echo 'quit'
} | timeout 30 "$ENGINE")"
grep -q '^readyok$' <<<"$search_output"
grep -Eq '^bestmove [a-i][0-9][a-i][0-9]' <<<"$search_output"

echo "JieqiCore verification passed"
