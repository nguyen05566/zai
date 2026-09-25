#!/usr/bin/env python3
"""Phỏm 2-player self-play simulator with parameterized AI weights.

Used by selfplay_tune.py to optimize the weights of choose_discard_ai via
hill-climbing search. Does NOT connect to GameVH — purely local simulation.

Game rules (simplified standard Phỏm):
  * 52-card deck, 2 players, 9 cards each (rest in deck).
  * 4 rounds. Each round, each player: TAKE (deck) or EAT (top discard if
    it forms meld), then REMOVE (discard 1 card).
  * After 4 rounds, both DROP melds via best_meld_partition.
  * Móm (no meld) = auto-loss. Ù (0 deadwood with meld) = auto-win.
  * Otherwise winner = lower deadwood.
"""
from __future__ import annotations

import random
import sys
from functools import lru_cache
from itertools import combinations

# Import the rule engine from phom_bot.py
sys.path.insert(0, "/home/z/my-project/download")
from phom_bot import (
    valid_card, rank, suit, point, card_name,
    meld_masks, best_meld_partition, best_deadwood,
    can_cover_required_cards, can_form_meld_with,
)


# Default weights (matching v2 baseline).
DEFAULT_WEIGHTS = {
    "early":   {"deadwood": 1.0, "own_neighbour": 0.7, "safety": 0.4,
                "danger": 0.8, "melds_after": 1.2},
    "middle":  {"deadwood": 0.5, "own_neighbour": 0.5, "safety": 1.0,
                "danger": 1.5, "melds_after": 2.0},
    "final":   {"deadwood": 0.3, "own_neighbour": 0.3, "safety": 1.8,
                "danger": 2.5, "melds_after": 2.5},
}


# ---------- AI helpers (mirrors phom_bot.py but parameterized) ----------

def _seen_cards(public_state: dict, my_hand: list[int]) -> set[int]:
    seen: set[int] = set()
    for item in public_state.get("discarded_cards", []):
        seen.add(item["card"])
    for item in public_state.get("eaten_cards", []):
        seen.add(item["card"])
    for meld in public_state.get("exposed_melds", []):
        seen.update(meld["cards"])
    seen.update(c for c in my_hand if valid_card(c))
    return seen


def _meld_partners(card: int) -> set[int]:
    r = rank(card); s = suit(card)
    partners: set[int] = set()
    for other_suit in range(4):
        if other_suit != s:
            partners.add(other_suit * 13 + r)
    for delta in (-2, -1, 1, 2):
        nr = r + delta
        if 0 <= nr < 13:
            partners.add(s * 13 + nr)
    partners.discard(card)
    return partners


def _is_safe_discard(card: int, seen: set[int]) -> bool:
    r = rank(card); s = suit(card)
    unseen_same_rank = sum(
        1 for other_suit in range(4)
        if other_suit != s and (other_suit * 13 + r) not in seen
    )
    if unseen_same_rank >= 2:
        return False
    def unseen(delta: int) -> bool:
        nr = r + delta
        return 0 <= nr < 13 and (s * 13 + nr) not in seen
    if unseen(-1) and unseen(+1): return False
    if unseen(-2) and unseen(-1): return False
    if unseen(+1) and unseen(+2): return False
    return True


def _opponent_danger_score(card: int, public_state: dict, my_slot: int) -> int:
    partners = _meld_partners(card)
    exposed_by_slot: dict[int, set[int]] = {}
    for meld in public_state.get("exposed_melds", []):
        exposed_by_slot.setdefault(meld["slot"], set()).update(meld["cards"])
    danger_slots: set[int] = set()
    for item in public_state.get("eaten_cards", []):
        if item["to_slot"] == my_slot: continue
        if item["card"] not in partners: continue
        if item["card"] in exposed_by_slot.get(item["to_slot"], set()): continue
        danger_slots.add(item["to_slot"])
    safe_slots: set[int] = set()
    for item in public_state.get("skipped_eat_cards", []):
        if item["by_slot"] == my_slot: continue
        if item["card"] not in partners: continue
        safe_slots.add(item["by_slot"])
    return len(danger_slots - safe_slots)


