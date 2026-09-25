#!/usr/bin/env python3
"""Continuous long-running self-play weight tuner for the Phỏm AI.

Designed to run for up to ~5.5 hours in a GitHub Actions job.
Strategy:
  * Hybrid parallelism: N worker processes (N = CPU count, capped at 4).
  * Each worker plays batches of K 4-player games in parallel.
  * Coordinator generates candidates, dispatches to workers, aggregates results.
  * Adopts any candidate that improves top2_rate (vs current best) by >1%.
  * Checkpoints best weights to weights.json every 5 minutes.
  * Logs progress to stdout (GitHub Actions captures it).

Tuning algorithm = hill-climb with random restart:
  1. Start from current best (or DEFAULT if no checkpoint).
  2. Each round: generate N_CANDS random perturbations (factor in [0.5..2.0]
     applied to all 15 weights independently).
  3. Evaluate each candidate over K_GAMES 4-player games (1 cand + 3 baseline).
  4. If best candidate beats current best by >1% top2_rate (validated with
     extra K_VALID games), adopt it as new baseline.
  5. If no improvement for STALE_ROUNDS rounds, restart from a fresh random
     perturbation of DEFAULT (escape local optimum).
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
import traceback
from copy import deepcopy
from multiprocessing import Pool, cpu_count
from pathlib import Path

# Add the script directory to path so we can import phom_sim
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
# Also add sibling directories where phom_sim.py / phom_bot.py might live
for candidate in [
    SCRIPT_DIR,  # scripts/ (local dev)
    SCRIPT_DIR.parent / "download",  # download/ (bot location)
    SCRIPT_DIR.parent / ".github" / "scripts",  # .github/scripts/ (CI)
    Path.cwd(),  # current working dir
    Path.cwd() / "scripts",
    Path.cwd() / ".github" / "scripts",
]:
    if candidate.exists():
        sys.path.insert(0, str(candidate))

from phom_sim import (
    DEFAULT_WEIGHTS, evaluate_weights_4p_worker,
)

# ====================== Configuration ======================

# Time budget — GitHub Actions job will be killed at this point.
# We use 325 min (5h25m) to leave 5 min buffer for final commit + push.
MAX_RUNTIME_SECONDS = int(os.environ.get("SELFPLAY_MAX_SECONDS", str(325 * 60)))

# Number of worker processes — use all available CPUs (capped at 4 to avoid
# GitHub Actions rate limits on free tier).
N_WORKERS = min(4, max(1, cpu_count()))

# Games per candidate evaluation
GAMES_PER_EVAL = int(os.environ.get("SELFPLAY_GAMES_PER_EVAL", "300"))

# Games per validation (stricter check before adopting)
GAMES_PER_VALIDATE = int(os.environ.get("SELFPLAY_GAMES_PER_VALIDATE", "500"))

# Number of candidates per round (parallelism)
N_CANDIDATES = int(os.environ.get("SELFPLAY_N_CANDS", "8"))

# Improvement threshold (top2_rate delta) to adopt a candidate
THRESHOLD = 0.01  # 1%

# Stale rounds before random restart
STALE_ROUNDS = 5

# Checkpoint interval (seconds) — write weights.json
CHECKPOINT_INTERVAL = 300  # 5 min

# Output paths
WEIGHTS_PATH = Path(os.environ.get(
    "SELFPLAY_WEIGHTS_PATH",
    str(SCRIPT_DIR.parent / "download" / "weights.json"),
))
PROGRESS_PATH = Path(os.environ.get(
    "SELFPLAY_PROGRESS_PATH",
    str(SCRIPT_DIR / "selfplay_progress.json"),
))

PHASES = ["early", "middle", "final"]
WEIGHT_KEYS = ["deadwood", "own_neighbour", "safety", "danger", "melds_after"]

# Base seed for reproducibility (incremented per round)
BASE_SEED = 17_000_000


# ====================== Helpers ======================

def random_perturb(weights: dict, rng: random.Random,
                   min_factor: float = 0.5, max_factor: float = 2.0) -> dict:
    """Return a copy of `weights` with every weight multiplied by an
    independent random factor in [min_factor, max_factor]."""
    new_w = deepcopy(weights)
    for phase in PHASES:
        for key in WEIGHT_KEYS:
            factor = rng.uniform(min_factor, max_factor)
            new_w[phase][key] = round(new_w[phase][key] * factor, 4)
    return new_w


def load_current_weights() -> dict:
    """Load current best weights from WEIGHTS_PATH, or DEFAULT if missing."""
    try:
        if WEIGHTS_PATH.exists():
            with WEIGHTS_PATH.open() as f:
                data = json.load(f)
            w = data.get("weights", data) if isinstance(data, dict) else None
            if isinstance(w, dict) and all(
                p in w and isinstance(w[p], dict)
                and all(k in w[p] for k in WEIGHT_KEYS)
                for p in PHASES
            ):
                print(f"[LOAD] Loaded weights from {WEIGHTS_PATH}", flush=True)
                return w
    except (json.JSONDecodeError, OSError) as e:
        print(f"[LOAD] Failed to load weights: {e}", flush=True)
    print(f"[LOAD] Using DEFAULT_WEIGHTS", flush=True)
    return deepcopy(DEFAULT_WEIGHTS)


def save_weights(weights: dict, top2_rate: float, win_rate: float,
                 total_games: int, round_num: int, source: str = "self-play") -> None:
    """Save weights + metadata to WEIGHTS_PATH (atomic)."""
    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "weights": weights,
        "top2_rate": round(top2_rate, 4),
        "win_rate": round(win_rate, 4),
        "games_used": total_games,
        "round": round_num,
        "source": source,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    tmp = WEIGHTS_PATH.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(out, f, indent=2)
    tmp.rename(WEIGHTS_PATH)


def evaluate_one(args):
    """Worker function: evaluate one candidate vs baseline."""
    cand, base, n_games, eval_id = args
    r = evaluate_weights_4p_worker(
        (cand, base, n_games, BASE_SEED + eval_id * 10000)
    )
    r["candidate"] = cand
    return r


# ====================== Main tuning loop ======================

def main():
    print(f"=== Continuous Self-Play Tuner ===", flush=True)
    print(f"Max runtime: {MAX_RUNTIME_SECONDS}s ({MAX_RUNTIME_SECONDS/60:.1f} min)",
          flush=True)
    print(f"Workers: {N_WORKERS}", flush=True)
    print(f"Games per eval: {GAMES_PER_EVAL}", flush=True)
    print(f"Games per validate: {GAMES_PER_VALIDATE}", flush=True)
    print(f"Candidates per round: {N_CANDIDATES}", flush=True)
    print(f"Threshold: {THRESHOLD*100:.1f}% top2_rate improvement", flush=True)
    print(f"Weights path: {WEIGHTS_PATH}", flush=True)
    print(f"Progress path: {PROGRESS_PATH}", flush=True)
    print(f"Baseline (DEFAULT) weights:", flush=True)
    print(json.dumps(DEFAULT_WEIGHTS, indent=2), flush=True)
    print("", flush=True)

    start_time = time.time()
    deadline = start_time + MAX_RUNTIME_SECONDS

    # Load current best
    current_best = load_current_weights()
    baseline_top2 = 0.50  # theoretical baseline (4 equal players)
    baseline_wr = 0.25

    # Quick baseline measurement (small batch)
    print(f"[BOOT] Measuring baseline ({GAMES_PER_EVAL} games)...", flush=True)
    boot = evaluate_weights_4p_worker(
        (current_best, DEFAULT_WEIGHTS, GAMES_PER_EVAL, BASE_SEED + 999_999)
    )
    baseline_top2 = boot["top2_rate"]
    baseline_wr = boot["win_rate"]
    baseline_dw = boot["avg_dw"]
    print(f"[BOOT] baseline: wr={baseline_wr:.3f} top2={baseline_top2:.3f} "
          f"dw={baseline_dw:.1f}\n", flush=True)

    total_games = GAMES_PER_EVAL
    round_num = 0
    stale_rounds = 0
    last_checkpoint = 0.0
    rng = random.Random(42424242)
    eval_id = 0

    # Save initial state
    save_weights(current_best, baseline_top2, baseline_wr, total_games,
                 round_num, source="initial")

    while time.time() < deadline:
        round_num += 1
        round_start = time.time()
        time_left = deadline - round_start
        if time_left < 120:  # need at least 2 min for another round
            print(f"\n[STOP] Only {time_left:.0f}s left — stopping", flush=True)
            break

        print(f"\n=== Round {round_num} === "
              f"(elapsed {(round_start-start_time)/60:.1f} min, "
              f"left {time_left/60:.1f} min)", flush=True)

        # Generate candidates
        candidates = []
        for c in range(N_CANDIDATES):
            if stale_rounds >= STALE_ROUNDS:
                # Random restart: perturb from DEFAULT instead of current
                print(f"  [RESTART] stale {stale_rounds} rounds — "
                      f"perturbing from DEFAULT", flush=True)
                cand = random_perturb(DEFAULT_WEIGHTS, rng)
                stale_rounds = 0
            else:
                cand = random_perturb(current_best, rng)
            candidates.append((cand, current_best, GAMES_PER_EVAL, eval_id))
            eval_id += 1

        # Evaluate in parallel
        results = []
        try:
            with Pool(N_WORKERS) as pool:
                for r in pool.imap_unordered(evaluate_one, candidates,
                                              chunksize=1):
                    results.append(r)
                    total_games += GAMES_PER_EVAL
                    print(f"  cand wr={r['win_rate']:.3f} "
                          f"top2={r['top2_rate']:.3f} "
                          f"dw={r['avg_dw']:.1f}", flush=True)
        except Exception as e:
            print(f"[ERROR] Pool failed: {e}", flush=True)
            traceback.print_exc()
            time.sleep(5)
            continue

        # Sort by top2_rate (higher better), then win_rate, then dw (lower better)
        results.sort(key=lambda r: (r["top2_rate"], r["win_rate"], -r["avg_dw"]),
                     reverse=True)
        best = results[0]
        delta = best["top2_rate"] - baseline_top2
        round_time = time.time() - round_start
        print(f"[Round {round_num}] best top2={best['top2_rate']:.3f} "
              f"wr={best['win_rate']:.3f} Δtop2={delta:+.3f} "
              f"({round_time:.1f}s, {total_games} games total)", flush=True)

        if delta >= THRESHOLD:
            # Validate with extra games
            print(f"  [VALIDATE] Validating with {GAMES_PER_VALIDATE} games...",
                  flush=True)
            valid = evaluate_weights_4p_worker(
                (best["candidate"], current_best, GAMES_PER_VALIDATE,
                 BASE_SEED + 80_000 + round_num * 1000)
            )
            total_games += GAMES_PER_VALIDATE
            delta_valid = valid["top2_rate"] - baseline_top2
            print(f"  [VALIDATE] top2={valid['top2_rate']:.3f} "
                  f"Δ={delta_valid:+.3f}", flush=True)
            if delta_valid >= THRESHOLD:
                current_best = deepcopy(best["candidate"])
                baseline_top2 = valid["top2_rate"]
                baseline_wr = valid["win_rate"]
                baseline_dw = valid["avg_dw"]
                stale_rounds = 0
                print(f"  [ADOPT] new baseline: top2={baseline_top2:.3f} "
                      f"wr={baseline_wr:.3f}", flush=True)
                # Save immediately on improvement
                save_weights(current_best, baseline_top2, baseline_wr,
                             total_games, round_num, source="self-play-improved")
                print(f"  [SAVE] weights.json updated", flush=True)
            else:
                print(f"  [REJECT] validation failed", flush=True)
                stale_rounds += 1
        else:
            stale_rounds += 1

        # Periodic checkpoint
        now = time.time()
        if now - last_checkpoint >= CHECKPOINT_INTERVAL:
            save_weights(current_best, baseline_top2, baseline_wr,
                         total_games, round_num, source="self-play-checkpoint")
            # Also save progress log
            progress = {
                "round": round_num,
                "elapsed_sec": now - start_time,
                "total_games": total_games,
                "current_top2": baseline_top2,
                "current_win_rate": baseline_wr,
                "stale_rounds": stale_rounds,
                "games_per_sec": total_games / (now - start_time),
                "current_weights": current_best,
            }
            try:
                with PROGRESS_PATH.open("w") as f:
                    json.dump(progress, f, indent=2)
            except OSError as e:
                print(f"  [WARN] progress save failed: {e}", flush=True)
            last_checkpoint = now
            print(f"  [CHECKPOINT] saved (elapsed "
                  f"{(now-start_time)/60:.1f} min, "
                  f"{total_games/(now-start_time):.0f} games/s, "
                  f"top2={baseline_top2:.3f})", flush=True)

    # Final save
    elapsed = time.time() - start_time
    save_weights(current_best, baseline_top2, baseline_wr, total_games,
                 round_num, source="self-play-final")
    print(f"\n=== DONE ===", flush=True)
    print(f"Total runtime: {elapsed/60:.1f} min ({elapsed:.0f}s)", flush=True)
    print(f"Total games: {total_games:,}", flush=True)
    print(f"Games/sec: {total_games/elapsed:.0f}", flush=True)
    print(f"Rounds: {round_num}", flush=True)
    print(f"Final top2_rate: {baseline_top2:.3f}", flush=True)
    print(f"Final win_rate:  {baseline_wr:.3f}", flush=True)
    print(f"\nFinal weights:", flush=True)
    print(json.dumps(current_best, indent=2), flush=True)
    print(f"\nSaved to: {WEIGHTS_PATH}", flush=True)


if __name__ == "__main__":
    main()
