#!/usr/bin/env python3
"""Hill-climb tuner for Mistboard UCI params via self-play.

Strategy:
  1. Start from DEFAULT_PARAMS.
  2. Each iteration: perturb one param by ±20%, evaluate vs baseline.
  3. Adopt best candidate if win_rate > baseline + threshold.
  4. Stop when no improvement or time budget exhausted.

Designed to run for up to ~5 hours in GitHub Actions.
"""
from __future__ import annotations

import json
import os
import sys
import time
from copy import deepcopy
from multiprocessing import Pool, cpu_count

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jieqi_sim import (
    DEFAULT_PARAMS, evaluate_params_worker, DEFAULT_MOVETIME_MS,
)

# ============================================================================
# Config
# ============================================================================

TOTAL_GAME_BUDGET = int(os.environ.get("JIEQI_TOTAL_GAMES", "999999"))
GAMES_PER_EVAL = int(os.environ.get("JIEQI_GAMES_PER_EVAL", "30"))
N_WORKERS = min(2, max(1, cpu_count()))
MOVETIME_MS = int(os.environ.get("JIEQI_MOVETIME_MS",
                                  str(DEFAULT_MOVETIME_MS)))
THRESHOLD = float(os.environ.get("JIEQI_THRESHOLD", "0.05"))  # 5% (lowered from 10%)
MAX_ITERS = 50  # increased — let it run as long as time allows

# Tunable params — list of (param_name, [factor_low, factor_high])
# Note: most UCI params are integers, so we perturb by ±1 step.
TUNABLE_PARAMS = [
    # (param_name, perturb_values, min, max)
    ("Threads", [1, 2], 1, 4),
    ("Hash", [32, 128, 256], 16, 1024),
    ("MultiPV", [1, 2, 3], 1, 5),
    ("Move Overhead", [5, 30, 100], 0, 5000),
    ("Slow Mover", [50, 200], 10, 1000),
    ("Skill Level", [10, 15, 18, 19, 20], 0, 20),
]

# Output paths
PARAMS_PATH = os.environ.get(
    "JIEQI_PARAMS_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "jieqi_best_params.json"),
)