def _own_neighbour_loss(remaining: list[int], card: int) -> int:
    r = rank(card); s = suit(card)
    run_neighbours = sum(1 for other in remaining
                         if suit(other) == s and abs(rank(other) - r) <= 2)
    set_neighbours = sum(1 for other in remaining if rank(other) == r) * 2
    return run_neighbours + set_neighbours


def choose_discard_weighted(
    hand: list[int],
    public_state: dict,
    my_slot: int,
    required_cards: list[int] | None = None,
    forbidden_cards: set[int] | None = None,
    round_idx: int = 0,
    is_final_round: bool = False,
    weights: dict = None,
) -> int | None:
    """Parameterized version of choose_discard_ai — accepts a weights dict
    with keys 'early'/'middle'/'final' each containing 5 weight values."""
    if weights is None:
        weights = DEFAULT_WEIGHTS

    concrete = [c for c in hand if valid_card(c)]
    required = list(required_cards or [])
    forbidden = set(forbidden_cards or [])
    if not concrete:
        return None

    seen = _seen_cards(public_state, concrete)

    if round_idx <= 1 and not is_final_round:
        phase = "early"
        mom_guard_soft = True
        mom_guard_hard = False
    elif round_idx == 2 and not is_final_round:
        phase = "middle"
        mom_guard_soft = True
        mom_guard_hard = True
    else:
        phase = "final"
        mom_guard_soft = True
        mom_guard_hard = not is_final_round

    w = weights[phase]
    raw_candidates: list[tuple] = []
    for index, card in enumerate(concrete):
        if card in forbidden:
            continue
        remaining = concrete[:index] + concrete[index + 1:]
        if not can_cover_required_cards(remaining, required):
            continue
        deadwood, groups = best_meld_partition([*remaining, *required])
        melds_after = len(groups)
        safe = _is_safe_discard(card, seen)
        danger = _opponent_danger_score(card, public_state, my_slot)
        own_loss = _own_neighbour_loss(remaining, card)
        if safe:
            safety_penalty = 0.0
        else:
            safety_penalty = 5.0 + danger * 5.0
        meld_bonus = melds_after * 1.5
        score = (
            w["deadwood"] * deadwood
            + w["own_neighbour"] * own_loss
            + w["safety"] * safety_penalty
            + w["danger"] * danger
            - w["melds_after"] * meld_bonus
            - 0.05 * point(card)
        )
        raw_candidates.append((score, deadwood, own_loss, safety_penalty,
                               danger, melds_after, point(card), card))

    if mom_guard_hard:
        filtered = [c for c in raw_candidates if c[5] > 0]
        if filtered:
            raw_candidates = filtered
    if mom_guard_soft:
        with_meld = [c for c in raw_candidates if c[5] > 0]
        if with_meld:
            raw_candidates = with_meld

    if not raw_candidates:
        # Fallback — just pick lowest-point non-forbidden card
        for card in sorted(concrete, key=lambda c: (point(c), c)):
            if card in forbidden:
                continue
            remaining = concrete[:concrete.index(card)] + concrete[concrete.index(card)+1:]
            if can_cover_required_cards(remaining, required):
                return card
        return None

    raw_candidates.sort(key=lambda item: item[0])
    return raw_candidates[0][7]


# ---------- Phỏm game simulator ----------

