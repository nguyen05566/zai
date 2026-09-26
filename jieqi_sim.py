#!/usr/bin/env python3
"""Jieqi (Cờ Úp) self-play simulator with parameterized Mistboard engine.

Strategy:
  * Use the existing pikajieqi-mistboard binary (or pikajieqi-native) for
    move selection.
  * Simulate games between two engine instances with different UCI params.
  * Track win/draw/loss to compare param sets.

The simulator does NOT implement full Xiangqi rules — it relies on the
engine to produce legal moves and detect game end. The simulator just:
  1. Sets up the initial Cờ Úp position (dark pieces randomly assigned).
  2. Alternates turns: ask engine for bestmove, apply it.
  3. Stops when engine returns "0000" (no move) or after MAX_MOVES.

Used by jieqi_selfplay_tune.py to optimize UCI params via hill-climbing.
"""
from __future__ import annotations

import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# ============================================================================
# Constants
# ============================================================================

INITIAL_FEN = "xxxxkxxxx/9/1x5x1/x1x1x1x1x/9/9/X1X1X1X1X/1X5X1/9/XXXXKXXXX"
INITIAL_BAG = "A2B2N2R2C2P5a2b2n2r2c2p5"
BAG_ORDER = ['A', 'B', 'N', 'R', 'C', 'P', 'a', 'b', 'n', 'r', 'c', 'p']
UCI_MOVE_RE = re.compile(r'^[a-i]\d[a-i]\d[a-zA-Z]?$')

# Default Mistboard params (matching cup_bot_mistboard.py)
DEFAULT_PARAMS = {
    "Threads": "1",
    "Hash": "64",
    "MultiPV": "1",
    "Move Overhead": "10",
    "Slow Mover": "100",
    "Skill Level": "20",  # max
    "UCI_AnalyseMode": "false",
}

MAX_MOVES = 60           # max moves per game (use material balance after this)
DEFAULT_MOVETIME_MS = 500  # 500ms per move — needs more depth to be decisive
ENGINE_STARTUP_TIMEOUT = 5.0


def parse_material_from_fen(fen: str) -> int:
    """Rough material balance from FEN. Positive = white ahead, negative = black.

    Approximate values:
      K=10000 (captured = game over)
      R=9, C=4.5, N=4, B=2, A=2, P=1
      (uppercase = white, lowercase = black)
    """
    piece_values = {
        'K': 10000, 'A': 200, 'B': 200, 'N': 400, 'R': 900,
        'C': 450, 'P': 100,
        'k': -10000, 'a': -200, 'b': -200, 'n': -400, 'r': -900,
        'c': -450, 'p': -100,
    }
    board = fen.split(' ')[0]
    score = 0
    for ch in board:
        if ch in piece_values:
            score += piece_values[ch]
        # 'x' and 'X' are dark (unrevealed) pieces — value ~0 for simplicity
    return score


# ============================================================================
# Engine wrapper
# ============================================================================

