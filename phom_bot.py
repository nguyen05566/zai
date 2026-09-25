#!/usr/bin/env python3
"""Merged GameVH Phỏm WebSocket bot with upgraded AI engine.

Combines the previous phom_logic.py (card primitives + meld evaluator) and
phom_table_bot1.py (HTTP login + WebSocket client + state machine) into a
single self-contained module, and replaces the original deadwood-only
choose_discard with a phase-aware AI engine that:

  1. Safe-card assessment — uses the full public state (discarded_cards,
     eaten_cards, exposed_melds, my hand) to compute which unseen cards
     could complete a meld with each candidate discard.
  2. Phase-aware tactics — early rounds (1-2) prioritise shedding high
     deadwood; late rounds (3-4, "vòng chốt") prioritise 100% safe cards
     and avoid feeding opponents.
  3. Opponent meld inference — penalises discards whose meld partners
     overlap with cards opponents have eaten but not yet exposed.
  4. Móm avoidance — in late rounds, refuses discards that would leave
     the hand without any meld.
  5. Preserves guardrails — every eaten card must remain coverable by a
     disjoint meld after the discard (luật 36-37-38), and the bot never
     retries a discard the server already rejected.

Run:
    GAMEVH_USER=nguyen9 GAMEVH_PASSWORD=nhat123456 PHOM_PLAY=1 \\
        PHOM_BET_XU=100 PHOM_MAX_GAMES=3 python3 phom_bot.py
"""
from __future__ import annotations

import atexit
import http.cookiejar
import json
import os
import re
import signal
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request
from functools import lru_cache
from itertools import combinations
from pathlib import Path

import websocket

# Optional PIMC engine — loaded lazily only when PHOM_PIMC=1
_PIMC_ENGINE = None
def _get_pimc():
    global _PIMC_ENGINE
    if _PIMC_ENGINE is None:
        try:
            import pimc_engine
            _PIMC_ENGINE = pimc_engine
        except ImportError:
            _PIMC_ENGINE = False
    return _PIMC_ENGINE if _PIMC_ENGINE else None


# Optional IS-MCTS engine — loaded lazily only when PHOM_ISMCTS=1
_ISMCTS_ENGINE = None
def _get_ismcts():
    global _ISMCTS_ENGINE
    if _ISMCTS_ENGINE is None:
        try:
            import ismcts_engine
            _ISMCTS_ENGINE = ismcts_engine
        except ImportError:
            _ISMCTS_ENGINE = False
    return _ISMCTS_ENGINE if _ISMCTS_ENGINE else None

# ===========================================================================
# Section A — Card primitives
# ===========================================================================

RANK_NAMES = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUIT_NAMES = ("S0", "S1", "S2", "S3")


def valid_card(card: int) -> bool:
    return isinstance(card, int) and 0 <= card < 52


def rank(card: int) -> int:
    return card % 13


def suit(card: int) -> int:
    return card // 13


def point(card: int) -> int:
    return rank(card) + 1


def card_name(card: int) -> str:
    if not valid_card(card):
        return "??"
    return f"{RANK_NAMES[rank(card)]}-{SUIT_NAMES[suit(card)]}({card})"


# ===========================================================================
# Section B — Meld evaluator (kept verbatim from phom_logic.py)
# ===========================================================================

def meld_masks(cards: list[int]) -> list[int]:
    """Return every set/run mask available in this concrete card list."""
    masks: set[int] = set()
    by_rank: dict[int, list[int]] = {}
    by_suit: dict[int, list[tuple[int, int]]] = {}
    for index, card in enumerate(cards):
        if not valid_card(card):
            continue
        by_rank.setdefault(rank(card), []).append(index)
        by_suit.setdefault(suit(card), []).append((rank(card), index))

    for indices in by_rank.values():
        if len(indices) >= 3:
            full = sum(1 << index for index in indices)
            masks.add(full)
            if len(indices) == 4:
                for omitted in indices:
                    masks.add(full & ~(1 << omitted))

    for values in by_suit.values():
        values.sort()
        for start in range(len(values)):
            for end in range(start + 3, len(values) + 1):
                segment = values[start:end]
                ranks = [item[0] for item in segment]
                if all(ranks[i] + 1 == ranks[i + 1] for i in range(len(ranks) - 1)):
                    masks.add(sum(1 << item[1] for item in segment))
                elif ranks[-1] - ranks[0] >= len(ranks):
                    break
    return sorted(masks)


def best_meld_partition(cards: list[int]) -> tuple[int, list[list[int]]]:
    """Return minimum deadwood points and one optimal disjoint meld partition."""
    concrete = [card for card in cards if valid_card(card)]
    cards = concrete
    masks = meld_masks(cards)
    mask_points = {
        mask: sum(point(cards[i]) for i in range(len(cards)) if mask & (1 << i))
        for mask in masks
    }

    @lru_cache(maxsize=None)
    def saved(used: int) -> tuple[int, tuple[int, ...]]:
        best_value: int = 0
        best_partition: tuple[int, ...] = ()
        for mask in masks:
            if used & mask:
                continue
            child_value, child_partition = saved(used | mask)
            candidate_value = mask_points[mask] + child_value
            candidate_partition = (mask, *child_partition)
            candidate_covered = sum(item.bit_count() for item in candidate_partition)
            best_covered = sum(item.bit_count() for item in best_partition)
            if ((candidate_value, candidate_covered, -len(candidate_partition))
                    > (best_value, best_covered, -len(best_partition))):
                best_value = candidate_value
                best_partition = candidate_partition
        return best_value, best_partition

    saved_points, partition_masks = saved(0)
    groups = [
        [cards[i] for i in range(len(cards)) if mask & (1 << i)]
        for mask in partition_masks
    ]
    return sum(point(card) for card in cards) - saved_points, groups


def best_deadwood(cards: list[int]) -> tuple[int, set[int]]:
    """Return minimum deadwood points and indices covered by disjoint melds."""
    concrete = [card for card in cards if valid_card(card)]
    deadwood, groups = best_meld_partition(concrete)
    remaining = list(enumerate(concrete))
    covered: set[int] = set()
    for group in groups:
        for card in group:
            for index, candidate in remaining:
                if index not in covered and candidate == card:
                    covered.add(index)
                    break
    return deadwood, covered


def can_cover_required_cards(cards: list[int], required_cards: list[int]) -> bool:
    """Whether disjoint melds can cover every mandatory (eaten) card."""
    concrete = [card for card in cards if valid_card(card)]
    required = [card for card in required_cards if valid_card(card)]
    if not required:
        return True
    combined = list(dict.fromkeys([*concrete, *required]))
    required_mask = 0
    for card in required:
        if card not in combined:
            return False
        required_mask |= 1 << combined.index(card)
    masks = meld_masks(combined)

    @lru_cache(maxsize=None)
    def search(used: int) -> bool:
        if used & required_mask == required_mask:
            return True
        return any(not (used & mask) and search(used | mask) for mask in masks)

    return search(0)


def can_form_meld_with(cards: list[int], offered: int | None) -> bool:
    if offered is None or not valid_card(offered):
        return False
    combined = [card for card in cards if valid_card(card)] + [offered]
    offered_index = len(combined) - 1
    return any(mask & (1 << offered_index) for mask in meld_masks(combined))