def find_binary():
    candidates = [
        os.environ.get("MISTBOARD_JIEQI_ENGINE"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "pikajieqi-mistboard"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "pikajieqi-native"),
        "pikajieqi-native",
        "pikajieqi-mistboard",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def perturb(params: dict, param_name: str, new_value) -> dict:
    new_p = deepcopy(params)
    new_p[param_name] = str(new_value)
    return new_p


def evaluate_one(args):
    binary, cand, base, n_games, base_seed, movetime = args
    # Run half games as candidate=white, half as black
    half = n_games // 2
    if half == 0:
        half = 1
    r0 = evaluate_params_worker(
        (binary, cand, base, half, base_seed, movetime, 0)
    )
    r1 = evaluate_params_worker(
        (binary, cand, base, n_games - half, base_seed + 1000, movetime, 1)
    )
    total_wins = r0["wins"] + r1["wins"]
    total_losses = r0["losses"] + r1["losses"]
    total_draws = r0["draws"] + r1["draws"]
    total_n = r0["n_games"] + r1["n_games"]
    return {
        "wins": total_wins,
        "losses": total_losses,
        "draws": total_draws,
        "n_games": total_n,
        "win_rate": total_wins / total_n if total_n else 0.0,
        "candidate": cand,
        "errors": r0.get("errors", 0) + r1.get("errors", 0),
    }


def main():
    print("=== Jieqi Self-Play Tuner ===", flush=True)
    binary = find_binary()
    if not binary:
        print(f"ERROR: No engine binary found.", flush=True)
        return 1
    print(f"Binary: {binary}", flush=True)
    print(f"Budget: {TOTAL_GAME_BUDGET} games", flush=True)
    print(f"Games per eval: {GAMES_PER_EVAL}", flush=True)
    print(f"Workers: {N_WORKERS}", flush=True)
    print(f"Movetime: {MOVETIME_MS}ms/move", flush=True)
    print(f"Threshold: {THRESHOLD*100:.1f}%", flush=True)
    print(f"Output: {PARAMS_PATH}", flush=True)
    print(f"Default params: {DEFAULT_PARAMS}", flush=True)
    print("", flush=True)

    current_best = deepcopy(DEFAULT_PARAMS)
    baseline_wr = 0.5  # theoretical baseline
    total_games = 0
    start = time.time()
    eval_id = 0
    max_runtime_sec = int(os.environ.get("JIEQI_MAX_SECONDS", "0"))  # 0 = unlimited
    deadline = (start + max_runtime_sec) if max_runtime_sec > 0 else None

    # Quick baseline measurement (cand vs cand = should be ~50%)
    print(f"[BOOT] Measuring baseline ({GAMES_PER_EVAL} games)...", flush=True)
    boot = evaluate_one(
        (binary, current_best, current_best, GAMES_PER_EVAL,
         999_999, MOVETIME_MS)
    )
    total_games += GAMES_PER_EVAL * 2
    baseline_wr = boot["win_rate"]
    print(f"[BOOT] baseline wr={baseline_wr:.3f} "
          f"(draws={boot['draws']}/{boot['n_games']})\n", flush=True)

    for it in range(MAX_ITERS):
        if total_games >= TOTAL_GAME_BUDGET:
            print(f"[STOP] Budget exhausted ({total_games} games)", flush=True)
            break
        if deadline and time.time() >= deadline:
            print(f"[STOP] Time limit reached ({time.time()-start:.0f}s elapsed)",
                  flush=True)
            break

        print(f"=== Iteration {it+1} ===", flush=True)

        # Generate all candidates
        candidates = []
        for param_name, values, _min, _max in TUNABLE_PARAMS:
            current_val = current_best.get(param_name)
            for v in values:
                if str(v) == str(current_val):
                    continue
                cand = perturb(current_best, param_name, v)
                candidates.append((cand, param_name, v))

        if not candidates:
            print("[STOP] No candidates to test", flush=True)
            break

        # Evaluate in parallel
        eval_args = [
            (binary, cand, current_best, GAMES_PER_EVAL,
             100_000 + eval_id * 10_000, MOVETIME_MS)
            for eval_id, (cand, _, _) in enumerate(candidates)
        ]
        eval_id += len(candidates)

        results = []
        with Pool(N_WORKERS) as pool:
            for r in pool.imap_unordered(evaluate_one, eval_args, chunksize=1):
                results.append(r)
                total_games += GAMES_PER_EVAL * 2
                # Find what was perturbed
                tag = "?"
                for pn, vals, _, _ in TUNABLE_PARAMS:
                    for v in vals:
                        test_p = perturb(current_best, pn, v)
                        if (r["candidate"] == test_p):
                            tag = f"{pn}={v}"
                            break
                print(f"  {tag:25s} wr={r['win_rate']:.3f} "
                      f"(W{r['wins']}-L{r['losses']}-D{r['draws']})",
                      flush=True)

        # Sort by win_rate desc, draws asc (more decisive is better)
        results.sort(key=lambda r: (r["win_rate"], -r["draws"]), reverse=True)
        best = results[0]
        delta = best["win_rate"] - baseline_wr
        print(f"[iter {it+1}] best wr={best['win_rate']:.3f} "
              f"(baseline {baseline_wr:.3f}, Δ={delta:+.3f})",
              flush=True)

        if delta >= THRESHOLD:
            current_best = deepcopy(best["candidate"])
            baseline_wr = best["win_rate"]
            print(f"[ADOPT] new baseline: {current_best}\n", flush=True)
            # Save checkpoint
            save_params(current_best, baseline_wr, total_games, it+1,
                        source="self-play-improved")
        else:
            print(f"[REJECT] no significant improvement\n", flush=True)

        # Always save checkpoint after each iteration (in case timeout kills process)
        save_params(current_best, baseline_wr, total_games, it+1,
                    source=f"self-play-checkpoint-iter{it+1}")

    elapsed = time.time() - start
    print(f"\n=== DONE ===", flush=True)
    print(f"Total games: {total_games}", flush=True)
    print(f"Elapsed: {elapsed:.1f}s ({total_games/max(elapsed,1):.1f} games/s)",
          flush=True)
    print(f"Final win_rate: {baseline_wr:.3f}", flush=True)
    print(f"Final params: {current_best}", flush=True)

    save_params(current_best, baseline_wr, total_games, MAX_ITERS,
                source="self-play-final")
    return 0


def save_params(params: dict, win_rate: float, total_games: int,
                iteration: int, source: str):
    out = {
        "params": params,
        "win_rate": round(win_rate, 4),
        "games_used": total_games,
        "iteration": iteration,
        "source": source,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        with open(PARAMS_PATH, "w") as f:
            json.dump(out, f, indent=2)
        print(f"[SAVE] {PARAMS_PATH}", flush=True)
    except OSError as e:
        print(f"[WARN] save failed: {e}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
