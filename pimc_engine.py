#!/usr/bin/env python3
"""PIMC (Perfect Information Monte Carlo) module for the Phỏm bot.

Strategy:
  For each candidate discard, simulate N games to completion with random
  assignments of unseen cards to opponents. Compute expected cost:
    cost = avg_my_deadwood + α × P(being_eaten) × card_point
  Choose discard with lowest expected cost.

Used only for `finalRemove` (vòng chốt) — the most important decision.
Falls back to choose_discard_ai if PIMC fails or PHOM_PIMC != '1'.
"""
from __future__ import annotations

import os
import random
import sys
import time
from copy import deepcopy
from typing import Optional

# Add path to find phom_bot
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SEARCH_PATHS = [
    SCRIPT_DIR,
    os.path.dirname(SCRIPT_DIR),  # parent
    os.path.join(os.path.dirname(SCRIPT_DIR), "download"),
    os.path.join(os.path.dirname(SCRIPT_DIR), ".github", "scripts"),
    os.getcwd(),
]
for p in _SEARCH_PATHS:
    if os.path.isdir(p):
        sys.path.insert(0, p)

from phom_bot import (
    valid_card, rank, suit, point, card_name,
    best_meld_partition, can_cover_required_cards, can_form_meld_with,
    is_meld, can_extend_public_meld,
)


# ====================== PIMC Configuration ======================

PIMC_ENABLED = os.environ.get("PHOM_PIMC", "0") == "1"
N_ROLLOUTS = int(os.environ.get("PHOM_PIMC_ROLLOUTS", "150"))
EAT_PENALTY_ALPHA = float(os.environ.get("PHOM_PIMC_ALPHA", "2.0"))
TIME_BUDGET_SEC = float(os.environ.get("PHOM_PIMC_TIMEOUT", "3.0"))


# ====================== Mini simulator ======================