def is_meld(cards: list[int]) -> bool:
    cards = [card for card in cards if valid_card(card)]
    if len(cards) < 3 or len(set(cards)) != len(cards):
        return False
    ranks = [rank(card) for card in cards]
    suits = [suit(card) for card in cards]
    is_set = len(cards) <= 4 and len(set(ranks)) == 1
    ordered = sorted(ranks)
    is_run = (len(set(suits)) == 1
              and all(ordered[i] + 1 == ordered[i + 1]
                      for i in range(len(ordered) - 1)))
    return is_set or is_run


def can_extend_public_meld(public_cards: list[int], card: int) -> bool:
    """Whether card can legally extend at least one exposed meld."""
    concrete = list(dict.fromkeys(
        value for value in public_cards if valid_card(value) and value != card
    ))
    for size in range(3, len(concrete) + 1):
        for subset in combinations(concrete, size):
            if is_meld(list(subset)) and is_meld([*subset, card]):
                return True
    return False


def choose_send_card(hand: list[int], exposed_lines: list[list[int]]) -> int | None:
    candidates = [
        card for card in hand if valid_card(card)
        and any(can_extend_public_meld(line, card) for line in exposed_lines)
    ]
    return max(candidates, key=lambda card: (point(card), card), default=None)


# ===========================================================================
# Section C — AI engine (replaces the old deadwood-only choose_discard)
# ===========================================================================

# Default weights — used when weights.json is not present or fails to load.
# These are the 2P-tuned weights that achieved 9/10 win rate on live server.
_DEFAULT_WEIGHTS = {
    "early": {
        "deadwood": 1.9927, "own_neighbour": 1.0324, "safety": 0.4629,
        "danger": 1.0211, "melds_after": 0.8178,
    },
    "middle": {
        "deadwood": 0.4185, "own_neighbour": 0.5036, "safety": 1.3825,
        "danger": 1.2678, "melds_after": 1.6607,
    },
    "final": {
        "deadwood": 0.1819, "own_neighbour": 0.434, "safety": 1.5181,
        "danger": 4.6453, "melds_after": 4.4736,
    },
}

# Search path for weights.json — co-located with this script first, then CWD.
_WEIGHTS_PATHS = [
    Path(__file__).resolve().parent / "weights.json",
    Path.cwd() / "weights.json",
]
_LOAD_WEIGHTS_CACHE: dict | None = None
_LOAD_WEIGHTS_MTIME: float | None = None


def _LOAD_WEIGHTS() -> dict:
    """Load weights.json (auto-tuned by self-play workflow) with caching.
    Reloads automatically if the file mtime changes — useful for hot-reload
    after a tuning run commits new weights.
    """
    global _LOAD_WEIGHTS_CACHE, _LOAD_WEIGHTS_MTIME
    for path in _WEIGHTS_PATHS:
        if not path.exists():
            continue
        try:
            mtime = path.stat().st_mtime
            if _LOAD_WEIGHTS_CACHE is not None and mtime == _LOAD_WEIGHTS_MTIME:
                return _LOAD_WEIGHTS_CACHE
            with path.open() as f:
                data = json.load(f)
            # Accept either {"weights": {...}} (tuner output) or {...} directly
            w = data.get("weights", data) if isinstance(data, dict) else None
            if not isinstance(w, dict):
                continue
            # Validate structure
            ok = all(
                phase in w and isinstance(w[phase], dict)
                and all(k in w[phase] for k in
                        ("deadwood", "own_neighbour", "safety",
                         "danger", "melds_after"))
                for phase in ("early", "middle", "final")
            )
            if not ok:
                continue
            _LOAD_WEIGHTS_CACHE = w
            _LOAD_WEIGHTS_MTIME = mtime
            return w
        except (json.JSONDecodeError, OSError):
            continue
    return _DEFAULT_WEIGHTS


def _seen_cards(public_state: dict, my_hand: list[int]) -> set[int]:
    """Every card whose location is publicly known."""
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
    """All cards (other than `card` itself) that could form a meld with it."""
    r = rank(card)
    s = suit(card)
    partners: set[int] = set()
    # Set-completion: same rank in the other three suits
    for other_suit in range(4):
        if other_suit == s:
            continue
        partners.add(other_suit * 13 + r)
    # Run-completion: ranks ±1, ±2 in the same suit
    for delta in (-2, -1, 1, 2):
        nr = r + delta
        if 0 <= nr < 13:
            partners.add(s * 13 + nr)
    partners.discard(card)
    return partners


def _is_safe_discard(card: int, seen: set[int]) -> bool:
    """A discard is 100% safe if no pair of unseen cards can form a meld with it."""
    r = rank(card)
    s = suit(card)
    # Set: need >= 2 unseen cards of the same rank in different suits
    unseen_same_rank = sum(
        1 for other_suit in range(4)
        if other_suit != s and (other_suit * 13 + r) not in seen
    )
    if unseen_same_rank >= 2:
        return False
    # Run: any of three patterns of two consecutive unseen neighbours
    def unseen(delta: int) -> bool:
        nr = r + delta
        return 0 <= nr < 13 and (s * 13 + nr) not in seen

    if unseen(-1) and unseen(+1):
        return False
    if unseen(-2) and unseen(-1):
        return False
    if unseen(+1) and unseen(+2):
        return False
    return True


def _opponent_danger_score(card: int, public_state: dict, my_slot: int) -> int:
    """Net opponent danger for discarding ``card``.

    Positive signals (raise danger):
      * Opponent has eaten a meld-partner of ``card`` and not yet exposed it.
    Negative signals (lower danger):
      * Opponent has *skipped* (drawn past) a meld-partner of ``card`` — they
        revealed they don't need that meld family.

    Returns a signed int. Caller treats >0 as danger; the magnitude scales
    the penalty.
    """
    partners = _meld_partners(card)
    exposed_by_slot: dict[int, set[int]] = {}
    for meld in public_state.get("exposed_melds", []):
        exposed_by_slot.setdefault(meld["slot"], set()).update(meld["cards"])

    danger_slots: set[int] = set()
    for item in public_state.get("eaten_cards", []):
        if item["to_slot"] == my_slot:
            continue
        if item["card"] not in partners:
            continue
        if item["card"] in exposed_by_slot.get(item["to_slot"], set()):
            continue  # already laid publicly — no longer in their hand
        danger_slots.add(item["to_slot"])

    safe_slots: set[int] = set()
    for item in public_state.get("skipped_eat_cards", []):
        if item["by_slot"] == my_slot:
            continue
        if item["card"] not in partners:
            continue
        # Only count as "safe signal" if they haven't subsequently eaten
        # the same card family (covered above by removal logic on eat).
        safe_slots.add(item["by_slot"])

    # A slot that has both eaten AND skipped partners is ambiguous — treat as
    # neutral (cancel out). Net danger = only slots in danger but not in safe.
    net_danger = danger_slots - safe_slots
    return len(net_danger)


def _own_neighbour_loss(remaining: list[int], card: int) -> int:
    """How many of our own meld-building neighbours would we lose by discarding
    `card`. Higher = worse (we're breaking a potential meld)."""
    r = rank(card)
    s = suit(card)
    run_neighbours = sum(
        1 for other in remaining
        if suit(other) == s and abs(rank(other) - r) <= 2
    )
    set_neighbours = sum(1 for other in remaining if rank(other) == r) * 2
    return run_neighbours + set_neighbours