class PhomGame:
    """2-player Phỏm game state."""

    def __init__(self, weights_a: dict, weights_b: dict, seed: int | None = None):
        self.weights = [weights_a, weights_b]
        self.rng = random.Random(seed)
        deck = list(range(52))
        self.rng.shuffle(deck)
        self.hands: list[list[int]] = [deck[:9], deck[9:18]]
        self.deck: list[int] = deck[18:]
        # Start discard pile with one card
        self.discard_pile: list[int] = [self.deck.pop()]
        self.eaten: list[list[int]] = [[], []]
        self.discarded: list[list[int]] = [[], []]
        self.last_discard: int | None = self.discard_pile[-1]
        self.turn: int = 0
        self.max_discards = 4
        self.game_over = False

    def public_state(self, player: int) -> dict:
        return {
            "discarded_cards": [
                {"card": c, "by_slot": p, "sequence": i}
                for p, cards in enumerate(self.discarded)
                for i, c in enumerate(cards)
            ],
            "eaten_cards": [
                {"card": c, "from_slot": 1 - p, "to_slot": p, "sequence": i}
                for p, cards in enumerate(self.eaten)
                for i, c in enumerate(cards)
            ],
            "exposed_melds": [],
            "skipped_eat_cards": [],
        }

    def try_eat(self, player: int) -> bool:
        """Try to eat last_discard. Returns True if eaten."""
        if self.last_discard is None:
            return False
        if not can_form_meld_with(self.hands[player], self.last_discard):
            return False
        # Eat: card moves to hand AND is added to eaten list (required to cover)
        self.hands[player].append(self.last_discard)
        self.eaten[player].append(self.last_discard)
        self.discard_pile.pop()
        self.last_discard = self.discard_pile[-1] if self.discard_pile else None
        return True

    def take_from_deck(self, player: int) -> bool:
        if not self.deck:
            return False
        self.hands[player].append(self.deck.pop())
        return True

    def play_turn(self, player: int) -> bool:
        """One player's turn. Returns False if game should end."""
        if len(self.discarded[player]) >= self.max_discards:
            return True
        # Eat or take
        ate = self.try_eat(player)
        if not ate:
            if not self.take_from_deck(player):
                # Deck empty — must eat or pass
                if not self.try_eat(player):
                    return False  # cannot continue

        # Discard
        my_discard_count = len(self.discarded[player])
        is_final = (my_discard_count == self.max_discards - 1)
        chosen = choose_discard_weighted(
            self.hands[player],
            self.public_state(player),
            player,
            required_cards=list(self.eaten[player]),
            forbidden_cards=set(),
            round_idx=my_discard_count,
            is_final_round=is_final,
            weights=self.weights[player],
        )
        if chosen is None or chosen not in self.hands[player]:
            # Fallback: discard first card
            chosen = self.hands[player][0]
        self.hands[player].remove(chosen)
        self.discard_pile.append(chosen)
        self.discarded[player].append(chosen)
        self.last_discard = chosen

        # Check ù
        if self.check_u(player):
            self.game_over = True
            self.winner = player
            self.u_win = True
        return True

    def check_u(self, player: int) -> bool:
        hand = list(self.hands[player])
        required = list(self.eaten[player])
        if not required:
            return False
        if not can_cover_required_cards(hand, required):
            return False
        all_cards = list(dict.fromkeys([*hand, *required]))
        dw, groups = best_meld_partition(all_cards)
        return bool(groups) and dw == 0

    def play(self) -> dict:
        """Play full game. Returns result dict."""
        self.winner = -1
        self.u_win = False
        # Alternate turns until both have max_discards or game over
        while not self.game_over:
            for player in range(2):
                if self.game_over:
                    break
                if len(self.discarded[player]) >= self.max_discards:
                    continue
                if not self.play_turn(player):
                    self.game_over = True
                    break
            if all(len(self.discarded[p]) >= self.max_discards for p in range(2)):
                self.game_over = True

        # Score
        if self.u_win:
            return {"winner": self.winner, "u_win": True, "dw": [0, 0]}

        dws = [0, 0]
        moms = [False, False]
        for p in range(2):
            hand = list(self.hands[p])
            required = list(self.eaten[p])
            if required and not can_cover_required_cards(hand, required):
                dws[p] = sum(point(c) for c in hand)
                moms[p] = True
                continue
            all_cards = list(dict.fromkeys([*hand, *required]))
            dw, groups = best_meld_partition(all_cards)
            if not groups:
                moms[p] = True
                dws[p] = sum(point(c) for c in hand)
            else:
                dws[p] = dw

        if moms[0] and moms[1]:
            winner = 0 if dws[0] < dws[1] else 1
        elif moms[0]:
            winner = 1
        elif moms[1]:
            winner = 0
        else:
            winner = 0 if dws[0] < dws[1] else 1
        return {"winner": winner, "u_win": False, "dw": dws, "moms": moms}


