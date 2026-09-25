#!/usr/bin/env python3
"""IS-MCTS style eat decision module for the Phỏm bot.

When state is 'eat' and last_discard can form a meld with our hand, decide:
  - EAT: take the discard, must keep it covered by a meld
  - TAKE: draw from deck (random card)

Strategy: simulate N=50 games for each option, pick lower expected cost.

Cost = avg_my_deadwood + α × P(being_eaten_in_future) × avg_eaten_card_point

Loaded only when PHOM_ISMCTS=1.
"""
from __future__ import annotations

import os
import random
import sys
import time
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SEARCH_PATHS = [
    SCRIPT_DIR,
    os.path.dirname(SCRIPT_DIR),
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
)

# Reuse PIMC's simulator
try:
    from pimc_engine import PIMCGameState
except ImportError:
    PIMCGameState = None


# ====================== IS-MCTS Configuration ======================

ISMCTS_ENABLED = os.environ.get("PHOM_ISMCTS", "0") == "1"
N_ROLLOUTS = int(os.environ.get("PHOM_ISMCTS_ROLLOUTS", "50"))
TIME_BUDGET_SEC = float(os.environ.get("PHOM_ISMCTS_TIMEOUT", "2.0"))


# ====================== IS-MCTS eat decision ======================

def ismcts_decide_eat(
    hand: list[int],
    last_discard: int | None,
    public_state: dict,
    my_slot: int,
    required_cards: list[int],
    round_idx: int,
) -> Optional[bool]:
    """Decide EAT (True) vs TAKE (False) using IS-MCTS rollouts.

    Returns None if IS-MCTS not applicable or fails.
    """
    if not ISMCTS_ENABLED or PIMCGameState is None:
        return None
    if last_discard is None or not valid_card(last_discard):
        return None
    if not can_form_meld_with(hand, last_discard):
        return None  # can't eat anyway

    start_time = time.time()
    rng = random.Random(int(start_time * 1000) % (2**31))

    # Compute seen / unseen
    seen = set()
    for item in public_state.get("discarded_cards", []):
        seen.add(item["card"])
    for item in public_state.get("eaten_cards", []):
        seen.add(item["card"])
    for meld in public_state.get("exposed_melds", []):
        seen.update(meld["cards"])
    seen.update(c for c in hand if valid_card(c))
    unseen = [c for c in range(52) if c not in seen]

    # Opponent count
    opp_slots = set()
    for item in public_state.get("eaten_cards", []):
        if item.get("to_slot") != my_slot:
            opp_slots.add(item["to_slot"])
    for meld in public_state.get("exposed_melds", []):
        if meld.get("slot") != my_slot:
            opp_slots.add(meld["slot"])
    n_opps = max(2, len(opp_slots)) if opp_slots else 3
    n_opps = min(n_opps, 3)
    opp_hand_size = max(3, len(unseen) // n_opps)

    # Check if EAT is legal (required cards still coverable after eating)
    new_required = list(required_cards) + [last_discard]
    if not can_cover_required_cards(hand, new_required):
        return False  # EAT is illegal — must TAKE

    # Simulate both branches
    def simulate_branch(eat: bool, n_sims: int) -> dict:
        """Run n_sims rollouts for either EAT or TAKE branch."""
        dw_sum = 0
        eaten_count = 0
        n_done = 0

        for _ in range(n_sims):
            if time.time() - start_time > TIME_BUDGET_SEC:
                break

            # Sample opponent hands
            shuffled = unseen[:]
            rng.shuffle(shuffled)
            opp_hands = []
            for i in range(n_opps):
                opp_hands.append(shuffled[i * opp_hand_size:(i + 1) * opp_hand_size])

            # Branch state
            if eat:
                # We eat: card goes to hand + required
                sim_hand = list(hand) + [last_discard]
                sim_required = list(new_required)
                sim_last = None  # discard pile cleared after eat
                sim_discard_pile = [item["card"] for item in
                                    public_state.get("discarded_cards", [])][-10:]
                if sim_discard_pile and sim_discard_pile[-1] == last_discard:
                    sim_discard_pile.pop()
            else:
                # We take from deck — sample a random unseen card
                if unseen:
                    drawn = rng.choice(unseen)
                    sim_hand = list(hand) + [drawn]
                else:
                    sim_hand = list(hand)
                sim_required = list(required_cards)
                sim_last = last_discard  # still in discard pile
                sim_discard_pile = [item["card"] for item in
                                    public_state.get("discarded_cards", [])][-10:]

            state = PIMCGameState(
                my_hand=sim_hand,
                opponent_hands=opp_hands,
                last_discard=sim_last,
                discard_pile=sim_discard_pile,
                eaten_by_me=sim_required,
                my_discard_count=round_idx,
            )

            # We need to discard something now (since we just ate/took)
            # Use simple heuristic
            discard = state._my_choose_discard(rng)
            if discard is None:
                continue

            try:
                r = state.simulate_to_end(discard, rng)
                dw_sum += r["my_deadwood"]
                if r["was_eaten"]:
                    eaten_count += 1
                n_done += 1
            except Exception:
                continue

        if n_done == 0:
            return {"avg_dw": float("inf"), "p_eaten": 1.0, "n": 0}

        return {
            "avg_dw": dw_sum / n_done,
            "p_eaten": eaten_count / n_done,
            "n": n_done,
        }

    # Run both branches
    eat_result = simulate_branch(True, N_ROLLOUTS)
    take_result = simulate_branch(False, N_ROLLOUTS)

    if eat_result["n"] == 0 and take_result["n"] == 0:
        return None  # both failed

    # Cost = avg_dw + α × p_eaten × point(last_discard)
    α = 2.0
    eat_cost = eat_result["avg_dw"] + α * eat_result["p_eaten"] * point(last_discard)
    take_cost = take_result["avg_dw"] + α * take_result["p_eaten"] * point(last_discard)

    eat_better = eat_cost < take_cost
    elapsed = time.time() - start_time
    print(f"[IS-MCTS] round={round_idx} last={card_name(last_discard)} "
          f"EAT: dw={eat_result['avg_dw']:.1f} p_eaten={eat_result['p_eaten']:.2f} "
          f"cost={eat_cost:.1f} (n={eat_result['n']}) | "
          f"TAKE: dw={take_result['avg_dw']:.1f} p_eaten={take_result['p_eaten']:.2f} "
          f"cost={take_cost:.1f} (n={take_result['n']}) → "
          f"{'EAT' if eat_better else 'TAKE'} ({elapsed:.2f}s)",
          flush=True)

    return eat_better