def choose_discard_basic(cards: list[int], required_cards: list[int] | None = None,
                         forbidden_cards: set[int] | None = None) -> int | None:
    """The original deadwood-only heuristic, retained as a fallback."""
    concrete = [card for card in cards if valid_card(card)]
    required = required_cards or []
    forbidden = forbidden_cards or set()
    if not concrete:
        return None
    candidates = []
    for index, card in enumerate(concrete):
        if card in forbidden:
            continue
        remaining = concrete[:index] + concrete[index + 1:]
        if not can_cover_required_cards(remaining, required):
            continue
        deadwood, _groups = best_meld_partition([*remaining, *required])
        neighbor_count = sum(
            1 for other in remaining
            if suit(other) == suit(card) and abs(rank(other) - rank(card)) <= 2
        )
        same_rank = sum(1 for other in remaining if rank(other) == rank(card))
        candidates.append((deadwood, neighbor_count + same_rank * 2, -point(card), card))
    return min(candidates)[3] if candidates else None


def choose_discard_ai(
    hand: list[int],
    public_state: dict,
    my_slot: int,
    required_cards: list[int] | None = None,
    forbidden_cards: set[int] | None = None,
    round_idx: int = 0,
    is_final_round: bool = False,
) -> int | None:
    """Phase-aware discard chooser.

    ``hand``           — cards currently in our hand (line 0).
    ``public_state``   — snapshot from PhomTableBot.public_state_snapshot().
    ``my_slot``        — our seat index.
    ``required_cards`` — cards we previously ate; every one must remain
                         coverable by a meld after the discard.
    ``forbidden_cards``— discards the server already rejected.
    ``round_idx``      — 0-indexed count of discards we've already made
                         this match (0 = first discard, 3 = last discard).
    ``is_final_round`` — True if we're in the "finalRemove" state, i.e.
                         the very last discard (vòng chốt).
    """
    concrete = [c for c in hand if valid_card(c)]
    required = list(required_cards or [])
    forbidden = set(forbidden_cards or [])
    if not concrete:
        return None

    seen = _seen_cards(public_state, concrete)

    # Phase weights — load from weights.json if available (auto-tuned by
    # self-play workflow), otherwise fall back to 2P-tuned defaults below.
    weights = _LOAD_WEIGHTS()

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
        mom_guard_hard = not is_final_round  # final round: allow móm

    w = weights[phase]
    w_deadwood = w["deadwood"]
    w_own_neighbour = w["own_neighbour"]
    w_safety = w["safety"]
    w_danger = w["danger"]
    w_melds_after = w["melds_after"]

    candidates: list[tuple] = []
    # First pass: collect all candidates. We'll apply the soft móm guard as
    # a re-filtering step after seeing whether any meld-preserving option
    # actually exists.
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
            w_deadwood * deadwood
            + w_own_neighbour * own_loss
            + w_safety * safety_penalty
            + w_danger * danger
            - w_melds_after * meld_bonus
            - 0.05 * point(card)
        )
        raw_candidates.append((
            score, deadwood, own_loss, safety_penalty, danger,
            melds_after, point(card), card,
        ))

    # Hard móm guard: drop candidates that would zero out melds entirely.
    if mom_guard_hard:
        filtered = [c for c in raw_candidates if c[5] > 0]
        if filtered:
            raw_candidates = filtered

    # Soft móm guard: prefer keeping >=1 meld, but fall back if the only
    # alternative is something the server already rejected or no candidate
    # remains.
    if mom_guard_soft:
        with_meld = [c for c in raw_candidates if c[5] > 0]
        if with_meld:
            raw_candidates = with_meld

    candidates = raw_candidates

    if not candidates:
        # Fallback to the basic heuristic if our AI pruned everything
        # (e.g. every discard triggers the móm guard).
        return choose_discard_basic(concrete, required, forbidden)

    candidates.sort(key=lambda item: item[0])
    best = candidates[0]
    n_skips = len(public_state.get("skipped_eat_cards", []))
    print(
        f"[AI] round_idx={round_idx} final={is_final_round} "
        f"chose={card_name(best[7])} score={best[0]:.2f} "
        f"deadwood={best[1]} own_loss={best[2]} safety={best[3]:.1f} "
        f"danger={best[4]} melds_after={best[5]} pt={best[6]} "
        f"skips_seen={n_skips} "
        f"(of {len(candidates)} candidates)",
        flush=True,
    )
    return best[7]


# ===========================================================================
# Section D — PhomTableBot (WS client + state machine)
# ===========================================================================

LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = os.environ.get("GAMEVH_PHOM_URL", "https://gamevh.net/play/0/1")
WS_URL = "wss://gamevh.net/ws/gameServer"
TARGET_BET = int(os.environ.get("PHOM_BET_XU", "100"))
PLAY_ENABLED = os.environ.get("PHOM_PLAY", "0") == "1"
MAX_GAMES = max(1, int(os.environ.get("PHOM_MAX_GAMES", "1")))
HOLD_SECONDS = int(os.environ.get("PHOM_HOLD_SECONDS", "10"))
ACTION_DELAY = float(os.environ.get("PHOM_ACTION_DELAY", "0.8"))
CAPTURE_DIR = Path(os.environ.get("PHOM_CAPTURE_DIR", "ws_capture/phom"))

CMD_NAMES = {
    300: "PONG", 301: "PING", 302: "LOGIN", 303: "ALERT",
    311: "BROADCAST", 314: "SET_CLIENT_MODE", 315: "CONFIG",
    331: "CHAT.SEND", 335: "CHAT.MSG",
    401: "ENTER_PLACE", 405: "CREATE_RULE", 406: "PLAYER_ENTERED",
    407: "PLAYER_EXITED", 408: "QUICK_PLAY", 410: "KICK_PLAYER",
    412: "LIST_ZONE_ROOM", 413: "LIST_BET_AMT", 414: "GET_TABLE_DATA",
    416: "SLOT_IN_TABLE_CHANGED", 417: "START_MATCH", 418: "GAMEOVER",
    419: "ENTER_STATE", 420: "SET_TURN", 421: "SET_PLAYER_STATUS",
    422: "SET_PLAYER_POINT", 423: "SET_PLAYER_ATTR", 433: "GET_TABLE_DATA_EX",
    434: "SET_READY", 501: "BET", 502: "PLAY", 518: "HIGHLIGHT",
    521: "TAKE_CARD", 522: "SHOW_PLAYER_CARD", 523: "CLEAR_CARDS",
    524: "SET_CARDS", 525: "SELECT_CARDS", 527: "COMPARE_BAND",
    528: "SEND_CARD", 529: "MOVE", 530: "CHANGE_PIECE",
    531: "SET_REMAIN_TURN", 532: "ADD_LOG", 533: "ASK_DRAW",
    534: "SURRENDER", 535: "RETREAT", 536: "ACCEPT", 537: "HIT",
    538: "STAY", 539: "FIRE_CARD", 540: "PASS_TURN", 541: "SORT_CARD",
    542: "TAKE", 543: "EAT", 544: "REMOVE", 545: "DROP_BAND",
    546: "SELECT_BAND", 547: "DROP_AVAILABLE_BAND", 548: "HINT",
    549: "SUBMIT", 601: "LOGIN_EX",
}
CMD_IDS = {name: code for code, name in CMD_NAMES.items()}


def utc_record(**fields):
    return {"timestamp": time.time(), **fields}