def play_one_game(weights_a: dict, weights_b: dict, seed: int) -> dict:
    game = PhomGame(weights_a, weights_b, seed=seed)
    return game.play()


# ---------- 4-player Phỏm simulator ----------

class PhomGame4P:
    """4-player Phỏm game state. Closer to real GameVH Phỏm.

    Layout: 1 candidate (slot 0) + 3 baseline bots (slots 1, 2, 3).
    Each round, players take turns in slot order. After 4 rounds, all DROP.
    Winner = lowest deadwood. Móm = auto-bottom. Ù = auto-top.
    """

    def __init__(self, weights_list: list[dict], seed: int | None = None):
        assert len(weights_list) == 4
        self.weights = weights_list
        self.rng = random.Random(seed)
        deck = list(range(52))
        self.rng.shuffle(deck)
        self.hands: list[list[int]] = [deck[i*9:(i+1)*9] for i in range(4)]
        self.deck: list[int] = deck[36:]  # 16 cards left in deck
        # Start discard pile with one card
        self.discard_pile: list[int] = [self.deck.pop()]
        self.eaten: list[list[int]] = [[] for _ in range(4)]
        self.discarded: list[list[int]] = [[] for _ in range(4)]
        self.last_discard: int | None = self.discard_pile[-1]
        self.max_discards = 4
        self.game_over = False
        self.u_winner: int | None = None

    def public_state(self, player: int) -> dict:
        return {
            "discarded_cards": [
                {"card": c, "by_slot": p, "sequence": i}
                for p, cards in enumerate(self.discarded)
                for i, c in enumerate(cards)
            ],
            "eaten_cards": [
                {"card": c, "from_slot": src, "to_slot": p, "sequence": i}
                for p, cards in enumerate(self.eaten)
                for i, c in enumerate(cards)
                # find who discarded it (the previous discarder of that card)
                for src in self._find_discarder(c, p)
            ],
            "exposed_melds": [],
            "skipped_eat_cards": [],
        }

    def _find_discarder(self, card: int, eater: int) -> list[int]:
        """Find who discarded the card that eater ate. Returns [from_slot] or []."""
        for p, cards in enumerate(self.discarded):
            if p == eater:
                continue
            if card in cards:
                return [p]
        return []

    def try_eat(self, player: int) -> bool:
        """Try to eat last_discard. Returns True if eaten."""
        if self.last_discard is None:
            return False
        if not can_form_meld_with(self.hands[player], self.last_discard):
            return False
        self.hands[player].append(self.last_discard)
        self.eaten[player].append(self.last_discard)
        self.discard_pile.pop()
        self.last_discard = self.discard_pile[-1] if self.discard_pile else None
        return True

    def take_from_deck(self, player: int) -> bool:
        if not self.deck:
            return False
        self.hands[player].append(self.deck.pop())
        return True

    def play_turn(self, player: int) -> bool:
        if len(self.discarded[player]) >= self.max_discards:
            return True
        ate = self.try_eat(player)
        if not ate:
            if not self.take_from_deck(player):
                if not self.try_eat(player):
                    return False
        my_discard_count = len(self.discarded[player])
        is_final = (my_discard_count == self.max_discards - 1)
        chosen = choose_discard_weighted(
            self.hands[player],
            self.public_state(player),
            player,
            required_cards=list(self.eaten[player]),
            forbidden_cards=set(),
            round_idx=my_discard_count,
            is_final_round=is_final,
            weights=self.weights[player],
        )
        if chosen is None or chosen not in self.hands[player]:
            chosen = self.hands[player][0]
        self.hands[player].remove(chosen)
        self.discard_pile.append(chosen)
        self.discarded[player].append(chosen)
        self.last_discard = chosen
        # Check ù
        if self.check_u(player):
            self.game_over = True
            self.u_winner = player
        return True

    def check_u(self, player: int) -> bool:
        hand = list(self.hands[player])
        required = list(self.eaten[player])
        if not required:
            return False
        if not can_cover_required_cards(hand, required):
            return False
        all_cards = list(dict.fromkeys([*hand, *required]))
        dw, groups = best_meld_partition(all_cards)
        return bool(groups) and dw == 0

    def play(self) -> dict:
        """Play full 4-player game. Returns result dict with rankings."""
        while not self.game_over:
            for player in range(4):
                if self.game_over:
                    break
                if len(self.discarded[player]) >= self.max_discards:
                    continue
                if not self.play_turn(player):
                    self.game_over = True
                    break
            if all(len(self.discarded[p]) >= self.max_discards for p in range(4)):
                self.game_over = True

        if self.u_winner is not None:
            # ù winner = rank 0, others ranked by deadwood
            dws = [0] * 4
            moms = [False] * 4
            for p in range(4):
                if p == self.u_winner:
                    dws[p] = 0
                    continue
                hand = list(self.hands[p])
                required = list(self.eaten[p])
                if required and not can_cover_required_cards(hand, required):
                    dws[p] = sum(point(c) for c in hand)
                    moms[p] = True
                    continue
                all_cards = list(dict.fromkeys([*hand, *required]))
                dw, groups = best_meld_partition(all_cards)
                if not groups:
                    moms[p] = True
                    dws[p] = sum(point(c) for c in hand)
                else:
                    dws[p] = dw
            # Rank: u_winner=0, then sort others by (mom, dw)
            others = [p for p in range(4) if p != self.u_winner]
            others.sort(key=lambda p: (moms[p], dws[p]))
            ranking = [self.u_winner] + others
            return {
                "u_win": True, "u_winner": self.u_winner,
                "dws": dws, "moms": moms, "ranking": ranking,
            }

        # No ù — score everyone
        dws = [0] * 4
        moms = [False] * 4
        for p in range(4):
            hand = list(self.hands[p])
            required = list(self.eaten[p])
            if required and not can_cover_required_cards(hand, required):
                dws[p] = sum(point(c) for c in hand)
                moms[p] = True
                continue
            all_cards = list(dict.fromkeys([*hand, *required]))
            dw, groups = best_meld_partition(all_cards)
            if not groups:
                moms[p] = True
                dws[p] = sum(point(c) for c in hand)
            else:
                dws[p] = dw
        # Rank: non-mom first by dw, then mom by dw
        ranking = sorted(range(4), key=lambda p: (moms[p], dws[p]))
        return {
            "u_win": False, "u_winner": None,
            "dws": dws, "moms": moms, "ranking": ranking,
        }