class PIMCGameState:
    """Minimal game state for PIMC rollouts.

    Tracks:
      - my_hand: list[int] (will simulate 3 more rounds after this discard)
      - opponent_hands: list[list[int]] (3 opponents)
      - last_discard: card currently in discard pile (top)
      - discard_pile: list (for tracking what's been discarded)
      - eaten_by_me: list (cards I've eaten — must stay covered)
      - my_discard_count: how many discards I've already made
      - max_rounds: 4 total discards per player
    """

    def __init__(self, my_hand, opponent_hands, last_discard,
                 discard_pile, eaten_by_me, my_discard_count):
        self.my_hand = list(my_hand)
        self.opponent_hands = [list(h) for h in opponent_hands]
        self.last_discard = last_discard
        self.discard_pile = list(discard_pile)
        self.eaten_by_me = list(eaten_by_me)
        self.my_discard_count = my_discard_count
        self.max_rounds = 4
        self.deck = []  # PIMC sim doesn't use deck — assume deck exhausted
        self.discarded_by_opp = [[] for _ in range(3)]
        self.eaten_by_opp = [[] for _ in range(3)]

    def my_remaining_discards(self) -> int:
        return self.max_rounds - self.my_discard_count

    def simulate_to_end(self, candidate_discard: int,
                        rng: random.Random) -> dict:
        """Simulate one full game with this candidate discard.
        Returns: {my_deadwood, was_eaten, opp_deadwoods}.

        Process:
          1. I discard `candidate_discard`.
          2. Each opponent (in order): try eat if forms meld, else skip; then discard.
          3. Repeat for my_remaining_discards rounds.
          4. At end, compute deadwood for everyone.
        """
        # Step 1: my discard
        if candidate_discard in self.my_hand:
            self.my_hand.remove(candidate_discard)
        self.discard_pile.append(candidate_discard)
        self.last_discard = candidate_discard
        was_eaten = False

        # Step 2-3: simulate remaining rounds
        rounds_left = self.my_remaining_discards() - 1  # -1 because we just discarded
        for round_idx in range(rounds_left):
            # Each opponent's turn
            for opp_idx in range(3):
                if not self.opponent_hands[opp_idx]:
                    continue
                # Try eat
                if (self.last_discard is not None
                        and can_form_meld_with(self.opponent_hands[opp_idx],
                                               self.last_discard)):
                    # Opponent eats with prob 0.7 (smart) or skips (random)
                    if rng.random() < 0.7:
                        self.opponent_hands[opp_idx].append(self.last_discard)
                        self.eaten_by_opp[opp_idx].append(self.last_discard)
                        if self.discard_pile:
                            self.discard_pile.pop()
                        was_eaten = (self.last_discard == candidate_discard
                                     and round_idx == 0)
                        self.last_discard = (self.discard_pile[-1]
                                             if self.discard_pile else None)
                # Opponent discards (use simple heuristic: lowest-point trash)
                if self.opponent_hands[opp_idx]:
                    discard = self._opponent_choose_discard(
                        opp_idx, rng)
                    if discard is not None:
                        self.opponent_hands[opp_idx].remove(discard)
                        self.discarded_by_opp[opp_idx].append(discard)
                        self.discard_pile.append(discard)
                        self.last_discard = discard

            # My turn — discard best card using simple heuristic
            if self.my_hand:
                my_disc = self._my_choose_discard(rng)
                if my_disc is not None:
                    self.my_hand.remove(my_disc)
                    self.discard_pile.append(my_disc)
                    self.last_discard = my_disc

        # Final scoring
        my_dw, my_melds = self._score_hand(self.my_hand, self.eaten_by_me)
        opp_dws = []
        for i in range(3):
            dw, _ = self._score_hand(self.opponent_hands[i],
                                     self.eaten_by_opp[i])
            opp_dws.append(dw)

        return {
            "my_deadwood": my_dw,
            "was_eaten": was_eaten,
            "opp_deadwoods": opp_dws,
            "candidate_point": point(candidate_discard),
        }

    def _opponent_choose_discard(self, opp_idx: int,
                                  rng: random.Random) -> Optional[int]:
        """Simple opponent: discard highest-point card not in a meld."""
        if not self.opponent_hands[opp_idx]:
            return None
        # Find cards not in melds
        dw, covered = self._score_hand(self.opponent_hands[opp_idx],
                                       self.eaten_by_opp[opp_idx])
        # Pick highest-point non-meld card
        candidates = []
        for i, c in enumerate(self.opponent_hands[opp_idx]):
            # Simple check: card is "trash" if not in any meld partition
            # For speed, just rank by point
            candidates.append((point(c), c))
        candidates.sort(reverse=True)
        return candidates[0][1] if candidates else None

    def _my_choose_discard(self, rng: random.Random) -> Optional[int]:
        """Simple discard heuristic for simulation — minimize deadwood."""
        if not self.my_hand:
            return None
        best = None
        best_dw = float("inf")
        for card in self.my_hand:
            remaining = [c for c in self.my_hand if c != card]
            if not can_cover_required_cards(remaining, self.eaten_by_me):
                continue
            dw, _ = best_meld_partition([*remaining, *self.eaten_by_me])
            if dw < best_dw:
                best_dw = dw
                best = card
        return best if best is not None else self.my_hand[0]

    def _score_hand(self, hand, eaten):
        """Score a hand: returns (deadwood, melds)."""
        if eaten and not can_cover_required_cards(hand, eaten):
            # Móm
            return sum(point(c) for c in hand), []
        all_cards = list(dict.fromkeys([*hand, *eaten]))
        dw, groups = best_meld_partition(all_cards)
        return dw, groups


# ====================== PIMC main function ======================