def append_jsonl(name: str, record: dict) -> None:
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = CAPTURE_DIR / name
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


class Codec:
    @staticmethod
    def pack_command(command: str | int, payload: bytes = b"") -> bytes:
        out = bytearray()
        if isinstance(command, str) and command in CMD_IDS:
            out.extend(struct.pack(">H", CMD_IDS[command]))
        elif isinstance(command, str):
            raw = command.encode("ascii")
            out.append((-len(raw)) & 0xFF)
            out.extend(raw)
        else:
            out.extend(struct.pack(">H", command))
        out.extend(payload)
        return bytes(out)

    @staticmethod
    def byte(value: int) -> bytes:
        return struct.pack(">b", value)

    @staticmethod
    def integer(value: int) -> bytes:
        return struct.pack(">i", value)

    @staticmethod
    def ascii(value: str) -> bytes:
        raw = value.encode("ascii")[:255]
        return struct.pack(">B", len(raw)) + raw

    @staticmethod
    def string(value: str) -> bytes:
        raw = value.encode("utf-16-be")
        return struct.pack(">h", len(raw) // 2) + raw

    @staticmethod
    def byte_array(values: list[int]) -> bytes:
        return struct.pack(">h", len(values)) + bytes(value & 0xFF for value in values)


class Incoming:
    def __init__(self, data: bytes):
        self.data = bytes(data)
        self.offset = 0
        self.command = self._command()

    def _command(self) -> str:
        first = self.byte()
        if first < 0:
            size = -first
            value = self.data[self.offset:self.offset + size].decode("ascii", "replace")
            self.offset += size
            return value
        second = self.data[self.offset]
        self.offset += 1
        code = ((first & 0xFF) << 8) | second
        return CMD_NAMES.get(code, str(code))

    def byte(self) -> int:
        value = struct.unpack_from(">b", self.data, self.offset)[0]
        self.offset += 1
        return value

    def short(self) -> int:
        value = struct.unpack_from(">h", self.data, self.offset)[0]
        self.offset += 2
        return value

    def integer(self) -> int:
        value = struct.unpack_from(">i", self.data, self.offset)[0]
        self.offset += 4
        return value

    def long(self) -> int:
        value = struct.unpack_from(">q", self.data, self.offset)[0]
        self.offset += 8
        return value

    def ascii(self) -> str:
        size = self.byte()
        if size < 0:
            size += 256
        value = self.data[self.offset:self.offset + size].decode("ascii", "replace")
        self.offset += size
        return value

    def string(self) -> str:
        chars = self.short()
        value = self.data[self.offset:self.offset + chars * 2].decode("utf-16-be", "replace")
        self.offset += chars * 2
        return value

    def byte_array(self) -> list[int]:
        size = self.short()
        if size < 0:
            raise ValueError(f"negative byte-array size: {size}")
        end = self.offset + size
        if end > len(self.data):
            raise ValueError("truncated byte array")
        value = list(self.data[self.offset:end])
        self.offset = end
        return value

    def remaining(self) -> int:
        return len(self.data) - self.offset


class PhomTableBot:
    def __init__(self, username: str, password: str, target_bet: int):
        self.username = username
        self.password = password
        self.target_bet = target_bet
        self.nickname = ""
        self.player_id = 0
        self.token = 0
        self.game_id = "0"
        self.place_path = "Lobby.0.1"
        self.http_cookie = ""
        self.ws = None
        self.connected = False
        self.logged_in = False
        self.created = False
        self.table_path = None
        self.bet_amounts = []
        self.states = {}
        self.begin_state_id = None
        self.current_state_id = None
        self.my_slot_id = -1
        self.is_playing = False
        self.board_lines = {}
        self.current_turn_slot = -1
        self.last_discard = None
        # Public, fair-play state retained for exactly one match.
        self.discarded_cards = []
        self.eaten_cards = []
        self.exposed_melds = []
        # Cards a given slot chose NOT to eat (i.e. they drew from the deck
        # while last_discard was sitting in the discard pile). Each entry:
        # {"card": int, "by_slot": int, "sequence": int}. Used by the AI to
        # demote danger scores for meld-partners those opponents skipped.
        self.skipped_eat_cards = []
        self._public_event_sequence = 0
        # AI round tracking — how many discards we've made this match.
        self.my_discard_count = 0
        self.games_started = 0
        self.games_completed = 0
        self.pending_action = None
        self.blocked_send_state_id = None
        self.rejected_discards = set()
        self._state_sequence = 0
        self._acted_sequence = -1
        self.stop_event = threading.Event()
        self.created_event = threading.Event()
        self.seated_event = threading.Event()
        self._send_lock = threading.Lock()
        self._action_lock = threading.Lock()
        self._enter_target = "lobby"
        self._last_receive = 0.0

    def fetch_session(self) -> None:
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar),
            urllib.request.HTTPRedirectHandler(),
        )
        opener.addheaders = [
            ("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/139 Safari/537.36"),
            ("Accept-Language", "vi-VN,vi;q=0.9,en;q=0.7"),
        ]
        opener.open(LOGIN_URL, timeout=20).read()
        body = urllib.parse.urlencode({
            "redirect": "/",
            "USER_NAME": self.username,
            "PASSWORD": self.password,
            "AUTO_LOGIN": "true",
            "LOGIN": "Đăng nhập",
        }).encode()
        req = urllib.request.Request(
            LOGIN_URL,
            data=body,
            headers={
                "Origin": "https://gamevh.net",
                "Referer": LOGIN_URL,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        opener.open(req, timeout=20).read()
        response = opener.open(GAME_URL, timeout=20)
        html = response.read().decode("utf-8", "replace")

        def required(pattern: str, label: str) -> str:
            match = re.search(pattern, html, re.I)
            if not match:
                raise RuntimeError(f"missing {label}; login may have failed")
            return match.group(1)

        self.token = int(required(r"var\s+token\s*=\s*(-?\d+)", "token"))
        self.nickname = required(
            r'''var\s+currentPlayerNickName\s*=\s*["']([^"']+)["']''',
            "nickname",
        ).strip()
        player_match = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", html, re.I)
        if player_match:
            self.player_id = int(player_match.group(1))
        game_match = re.search(r'''var\s+gameId\s*=\s*["']([^"']+)["']''', html, re.I)
        if game_match:
            self.game_id = game_match.group(1)
        place_match = re.search(r'''var\s+placePath\s*=\s*["']([^"']+)["']''', html, re.I)
        if place_match:
            self.place_path = place_match.group(1)
        self.http_cookie = "; ".join(f"{c.name}={c.value}" for c in jar)
        print(
            f"[SESSION] login OK nick={self.nickname!r} id={self.player_id} "
            f"game={self.game_id!r} place={self.place_path!r}",
            flush=True,
        )

    def send(self, command: str | int, payload: bytes = b"", *, detail: dict | None = None) -> None:
        if not self.ws or not self.connected:
            return
        with self._send_lock:
            self.ws.send(Codec.pack_command(command, payload), opcode=websocket.ABNF.OPCODE_BINARY)
        append_jsonl("events.jsonl", utc_record(
            direction="out", command=str(command), payload_length=len(payload),
            detail=detail or {},
        ))

    def send_login(self) -> None:
        payload = b"".join((
            Codec.ascii(self.nickname),
            Codec.integer(self.token),
            Codec.ascii("5.0.2"),
            Codec.ascii(""),
            Codec.ascii(self.game_id),
            Codec.byte(1),
        ))
        with self._send_lock:
            self.ws.send(Codec.pack_command("LOGIN", payload), opcode=websocket.ABNF.OPCODE_BINARY)
        append_jsonl("events.jsonl", utc_record(
            direction="out", command="LOGIN", payload_length=len(payload), redacted=True,
        ))

    def enter_place(self, path: str, target: str) -> None:
        self._enter_target = target
        payload = Codec.ascii(path) + Codec.string("") + Codec.byte(1)
        self.send("ENTER_PLACE", payload, detail={"path": path, "target": target})

    def request_bets(self) -> None:
        self.send("LIST_BET_AMT")

    def create_table(self, bet_id: int) -> None:
        payload = Codec.byte(bet_id) + Codec.byte(0)
        print(f"[CREATE] requesting public Phỏm table bet={self.target_bet} id={bet_id}", flush=True)
        self.send("CREATE_RULE", payload, detail={"bet_id": bet_id, "bet": self.target_bet})

    def request_table_data(self) -> None:
        self.send("GET_TABLE_DATA_EX", Codec.ascii(""))

    @staticmethod
    def _unsigned_count(value: int) -> int:
        return value + 256 if value < 0 else value

    def parse_state_data(self, message: Incoming) -> None:
        self.states = {}
        state_count = self._unsigned_count(message.byte())
        for _ in range(state_count):
            state_id = message.byte()
            state_code = message.ascii()
            mode = message.byte()
            command_count = self._unsigned_count(message.byte())
            commands = []
            for _ in range(command_count):
                commands.append({
                    "position": message.byte(),
                    "code": message.ascii(),
                    "name": message.string(),
                    "fill_board_state": message.byte() == 1,
                    "take_confirmation": message.byte() == 1,
                })
            self.states[state_id] = {
                "id": state_id,
                "code": state_code,
                "mode": mode,
                "commands": commands,
            }
        self.begin_state_id = message.byte()

    def parse_table_data(self, message: Incoming) -> dict:
        self.my_slot_id = message.byte()
        self.is_playing = message.byte() == 1
        player_count = self._unsigned_count(message.byte())
        players = []
        for _ in range(player_count):
            slot_id = message.byte()
            player_id = message.long()
            _full_name = message.string()
            _avatar_id = message.short()
            _avatar = message.ascii()
            _tag_id = message.byte()
            _chip_balance = message.long()
            _star_balance = message.long()
            _score = message.long()
            _level = message.byte()
            owner = message.byte() == 1
            players.append({"slot": slot_id, "player_id": player_id, "owner": owner})
        current_turn_slot = message.byte()
        self.current_turn_slot = current_turn_slot
        current_turn_timeout = message.short()
        player_remain_duration = message.short()
        self.current_state_id = message.byte()
        return {
            "my_slot": self.my_slot_id,
            "is_playing": self.is_playing,
            "players": players,
            "turn_slot": current_turn_slot,
            "turn_timeout": current_turn_timeout,
            "player_remain_duration": player_remain_duration,
            "state_id": self.current_state_id,
        }

    def parse_match_points(self, message: Incoming) -> dict[int, int]:
        count = self._unsigned_count(message.byte())
        return {message.byte(): message.integer() for _ in range(count)}

    def parse_board_data(self, message: Incoming) -> dict:
        slot_count = self._unsigned_count(message.byte())
        lines = {}
        for _ in range(slot_count):
            slot_id = message.byte()
            line_count = self._unsigned_count(message.byte())
            for _ in range(line_count):
                line_id = message.byte() - 1
                card_count = message.byte()
                if card_count < 0:
                    cards = message.byte_array()
                else:
                    cards = [-1] * card_count
                lines[(slot_id, line_id)] = cards
        self.board_lines = lines
        hand = lines.get((self.my_slot_id, 0), [])
        return {
            "slot_count": slot_count,
            "line_counts": {
                f"{slot}:{line}": len(cards)
                for (slot, line), cards in sorted(lines.items())
            },
            "hand": hand,
        }

    def reset_public_state(self) -> None:
        self.discarded_cards = []
        self.eaten_cards = []
        self.exposed_melds = []
        self.skipped_eat_cards = []
        self._public_event_sequence = 0
        self.my_discard_count = 0

    def public_state_snapshot(self) -> dict:
        return {
            "discarded_cards": [dict(item) for item in self.discarded_cards],
            "eaten_cards": [dict(item) for item in self.eaten_cards],
            "exposed_melds": [
                {"slot": item["slot"], "cards": list(item["cards"])}
                for item in self.exposed_melds
            ],
            "skipped_eat_cards": [dict(item) for item in self.skipped_eat_cards],
            "last_discard": self.last_discard,
        }

    def rebuild_exposed_melds(self, slot_id: int | None = None) -> None:
        slots = ({slot for slot, line in self.board_lines if line == 1}
                 if slot_id is None else {slot_id})
        self.exposed_melds = [
            item for item in self.exposed_melds if item["slot"] not in slots
        ]
        for slot in sorted(slots):
            cards = [card for card in self.board_lines.get((slot, 1), [])
                     if 0 <= card < 52]
            _deadwood, groups = best_meld_partition(cards)
            for group in groups:
                self.exposed_melds.append({
                    "slot": slot,
                    "cards": sorted(group),
                })
        self.exposed_melds.sort(
            key=lambda item: (item["slot"], item["cards"][0], len(item["cards"]))
        )

    def record_public_state(self, reason: str) -> None:
        append_jsonl("events.jsonl", utc_record(
            direction="state",
            command="STATE_MANAGER",
            reason=reason,
            state=self.public_state_snapshot(),
        ))

    def current_hand(self) -> list[int]:
        return [card for card in self.board_lines.get((self.my_slot_id, 0), [])
                if 0 <= card < 52]

    @staticmethod
    def describe_cards(cards: list[int]) -> list[str]:
        return [card_name(card) for card in cards]

    def exposed_meld_lines(self) -> list[list[int]]:
        return [
            [card for card in cards if 0 <= card < 52]
            for (slot_id, line_id), cards in self.board_lines.items()
            if slot_id != self.my_slot_id and line_id == 1
            and any(0 <= card < 52 for card in cards)
        ]

    def has_own_exposed_meld_marker(self) -> bool:
        return any(0 <= card < 52
                   for card in self.board_lines.get((self.my_slot_id, 1), []))

    def own_eaten_cards(self) -> list[int]:
        return [
            item["card"] for item in self.eaten_cards
            if item["to_slot"] == self.my_slot_id
        ]

    def enter_state(self, state_id: int, source: str) -> None:
        if state_id != self.current_state_id:
            self.blocked_send_state_id = None
            self.rejected_discards.clear()
        self.current_state_id = state_id
        self._state_sequence += 1
        state = self.states.get(state_id)
        print(
            f"[STATE-IN] source={source} id={state_id} "
            f"code={(state or {}).get('code')!r} turn={self.current_turn_slot} "
            f"hand={self.describe_cards(self.current_hand())}",
            flush=True,
        )
        if PLAY_ENABLED:
            sequence = self._state_sequence
            threading.Timer(ACTION_DELAY, self.act_for_state, args=(sequence,)).start()

    def _state_command(self, state: dict, code: str) -> dict | None:
        return next((item for item in state.get("commands", [])
                     if item.get("code") == code), None)

    def send_state_action(self, state: dict, code: str,
                          selected_cards: list[int] | None = None) -> bool:
        command = self._state_command(state, code)
        if command is None:
            return False
        selected_cards = selected_cards or []
        payload = b""
        if command["fill_board_state"]:
            if not selected_cards:
                print(f"[ACTION] {code} requires cards but none selected", flush=True)
                return False
            if state["mode"] == 1:
                payload = Codec.byte(selected_cards[0])
            else:
                payload = Codec.byte_array(selected_cards)
        self.pending_action = {
            "code": code,
            "state_id": state["id"],
            "cards": list(selected_cards),
            "sent_at": time.time(),
        }
        print(
            f"[ACTION] send {code} cards={self.describe_cards(selected_cards)}",
            flush=True,
        )
        self.send(code, payload, detail={
            "state_id": state["id"], "cards": selected_cards,
        })
        return True

    def act_for_state(self, sequence: int) -> None:
        with self._action_lock:
            if not PLAY_ENABLED or self.stop_event.is_set():
                return
            if sequence != self._state_sequence or sequence == self._acted_sequence:
                return
            if self.pending_action is not None:
                return
            state = self.states.get(self.current_state_id)
            if state is None:
                print(f"[ACTION] unknown state {self.current_state_id}", flush=True)
                self.request_table_data()
                return
            if self.current_turn_slot not in (-1, self.my_slot_id):
                return

            code = state["code"]
            sent = False
            if code == "take":
                sent = self.send_state_action(state, "TAKE")
            elif code == "eat":
                hand = self.current_hand()
                # IS-MCTS for eat decision (if enabled via PHOM_ISMCTS=1)
                should_eat = None
                ismcts = _get_ismcts()
                if (ismcts is not None and ismcts.ISMCTS_ENABLED
                        and self._state_command(state, "EAT") is not None
                        and self.last_discard is not None
                        and can_form_meld_with(hand, self.last_discard)):
                    try:
                        should_eat = ismcts.ismcts_decide_eat(
                            hand,
                            self.last_discard,
                            self.public_state_snapshot(),
                            self.my_slot_id,
                            required_cards=self.own_eaten_cards(),
                            round_idx=self.my_discard_count,
                        )
                    except Exception as e:
                        print(f"[IS-MCTS] error: {e} — falling back to "
                              f"heuristic", flush=True)
                        should_eat = None

                if should_eat is not None:
                    # IS-MCTS decided
                    sent = self.send_state_action(
                        state, "EAT" if should_eat else "TAKE"
                    )
                elif (self._state_command(state, "EAT") is not None
                        and can_form_meld_with(hand, self.last_discard)):
                    # Fallback: original heuristic — eat if forms meld
                    sent = self.send_state_action(state, "EAT")
                else:
                    sent = self.send_state_action(state, "TAKE")
            elif code in ("remove", "finalRemove"):
                send_card = None
                if (code == "finalRemove"
                        and self.blocked_send_state_id != state["id"]
                        and self._state_command(state, "SEND_CARD")):
                    send_card = choose_send_card(
                        self.current_hand(), self.exposed_meld_lines()
                    )
                if send_card is not None:
                    sent = self.send_state_action(state, "SEND_CARD", [send_card])
                else:
                    discard = None
                    # PIMC for any remove state (if enabled via PHOM_PIMC=1)
                    # Originally only finalRemove, but server rarely triggers
                    # that state — extend to all remove states for impact.
                    pimc = _get_pimc()
                    if pimc is not None and pimc.PIMC_ENABLED:
                        try:
                            discard = pimc.pimc_choose_discard(
                                self.current_hand(),
                                self.public_state_snapshot(),
                                self.my_slot_id,
                                required_cards=self.own_eaten_cards(),
                                forbidden_cards=self.rejected_discards,
                                round_idx=self.my_discard_count,
                                is_final_round=(code == "finalRemove"),
                            )
                        except Exception as e:
                            print(f"[PIMC] error: {e} — falling back to "
                                  f"heuristic", flush=True)
                            discard = None
                    # Fallback to heuristic
                    if discard is None:
                        discard = choose_discard_ai(
                            self.current_hand(),
                            self.public_state_snapshot(),
                            self.my_slot_id,
                            required_cards=self.own_eaten_cards(),
                            forbidden_cards=self.rejected_discards,
                            round_idx=self.my_discard_count,
                            is_final_round=(code == "finalRemove"),
                        )
                    if discard is not None:
                        sent = self.send_state_action(state, "REMOVE", [discard])
                    else:
                        print("[ACTION] no concrete hand card available; refreshing", flush=True)
                        self.request_table_data()
            elif code == "drop":
                send_card = None
                if (self.blocked_send_state_id != state["id"]
                        and self.has_own_exposed_meld_marker()
                        and self._state_command(state, "SEND_CARD")):
                    send_card = choose_send_card(
                        self.current_hand(), self.exposed_meld_lines()
                    )
                if send_card is not None:
                    sent = self.send_state_action(state, "SEND_CARD", [send_card])
                else:
                    sent = self.send_state_action(state, "DROP_AVAILABLE_BAND")
            elif code == "waitTurn":
                return
            else:
                print(f"[ACTION] no policy for state {code!r}", flush=True)

            if sent:
                self._acted_sequence = sequence

    def handle_action_response(self, message: Incoming) -> bool:
        pending = self.pending_action
        if pending is None or message.command != pending["code"]:
            return False
        status = message.byte()
        error = ""
        if status != 0 and message.remaining() >= 2:
            error = message.string()
        append_jsonl("events.jsonl", utc_record(
            direction="parsed", command=message.command,
            status=status, error=error, pending=pending,
        ))
        print(
            f"[ACTION] result {message.command} status={status} error={error!r}",
            flush=True,
        )
        self.pending_action = None
        if status != 0:
            if message.command == "SEND_CARD":
                self.blocked_send_state_id = pending["state_id"]
            elif message.command == "REMOVE":
                self.rejected_discards.update(pending["cards"])
            self.request_table_data()
        elif message.command == "SEND_CARD":
            self.blocked_send_state_id = None
            self._state_sequence += 1
            sequence = self._state_sequence
            threading.Timer(ACTION_DELAY, self.act_for_state, args=(sequence,)).start()
        return True

    def apply_move_event(self, message: Incoming) -> None:
        cards = message.byte_array()
        source_slot = message.byte()
        source_line = message.byte() - 1
        target_slot = message.byte()
        target_line = message.byte() - 1
        target_index = message.byte()
        source_key = (source_slot, source_line)
        target_key = (target_slot, target_line)
        source_cards = self.board_lines.setdefault(source_key, [])
        moved = []
        for card in cards:
            if card in source_cards:
                source_cards.remove(card)
            elif -1 in source_cards:
                source_cards.remove(-1)
            moved.append(card)
        target_cards = self.board_lines.setdefault(target_key, [])
        if target_index < 0 or target_index > len(target_cards):
            target_cards.extend(moved)
        else:
            for offset, card in enumerate(moved):
                target_cards.insert(target_index + offset, card)
        reasons = []
        concrete = [card for card in moved if 0 <= card < 52]
        if source_line == 0 and target_line == 3 and concrete:
            self._public_event_sequence += 1
            for card in concrete:
                self.discarded_cards.append({
                    "sequence": self._public_event_sequence,
                    "card": card,
                    "by_slot": source_slot,
                })
            self.last_discard = concrete[-1]
            # Track our own discard count for the AI's round_idx.
            if source_slot == self.my_slot_id:
                self.my_discard_count += 1
            reasons.append("discard")
        # Detect "skip eat": a player drew from the deck (-1:0 -> slot:0)
        # while last_discard was sitting in the pile and they aren't us.
        # That's a tell they don't need last_discard (or its meld family).
        if (source_slot == -1 and source_line == 0
                and target_line == 0 and target_slot != self.my_slot_id
                and self.last_discard is not None):
            self._public_event_sequence += 1
            self.skipped_eat_cards.append({
                "sequence": self._public_event_sequence,
                "card": self.last_discard,
                "by_slot": target_slot,
            })
            reasons.append("skip-eat")
        if (source_line == 3 and target_line == 1
                and source_slot != target_slot and concrete):
            self._public_event_sequence += 1
            for card in concrete:
                self.eaten_cards.append({
                    "sequence": self._public_event_sequence,
                    "card": card,
                    "from_slot": source_slot,
                    "to_slot": target_slot,
                })
            # They ate it -> remove from skipped list (in case they had skipped
            # the same card family earlier but changed their mind).
            self.skipped_eat_cards = [
                item for item in self.skipped_eat_cards
                if not (item["by_slot"] == target_slot
                        and item["card"] in concrete)
            ]
            self.last_discard = None
            reasons.append("eat")
        if source_line == 1:
            self.rebuild_exposed_melds(source_slot)
            reasons.append("exposed-source-update")
        if target_line == 1:
            self.rebuild_exposed_melds(target_slot)
            reasons.append("exposed-target-update")
        if reasons:
            if not any(reason in ("discard", "eat") for reason in reasons):
                self._public_event_sequence += 1
            self.record_public_state("+".join(reasons))
        print(
            f"[MOVE] cards={self.describe_cards(moved)} "
            f"{source_slot}:{source_line}->{target_slot}:{target_line} "
            f"hand={self.describe_cards(self.current_hand())} "
            f"my_discards={self.my_discard_count}",
            flush=True,
        )

    def _capture_incoming(self, raw: bytes, message: Incoming) -> None:
        redacted = message.command in {
            "LOGIN", "CONFIG", "SLOT_IN_TABLE_CHANGED", "PLAYER_ENTERED",
            "SHOW_PLAYER_CARD", "START_MATCH", "ENTER_STATE",
        }
        append_jsonl("frames.jsonl", utc_record(
            direction="in",
            command=message.command,
            length=len(raw),
            payload_hex=None if redacted else raw[message.offset:message.offset + 256].hex(),
            redacted=redacted,
        ))

    def reset_capture(self) -> None:
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        for name in ("frames.jsonl", "events.jsonl"):
            (CAPTURE_DIR / name).write_text("", encoding="utf-8")

    def on_open(self, ws) -> None:
        self.connected = True
        self._last_receive = time.time()
        print("[WS] connected", flush=True)
        self.send_login()

    def on_message(self, ws, raw) -> None:
        if not isinstance(raw, (bytes, bytearray)):
            return
        self._last_receive = time.time()
        try:
            message = Incoming(raw)
            if PLAY_ENABLED and message.command == "START_MATCH":
                self.reset_capture()
            self._capture_incoming(bytes(raw), message)
            self.handle(message)
        except Exception as exc:
            print(f"[WS] decode error: {exc}", flush=True)

    def handle(self, message: Incoming) -> None:
        command = message.command
        if command == "PING":
            self.send("PONG")
            return
        if self.handle_action_response(message):
            return
        if command == "LOGIN":
            status = message.byte()
            if status != 0:
                error = message.string() if message.remaining() >= 2 else ""
                raise RuntimeError(f"WS login failed status={status} error={error!r}")
            self.logged_in = True
            path = message.string()
            if path == "REFRESH":
                raise RuntimeError("server requested HTTP session refresh")
            if message.remaining() > 0:
                _ws_cookie = message.ascii()
            print("[WS] protocol login OK", flush=True)
            self.enter_place(self.place_path, "lobby")
            return
        if command == "ENTER_PLACE":
            status = message.byte()
            if status != 0:
                error = message.string() if message.remaining() >= 2 else ""
                print(f"[ENTER] target={self._enter_target} status={status} {error!r}", flush=True)
                return
            detail = {"target": self._enter_target}
            if message.remaining() >= 3:
                detail["currency"] = message.byte()
                detail["entrance_rate_tenths"] = message.short()
            append_jsonl("events.jsonl", utc_record(direction="parsed", command=command, detail=detail))
            print(f"[ENTER] {self._enter_target} OK", flush=True)
            if self._enter_target == "lobby" and not self.created:
                self.request_bets()
            elif self._enter_target == "table":
                self.created_event.set()
            return
        if command == "LIST_BET_AMT":
            status = message.byte()
            if status != 0:
                error = message.string() if message.remaining() >= 2 else ""
                raise RuntimeError(f"LIST_BET_AMT failed status={status} error={error!r}")
            count = message.byte()
            if count < 0:
                count += 256
            self.bet_amounts = [{"id": i, "value": message.integer()} for i in range(count)]
            append_jsonl("events.jsonl", utc_record(
                direction="parsed", command=command, bets=self.bet_amounts,
            ))
            print(f"[BET] {self.bet_amounts}", flush=True)
            exact = next((item for item in self.bet_amounts if item["value"] == self.target_bet), None)
            if exact is None:
                raise RuntimeError(f"exact bet {self.target_bet} not offered; refusing fallback")
            self.create_table(exact["id"])
            return
        if command == "GET_TABLE_DATA_EX":
            status = message.byte()
            if status != 0:
                error = message.string() if message.remaining() >= 2 else ""
                raise RuntimeError(
                    f"GET_TABLE_DATA_EX failed status={status} error={error!r}"
                )
            self.parse_state_data(message)
            table = self.parse_table_data(message)
            points = self.parse_match_points(message)
            board = self.parse_board_data(message)
            if self.is_playing:
                self.rebuild_exposed_melds()
            auto_start = message.byte() == 1
            currency = message.byte()
            arg_count = self._unsigned_count(message.byte())
            args = {message.ascii(): message.string() for _ in range(arg_count)}
            state_dump = [self.states[key] for key in sorted(self.states)]
            append_jsonl("events.jsonl", utc_record(
                direction="parsed", command=command,
                states=state_dump, begin_state_id=self.begin_state_id,
                table=table, points=points, board=board,
                auto_start=auto_start, currency=currency, args=args,
                public_state=self.public_state_snapshot(),
            ))
            print(
                f"[TABLE-DATA] my_slot={self.my_slot_id} playing={self.is_playing} "
                f"state={self.current_state_id} begin={self.begin_state_id} "
                f"hand={board['hand']}",
                flush=True,
            )
            for state in state_dump:
                summary = [
                    f"{item['code']}:{item['name']}"
                    f"{'[cards]' if item['fill_board_state'] else ''}"
                    for item in state["commands"]
                ]
                print(
                    f"[STATE] id={state['id']} code={state['code']!r} "
                    f"mode={state['mode']} commands={summary}",
                    flush=True,
                )
            if PLAY_ENABLED and self.is_playing and self.current_state_id in self.states:
                self.enter_state(self.current_state_id, "GET_TABLE_DATA_EX")
            return
        if command == "CREATE_RULE":
            status = message.byte()
            if status != 0:
                error = message.string() if message.remaining() >= 2 else ""
                raise RuntimeError(f"CREATE_RULE failed status={status} error={error!r}")
            table_id = message.ascii()
            self.table_path = (table_id if "." in table_id
                               else f"{self.place_path}.{table_id}")
            entrance_value = message.integer() if message.remaining() >= 4 else None
            self.created = True
            append_jsonl("events.jsonl", utc_record(
                direction="parsed", command=command,
                table_id=table_id, table_path=self.table_path,
                entrance_value=entrance_value,
            ))
            print(
                f"[CREATE] success table={self.table_path!r} "
                f"entrance_value={entrance_value}",
                flush=True,
            )
            self._enter_target = "table"
            self.created_event.set()
            self.request_table_data()
            if HOLD_SECONDS > 0 and not PLAY_ENABLED:
                print(
                    f"[SAFETY] capture-only probe will disconnect in "
                    f"{HOLD_SECONDS}s",
                    flush=True,
                )
                threading.Timer(HOLD_SECONDS, self.close).start()
            return
        if command == "START_MATCH":
            if not PLAY_ENABLED:
                print(
                    "[SAFETY] START_MATCH received in capture-only mode; "
                    "disconnecting",
                    flush=True,
                )
                threading.Thread(target=self.close, daemon=True).start()
                return
            self.reset_public_state()
            points = self.parse_match_points(message)
            board = self.parse_board_data(message)
            self.rebuild_exposed_melds()
            self.games_started += 1
            self.is_playing = True
            self.last_discard = None
            self.pending_action = None
            self.rejected_discards.clear()
            append_jsonl("events.jsonl", utc_record(
                direction="parsed", command=command,
                game=self.games_started, points=points, board=board,
                states=[self.states[key] for key in sorted(self.states)],
                begin_state_id=self.begin_state_id,
                public_state=self.public_state_snapshot(),
            ))
            print(
                f"[GAME] START #{self.games_started} "
                f"hand={self.describe_cards(board['hand'])}",
                flush=True,
            )
            if self.begin_state_id is not None:
                self.enter_state(self.begin_state_id, "START_MATCH")
            return
        if command == "SET_TURN":
            self.current_turn_slot = message.byte()
            turn_timeout = message.short()
            remain_duration = message.short() if message.remaining() >= 2 else None
            print(
                f"[TURN] slot={self.current_turn_slot} timeout={turn_timeout} "
                f"remain={remain_duration}",
                flush=True,
            )
            if PLAY_ENABLED and self.current_turn_slot == -2 and turn_timeout > 0:
                self.send("SET_READY")
            return
        if command == "ENTER_STATE":
            state_id = message.byte()
            self.enter_state(state_id, "ENTER_STATE")
            return
        if command == "MOVE":
            self.apply_move_event(message)
            return
        if command == "SET_CARDS":
            slot_id = message.byte()
            line_id = message.byte() - 1
            card_count = message.byte()
            cards = message.byte_array() if card_count < 0 else [-1] * card_count
            self.board_lines[(slot_id, line_id)] = cards
            if line_id == 1:
                self._public_event_sequence += 1
                self.rebuild_exposed_melds(slot_id)
                self.record_public_state("set-exposed-cards")
            print(
                f"[CARDS] set {slot_id}:{line_id} "
                f"count={len(cards)} hand={self.describe_cards(self.current_hand())}",
                flush=True,
            )
            return
        if command == "CLEAR_CARDS":
            slot_id = message.byte()
            line_id = message.byte() - 1
            self.board_lines[(slot_id, line_id)] = []
            if line_id == 1:
                self._public_event_sequence += 1
                self.rebuild_exposed_melds(slot_id)
                self.record_public_state("clear-exposed-cards")
            return
        if command == "SHOW_PLAYER_CARD":
            slot_id = message.byte()
            cards = message.byte_array()
            self.board_lines[(slot_id, 0)] = cards
            return
        if command == "GAMEOVER":
            count = self._unsigned_count(message.byte())
            results = []
            for _ in range(count):
                results.append({
                    "slot": message.byte(),
                    "grade": message.byte(),
                    "earn": message.long(),
                })
            result_text = message.string() if message.remaining() >= 2 else ""
            self.games_completed += 1
            self.is_playing = False
            append_jsonl("events.jsonl", utc_record(
                direction="parsed", command=command,
                game=self.games_completed, results=results,
                result_text=result_text,
                public_state=self.public_state_snapshot(),
            ))
            print(
                f"[GAME] OVER #{self.games_completed} results={results} "
                f"text={result_text!r}",
                flush=True,
            )
            if self.games_completed >= MAX_GAMES:
                threading.Timer(1.0, self.close).start()
            else:
                threading.Timer(3.0, lambda: self.send("SET_READY")).start()
            return
        if command == "SLOT_IN_TABLE_CHANGED":
            _full_name = message.string()
            slot_id = message.byte()
            _chip_balance = message.long()
            _score = message.long()
            _level = message.byte()
            _avatar_id = message.short()
            _avatar = message.ascii()
            _tag_id = message.byte()
            owner = message.byte() == 1
            player_id = message.long()
            _star_balance = message.long()
            if player_id == self.player_id:
                self.seated_event.set()
                print(
                    f"[TABLE] seated slot={slot_id} owner={owner} "
                    f"table={self.table_path or '(creating)'}",
                    flush=True,
                )
            return
        if command == "ALERT":
            text = message.string() if message.remaining() >= 2 else ""
            print(f"[ALERT] {text}", flush=True)
            return
        print(f"[WS] {command} bytes_remaining={message.remaining()}", flush=True)

    def on_error(self, ws, error) -> None:
        print(f"[WS] error {type(error).__name__}: {error}", flush=True)

    def on_close(self, ws, code, reason) -> None:
        self.connected = False
        print(f"[WS] closed code={code} reason={reason!r}", flush=True)
        self.stop_event.set()

    def keepalive(self) -> None:
        while not self.stop_event.wait(10):
            if self.connected:
                self.send("PING")

    def run(self) -> None:
        self.fetch_session()
        self.reset_capture()
        self.ws = websocket.WebSocketApp(
            WS_URL,
            cookie=self.http_cookie,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        threading.Thread(target=self.keepalive, daemon=True).start()
        self.ws.run_forever(origin="https://gamevh.net", ping_interval=0)

    def close(self) -> None:
        self.stop_event.set()
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass


# ===========================================================================
# Section E — Entry point
# ===========================================================================

def main() -> int:
    username = os.environ.get("GAMEVH_USER", "").strip()
    password = os.environ.get("GAMEVH_PASSWORD", "")
    if not username or not password:
        print("Set GAMEVH_USER and GAMEVH_PASSWORD in the environment", file=sys.stderr)
        return 2
    bot = PhomTableBot(username, password, TARGET_BET)
    atexit.register(bot.close)
    signal.signal(signal.SIGTERM, lambda *_: bot.close())
    signal.signal(signal.SIGINT, lambda *_: bot.close())
    bot.run()
    return 0 if bot.created else 1


if __name__ == "__main__":
    raise SystemExit(main())