def play_one_game_4p(candidate: dict, baseline: dict, seed: int,
                     candidate_slot: int = 0) -> dict:
    """Play a 4-player game with 1 candidate + 3 baselines.
    Returns dict with candidate's rank (0=winner, 3=bottom) and dw.
    """
    weights_list = [baseline] * 4
    weights_list[candidate_slot] = candidate
    game = PhomGame4P(weights_list, seed=seed)
    result = game.play()
    cand_rank = result["ranking"].index(candidate_slot)
    return {
        "rank": cand_rank,  # 0 = win, 1 = 2nd, 2 = 3rd, 3 = bottom
        "dw": result["dws"][candidate_slot],
        "mom": result["moms"][candidate_slot],
        "u_win": result["u_win"] and result["u_winner"] == candidate_slot,
    }


# ---------- Worker function for multiprocessing ----------

def evaluate_weights_worker(args) -> dict:
    """Play n_games between candidate weights and baseline, return win stats.
    args = (candidate_weights, baseline_weights, n_games, base_seed, candidate_side)
    candidate_side: 0 = candidate plays first, 1 = candidate plays second
    """
    candidate, baseline, n_games, base_seed, side = args
    wins = 0
    losses = 0
    draws = 0
    u_wins = 0
    dw_total = 0
    for i in range(n_games):
        seed = base_seed + i
        # Alternate sides to remove first-mover advantage
        if (i % 2 == 0) == (side == 0):
            wa, wb = candidate, baseline
            cand_slot = 0
        else:
            wa, wb = baseline, candidate
            cand_slot = 1
        result = play_one_game(wa, wb, seed)
        if result["winner"] == cand_slot:
            wins += 1
            if result.get("u_win"):
                u_wins += 1
        elif result["winner"] == 1 - cand_slot:
            losses += 1
        else:
            draws += 1
        dw_total += result["dw"][cand_slot]
    return {
        "wins": wins, "losses": losses, "draws": draws,
        "u_wins": u_wins, "n_games": n_games,
        "win_rate": wins / n_games if n_games else 0.0,
        "avg_dw": dw_total / n_games if n_games else 0.0,
    }