def pimc_choose_discard(
    hand: list[int],
    public_state: dict,
    my_slot: int,
    required_cards: list[int],
    forbidden_cards: set[int],
    round_idx: int,
    is_final_round: bool,
) -> Optional[int]:
    """PIMC discard chooser for final round.

    Returns the card id to discard, or None if PIMC fails.
    """
    if not PIMC_ENABLED:
        return None
    if not hand:
        return None

    start_time = time.time()
    rng = random.Random(int(start_time * 1000) % (2**31))

    # Compute seen cards (known locations)
    seen = set()
    for item in public_state.get("discarded_cards", []):
        seen.add(item["card"])
    for item in public_state.get("eaten_cards", []):
        seen.add(item["card"])
    for meld in public_state.get("exposed_melds", []):
        seen.update(meld["cards"])
    seen.update(c for c in hand if valid_card(c))

    # Unseen cards = potential opponent holdings
    unseen = [c for c in range(52) if c not in seen]

    # Number of opponents (assume 3 for 4-player game, fallback to 2 if 3-player)
    opp_slots = set()
    for item in public_state.get("eaten_cards", []):
        if item.get("to_slot") != my_slot:
            opp_slots.add(item["to_slot"])
    for meld in public_state.get("exposed_melds", []):
        if meld.get("slot") != my_slot:
            opp_slots.add(meld["slot"])
    n_opps = max(2, len(opp_slots)) if opp_slots else 3
    n_opps = min(n_opps, 3)

    # Estimate opponent hand sizes — assume 9 cards each (initial) minus discards
    # For simplicity, distribute unseen cards evenly
    opp_hand_size = max(3, len(unseen) // n_opps)

    # Generate candidates
    candidates = []
    for card in hand:
        if card in forbidden_cards:
            continue
        remaining = [c for c in hand if c != card]
        if not can_cover_required_cards(remaining, required_cards):
            continue
        candidates.append(card)

    if not candidates:
        return None

    # Limit candidates if too many (for speed)
    if len(candidates) > 8:
        # Sort by simple heuristic and take top 8
        scored = []
        for c in candidates:
            remaining = [x for x in hand if x != c]
            dw, _ = best_meld_partition([*remaining, *required_cards])
            scored.append((dw, c))
        scored.sort()
        candidates = [c for _, c in scored[:8]]

    print(f"[PIMC] round_idx={round_idx} final={is_final_round} "
          f"n_cands={len(candidates)} unseen={len(unseen)} "
          f"n_opps={n_opps} opp_hand_size={opp_hand_size}",
          flush=True)

    # Run rollouts per candidate
    results = {}
    total_sims = 0
    for cand in candidates:
        # Stop if time budget exhausted
        if time.time() - start_time > TIME_BUDGET_SEC:
            print(f"[PIMC] time budget exhausted at cand={cand} "
                  f"({total_sims} sims done)", flush=True)
            break

        dw_sum = 0
        eaten_count = 0
        win_count = 0
        n_done = 0

        for sim in range(N_ROLLOUTS):
            if time.time() - start_time > TIME_BUDGET_SEC:
                break

            # Sample opponent hands from unseen
            shuffled = unseen[:]
            rng.shuffle(shuffled)
            opp_hands = []
            for i in range(n_opps):
                opp_hands.append(shuffled[i * opp_hand_size:(i + 1) * opp_hand_size])

            # Build state
            state = PIMCGameState(
                my_hand=hand,
                opponent_hands=opp_hands,
                last_discard=public_state.get("last_discard"),
                discard_pile=[item["card"] for item in
                              public_state.get("discarded_cards", [])][-10:],
                eaten_by_me=required_cards,
                my_discard_count=round_idx,
            )

            try:
                r = state.simulate_to_end(cand, rng)
                dw_sum += r["my_deadwood"]
                if r["was_eaten"]:
                    eaten_count += 1
                # Win = my deadwood is min
                if r["my_deadwood"] < min(r["opp_deadwoods"]):
                    win_count += 1
                n_done += 1
                total_sims += 1
            except Exception:
                continue

        if n_done == 0:
            continue

        avg_dw = dw_sum / n_done
        p_eaten = eaten_count / n_done
        win_rate = win_count / n_done
        # Expected cost: deadwood + penalty for being eaten
        cost = avg_dw + EAT_PENALTY_ALPHA * p_eaten * point(cand)
        results[cand] = {
            "avg_dw": avg_dw,
            "p_eaten": p_eaten,
            "win_rate": win_rate,
            "cost": cost,
            "n_sims": n_done,
        }

    if not results:
        return None

    # Pick min cost
    best = min(results.items(), key=lambda x: x[1]["cost"])
    best_card = best[0]
    best_r = best[1]
    elapsed = time.time() - start_time
    print(f"[PIMC] chose={card_name(best_card)} "
          f"avg_dw={best_r['avg_dw']:.1f} p_eaten={best_r['p_eaten']:.2f} "
          f"win_rate={best_r['win_rate']:.2f} cost={best_r['cost']:.1f} "
          f"({total_sims} sims in {elapsed:.2f}s)",
          flush=True)

    # Print top 3 for debugging
    sorted_results = sorted(results.items(), key=lambda x: x[1]["cost"])[:3]
    for c, r in sorted_results:
        print(f"  cand={card_name(c):12s} dw={r['avg_dw']:5.1f} "
              f"p_eaten={r['p_eaten']:.2f} win={r['win_rate']:.2f} "
              f"cost={r['cost']:5.1f}",
              flush=True)

    return best_card
