#!/usr/bin/env python3
"""Pure helpers for the fair GameVH -> JieqiCore state boundary.

The server's START_MATCH frame contains ``raw_face`` for covered pieces.  That
is truth data for diagnostics, not information that may be sent to the live
engine before a reveal.  These helpers keep physical covered/open state
separate from truth and only expose a role when the move makes it known to the
bot under the normal Jieqi information rules.
"""
from __future__ import annotations

TYPE_TO_FEN = {1: "k", 2: "a", 3: "b", 4: "r", 5: "c", 6: "n", 7: "p"}
VALID_REVEALS = frozenset("ABNRCPabnrcp")


def _truth_role(value: str | None) -> str | None:
    if value and value in VALID_REVEALS:
        return value
    return None


def fen_piece_for_view(sid: str, face: str, is_open: bool) -> str:
    """Return the role visible to JieqiCore for one START_MATCH piece.

    Covered pieces remain X/x even though ``face`` contains server truth.
    Open pieces use their actual role.  Only the color bit of a covered piece
    is used; its hidden type never crosses the live engine boundary.
    """
    color = (sid or face or "r")[:1]
    if not is_open:
        return "X" if color == "r" else "x"

    if len(face) < 2 or not face[1].isdigit():
        raise ValueError(f"invalid open face: {face!r}")
    role = TYPE_TO_FEN.get(int(face[1]))
    if role is None:
        raise ValueError(f"invalid open piece type: {face!r}")
    return role.upper() if color == "r" else role


def resolve_move_reveals(
    *,
    wire_reveal: str | None,
    mover_dark: bool,
    flip_move: bool,
    captured_dark: bool,
    mover_is_bot: bool,
    source_truth: str | None,
    target_truth: str | None,
) -> tuple[str | None, str | None]:
    """Resolve the mover/captured suffixes the current bot may know.

    A covered mover becomes public when it moves or flips, so START_MATCH truth
    may safely repair a missing MOVE reveal at that moment.  A covered captured
    role is exposed only when this bot is the capturer (capturer-only reveal).
    Truth for a covered piece captured by the opponent is deliberately ignored.
    """
    wire = _truth_role(wire_reveal)
    source = _truth_role(source_truth)
    target = _truth_role(target_truth)

    mover_reveal = None
    captured_reveal = None

    if mover_dark or flip_move:
        # MOVE is primary.  If the two sources disagree after the legal reveal,
        # START_MATCH truth is the stable identity for that exact piece.
        mover_reveal = source if wire and source and wire != source else (wire or source)

    if captured_dark and mover_is_bot:
        # With a dark mover the MOVE byte describes the mover; target truth is
        # needed for the second (captured-role) suffix.  With an open mover the
        # MOVE byte may itself be the captured role.
        if mover_dark or flip_move:
            captured_reveal = target
        else:
            captured_reveal = target if wire and target and wire != target else (wire or target)

    return mover_reveal, captured_reveal


def encode_jieqi_move(
    move: str,
    mover_reveal: str | None = None,
    captured_reveal: str | None = None,
) -> str:
    """Encode JieqiCore's 4/5/6-character replay token."""
    if len(move) != 4:
        raise ValueError(f"invalid coordinate move: {move!r}")
    mover = _truth_role(mover_reveal)
    captured = _truth_role(captured_reveal)
    if mover_reveal and not mover:
        raise ValueError(f"invalid mover reveal: {mover_reveal!r}")
    if captured_reveal and not captured:
        raise ValueError(f"invalid captured reveal: {captured_reveal!r}")
    return move + (mover or "") + (captured or "")