def evaluate_weights_4p_worker(args) -> dict:
    """4-player evaluation. 1 candidate vs 3 baselines.
    Returns win stats where 'win' = candidate ranks #1.
    Also tracks top-2 (rank 0 or 1) and avg rank/dw.
    args = (candidate_weights, baseline_weights, n_games, base_seed)
    """
    candidate, baseline, n_games, base_seed = args
    wins = 0  # rank 0
    top2 = 0  # rank 0 or 1
    bottom = 0  # rank 3
    u_wins = 0
    dw_total = 0
    rank_sum = 0
    for i in range(n_games):
        seed = base_seed + i
        # Rotate candidate's seat to remove first-mover bias
        cand_slot = i % 4
        result = play_one_game_4p(candidate, baseline, seed, cand_slot)
        rank_sum += result["rank"]
        dw_total += result["dw"]
        if result["rank"] == 0:
            wins += 1
            if result["u_win"]:
                u_wins += 1
        if result["rank"] <= 1:
            top2 += 1
        if result["rank"] == 3:
            bottom += 1
    return {
        "wins": wins, "top2": top2, "bottom": bottom,
        "u_wins": u_wins, "n_games": n_games,
        "win_rate": wins / n_games if n_games else 0.0,
        "top2_rate": top2 / n_games if n_games else 0.0,
        "bottom_rate": bottom / n_games if n_games else 0.0,
        "avg_rank": rank_sum / n_games if n_games else 0.0,
        "avg_dw": dw_total / n_games if n_games else 0.0,
    }


if __name__ == "__main__":
    # Smoke test 2-player
    result = evaluate_weights_worker(
        (DEFAULT_WEIGHTS, DEFAULT_WEIGHTS, 100, 42, 0)
    )
    print("2P baseline vs baseline (100 games):")
    print(f"  wins={result['wins']} losses={result['losses']} draws={result['draws']}")
    print(f"  win_rate={result['win_rate']:.2%} avg_dw={result['avg_dw']:.1f}")
    print(f"  u_wins={result['u_wins']}")

    # Smoke test 4-player
    result4 = evaluate_weights_4p_worker(
        (DEFAULT_WEIGHTS, DEFAULT_WEIGHTS, 100, 42)
    )
    print("\n4P baseline vs baseline (100 games):")
    print(f"  wins(rank 0)={result4['wins']} top2={result4['top2']} "
          f"bottom={result4['bottom']}")
    print(f"  win_rate={result4['win_rate']:.2%} top2_rate={result4['top2_rate']:.2%}")
    print(f"  avg_rank={result4['avg_rank']:.2f} avg_dw={result4['avg_dw']:.1f}")
    print(f"  u_wins={result4['u_wins']}")
    # Baseline should be ~25% win rate (4 players equal)