class JieqiEngine:
    """Wrap the pikajieqi-mistboard / pikajieqi-native binary in UCI mode."""

    def __init__(self, binary_path: str, params: dict = None,
                 name: str = "engine"):
        self.binary_path = binary_path
        self.params = {**DEFAULT_PARAMS, **(params or {})}
        self.name = name
        self.proc: Optional[subprocess.Popen] = None
        self._latest_bestmove = None
        self._latest_score = None  # centipawn score from engine
        self._init_engine()

    def _init_engine(self):
        if not os.path.isfile(self.binary_path):
            raise FileNotFoundError(f"Engine binary not found: {self.binary_path}")
        if not os.access(self.binary_path, os.X_OK):
            os.chmod(self.binary_path, 0o755)

        self.proc = subprocess.Popen(
            [self.binary_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        # Send uci + setoptions + isready
        self._send("uci")
        # Wait for uciok
        self._wait_for("uciok", timeout=ENGINE_STARTUP_TIMEOUT)
        for k, v in self.params.items():
            self._send(f"setoption name {k} value {v}")
        self._send("isready")
        self._wait_for("readyok", timeout=ENGINE_STARTUP_TIMEOUT)

    def _send(self, cmd: str):
        if self.proc and self.proc.stdin:
            self.proc.stdin.write((cmd + "\n").encode())
            self.proc.stdin.flush()

    def _wait_for(self, token: str, timeout: float = 5.0) -> Optional[str]:
        if not self.proc or not self.proc.stdout:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                time.sleep(0.001)
                continue
            line = line.decode("utf-8", "replace").strip()
            if token in line:
                return line
        return None

    def get_bestmove(self, fen: str, moves: list[str],
                     movetime_ms: int = DEFAULT_MOVETIME_MS) -> Optional[str]:
        """Ask engine for bestmove. Returns 'c3c4' or 'c3c4B' format, or None.

        Also captures the latest eval score (centipawn from side-to-move POV)
        in self._latest_score, accessible via get_last_score().
        """
        if not self.proc or not self.proc.stdout:
            return None
        self._latest_bestmove = None
        self._latest_score = None
        # Build position command
        cmd = f"position fen {fen}"
        if moves:
            cmd += " moves " + " ".join(moves)
        self._send(cmd)
        self._send(f"go movetime {movetime_ms}")

        # Read until bestmove, capturing score
        deadline = time.time() + (movetime_ms / 1000.0) + 1.5
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                time.sleep(0.001)
                continue
            line = line.decode("utf-8", "replace").strip()
            if line.startswith("info") and "score cp" in line:
                # Parse "score cp <value>" or "score mate <value>"
                m = re.search(r"score cp (-?\d+)", line)
                if m:
                    self._latest_score = int(m.group(1))
                else:
                    m2 = re.search(r"score mate (-?\d+)", line)
                    if m2:
                        # Convert mate to large cp
                        self._latest_score = 100000 if int(m2.group(1)) > 0 else -100000
            elif line.startswith("bestmove"):
                parts = line.split()
                if len(parts) >= 2 and parts[1] != "0000":
                    self._latest_bestmove = parts[1]
                return self._latest_bestmove
        return None

    def get_last_score(self) -> Optional[int]:
        """Return centipawn score from the last get_bestmove call.
        Score is from the perspective of the side that just moved (the
        opponent of side-to-move at the time of the search).
        """
        return self._latest_score

    def quit(self):
        try:
            self._send("quit")
            if self.proc:
                self.proc.wait(timeout=2)
        except Exception:
            pass
        finally:
            if self.proc:
                try:
                    self.proc.kill()
                except Exception:
                    pass
                self.proc = None


# ============================================================================
# Game simulator
# ============================================================================

class JieqiGame:
    """Simulate one Cờ Úp game between two engines."""

    def __init__(self, engine_a: JieqiEngine, engine_b: JieqiEngine,
                 movetime_ms: int = DEFAULT_MOVETIME_MS,
                 seed: int | None = None):
        self.engine_w = engine_a  # plays white (uppercase X = red)
        self.engine_b = engine_b  # plays black (lowercase x)
        self.movetime_ms = movetime_ms
        self.moves: list[str] = []
        self.start_fen = f"{INITIAL_FEN} {INITIAL_BAG} w - - 0 1"
        self.start_side = 'w'
        self.game_over = False
        self.winner = None  # 'w', 'b', or 'draw'
        self.move_count = 0
        self.rng = random.Random(seed)

    def play(self) -> dict:
        """Play full game. Returns {'winner', 'moves', 'reason', 'final_score'}.

        Win condition priority:
          1. Engine returns no move (current side loses)
          2. After MAX_MOVES: use engine's last eval score as tiebreaker
             (positive = white ahead, negative = black ahead)
        """
        side = self.start_side
        last_score = 0  # score from last engine's POV (side-to-move)
        while not self.game_over and self.move_count < MAX_MOVES:
            engine = self.engine_w if side == 'w' else self.engine_b
            bestmove = engine.get_bestmove(
                self.start_fen, self.moves, self.movetime_ms
            )
            # Capture score from this engine's POV (side-to-move)
            score = engine.get_last_score()
            if score is not None:
                # Engine reports score from side-to-move POV.
                # Convert to white's POV: if side == 'b', negate.
                last_score = score if side == 'w' else -score

            if bestmove is None or bestmove == "0000":
                # No move — current side loses (or draw if early)
                if self.move_count < 10:
                    self.winner = 'draw'
                else:
                    self.winner = 'b' if side == 'w' else 'w'
                self.game_over = True
                break

            self.moves.append(bestmove)
            self.move_count += 1
            side = 'b' if side == 'w' else 'w'

        if not self.game_over:
            # Max moves reached — use engine score as tiebreaker
            # Threshold: > 100cp = decisive advantage
            if last_score > 100:
                self.winner = 'w'
            elif last_score < -100:
                self.winner = 'b'
            else:
                # Close game — use deterministic random tiebreaker
                self.winner = 'w' if self.rng.random() < 0.5 else 'b'
            self.game_over = True

        return {
            "winner": self.winner,
            "moves": self.move_count,
            "reason": "max_moves" if self.move_count >= MAX_MOVES else "no_move",
            "final_score": last_score,
        }


# ============================================================================
# Worker function for multiprocessing
# ============================================================================

def evaluate_params_worker(args) -> dict:
    """Play n_games between candidate params and baseline params.

    args = (binary_path, candidate_params, baseline_params, n_games,
            base_seed, movetime_ms, candidate_side)
    """
    (binary_path, candidate, baseline, n_games, base_seed,
     movetime_ms, side) = args

    # Spawn 2 engines (one with candidate params, one with baseline)
    # We re-use the same engines for all games to avoid restart overhead.
    try:
        if side == 0:
            engine_a = JieqiEngine(binary_path, candidate, "cand")
            engine_b = JieqiEngine(binary_path, baseline, "base")
            cand_side = 'w'
        else:
            engine_a = JieqiEngine(binary_path, baseline, "base")
            engine_b = JieqiEngine(binary_path, candidate, "cand")
            cand_side = 'b'
    except Exception as e:
        return {"wins": 0, "losses": 0, "draws": n_games, "n_games": n_games,
                "win_rate": 0.0, "error": str(e)}

    wins = 0
    losses = 0
    draws = 0
    errors = 0

    try:
        for i in range(n_games):
            game = JieqiGame(engine_a, engine_b, movetime_ms=movetime_ms,
                             seed=base_seed + i)
            try:
                result = game.play()
            except Exception as e:
                errors += 1
                draws += 1
                continue

            if result["winner"] == cand_side:
                wins += 1
            elif result["winner"] == 'draw':
                draws += 1
            else:
                losses += 1
    finally:
        engine_a.quit()
        engine_b.quit()

    return {
        "wins": wins, "losses": losses, "draws": draws,
        "errors": errors, "n_games": n_games,
        "win_rate": wins / n_games if n_games else 0.0,
    }


# ============================================================================
# Main: smoke test
# ============================================================================

if __name__ == "__main__":
    # Find engine binary
    candidates = [
        os.environ.get("MISTBOARD_JIEQI_ENGINE"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "pikajieqi-mistboard"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "pikajieqi-native"),
        "/tmp/zai_repo4/pikajieqi-native",
        "/home/z/my-project/download/pikajieqi-native",
    ]
    binary = None
    for c in candidates:
        if c and os.path.isfile(c):
            binary = c
            break

    if not binary:
        print("ERROR: No engine binary found. Set MISTBOARD_JIEQI_ENGINE env.")
        sys.exit(1)

    print(f"=== Jieqi Simulator Smoke Test ===")
    print(f"Binary: {binary}")
    print(f"Params: {DEFAULT_PARAMS}")
    print(f"Playing 5 games, {DEFAULT_MOVETIME_MS}ms/move...")
    print()

    result = evaluate_params_worker(
        (binary, DEFAULT_PARAMS, DEFAULT_PARAMS, 5, 42,
         DEFAULT_MOVETIME_MS, 0)
    )
    print(f"Result: {result}")
