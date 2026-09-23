#!/usr/bin/env python3
"""Safe WebSocket frame logger for an authorized GameVH bot session.

This does not intercept traffic, bypass TLS, or collect credentials. It is
intended to be called from the bot's on_message callback after websocket-client
has decrypted the WSS frame for the bot itself.

Multi-bot support
-----------------
Several bots (arena15, cup_bot_mistboard, ...) may run from the same checkout
(e.g. side by side on one VPS, or several processes in one CI job). Each bot
automatically logs into its own subdirectory:

    ws_capture/<bot>/frames.jsonl
    ws_capture/<bot>/start_match.jsonl
    ws_capture/<bot>/events.jsonl

The <bot> name is resolved, in order, from:
    1. $WS_CAPTURE_DIR        — full directory override (skips the base dir)
    2. $WS_CAPTURE_BOT        — bot name only
    3. $CARO_USER19           — bot account name (set by the workflows)
    4. sys.argv[0] basename   — e.g. "arena15.py" -> "arena15"
    5. "default"

Per-match logs and rotation
---------------------------
By default, receiving START_MATCH truncates frames.jsonl, start_match.jsonl,
and events.jsonl before the new match is written. This keeps the capture files
scoped to exactly one game and prevents tools or people from accidentally
mixing raw_face/reveal records from an older game. Set
WS_CAPTURE_PER_MATCH=0 to restore the old append-across-games behaviour.

The files are also size-capped (default 20 MB, keep 2 rotated copies) so one
very long game cannot fill the disk. PING/PONG frames are not written by
default (biggest noise source); set WS_CAPTURE_LOG_PING=1 to keep them.

This module never raises: logging failures are swallowed after one warning so
the bot's confirm/reveal pipeline (decode_frame) is never disturbed.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any

CMD_NAMES = {
    417: "START_MATCH",
    418: "GAMEOVER",
    420: "SET_TURN",
    529: "MOVE",
}

# --- tunables (env-overridable, read lazily so tests can change them) -------
_WRITE_LOCK = threading.Lock()
_WARNED = set()

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default

def _max_bytes() -> int:
    return _env_int("WS_CAPTURE_MAX_BYTES", 20 * 1024 * 1024)

def _keep_copies() -> int:
    return max(0, _env_int("WS_CAPTURE_KEEP", 2))

def _log_ping() -> bool:
    return os.environ.get("WS_CAPTURE_LOG_PING", "") == "1"

def _per_match_logs() -> bool:
    # Safer default: one capture set belongs to one game only.
    return os.environ.get("WS_CAPTURE_PER_MATCH", "1") != "0"

def _base_dir() -> str:
    return os.environ.get("WS_CAPTURE_BASE", "ws_capture")

def _bot_name() -> str:
    explicit = os.environ.get("WS_CAPTURE_BOT")
    if explicit:
        return explicit
    user = os.environ.get("CARO_USER19")
    if user:
        return user
    try:
        argv0 = sys.argv[0] or ""
        if argv0 and not argv0.endswith(("python", "python3", "-")):
            return Path(argv0).stem or "default"
    except Exception:
        pass
    return "default"

def _resolve_dir(directory: str | None) -> Path:
    if directory:
        return Path(directory)
    env_dir = os.environ.get("WS_CAPTURE_DIR")
    if env_dir:
        return Path(env_dir)
    return Path(_base_dir()) / _bot_name()

def _warn_once(key: str, text: str) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        try:
            print(f"[WS-CAPTURE] {text}", flush=True)
        except Exception:
            pass


def s8(v: int) -> int:
    return v - 256 if v > 127 else v


def decode_piece_id(raw: int) -> str:
    raw = s8(raw)
    color = "r" if raw >= 0 else "b"
    value = abs(raw)
    return f"{color}{value >> 3}{'' if (value & 7) == 0 else value & 7}"


def u8(data: bytes, off: int) -> tuple[int, int]:
    if off >= len(data):
        raise ValueError("truncated byte")
    return data[off], off + 1


def i32(data: bytes, off: int) -> tuple[int, int]:
    if off + 4 > len(data):
        raise ValueError("truncated int")
    return struct.unpack_from(">i", data, off)[0], off + 4


def command_id(data: bytes) -> tuple[int | str, int]:
    """Parse the same command prefix used by n17.py."""
    if not data:
        raise ValueError("empty frame")
    first = s8(data[0])
    if first < 0:
        n = -first
        end = 1 + n
        return data[1:end].decode("ascii", "replace"), end
    if len(data) < 2:
        raise ValueError("truncated numeric command")
    number = (data[0] << 8) | data[1]
    return CMD_NAMES.get(number, number), 2


def parse_start_match(data: bytes) -> dict[str, Any]:
    cmd, off = command_id(data)
    if cmd != "START_MATCH":
        raise ValueError(f"not START_MATCH: {cmd!r}")

    player_count, off = u8(data, off)
    players = []
    for _ in range(player_count):
        slot, off = u8(data, off)
        player_id, off = i32(data, off)
        players.append({"slot": slot, "player_id": player_id})

    piece_count, off = u8(data, off)
    pieces = []
    for index in range(piece_count):
        raw_sid, off = u8(data, off)
        raw_face, off = u8(data, off)
        position, off = u8(data, off)
        is_open, off = u8(data, off)
        pieces.append({
            "index": index,
            "raw_sid_byte": raw_sid,
            "raw_face_byte": raw_face,
            "sid_decoded": decode_piece_id(raw_sid),
            "face_decoded": decode_piece_id(raw_face),
            "position": position,
            "is_open": bool(is_open),
        })

    return {
        "command": "START_MATCH",
        "player_count": player_count,
        "players": players,
        "piece_count": piece_count,
        "pieces": pieces,
        "remaining_bytes": len(data) - off,
    }


PIECE_TYPE_TO_FEN = {1: "k", 2: "a", 3: "b", 4: "r", 5: "c", 6: "n", 7: "p"}


def decode_face_byte(raw: int) -> str | None:
    """Decode the revealed-piece byte used by GameVH MOVE payloads."""
    value = s8(raw)
    if value == 0:
        return None
    piece = PIECE_TYPE_TO_FEN.get(abs(value) >> 3)
    if not piece:
        return None
    return piece.upper() if value > 0 else piece


def parse_move(data: bytes) -> dict[str, Any]:
    """Parse MOVE, including the revealed piece, from the wire payload.

    GameVH puts the revealed SID at payload byte 2 when payload byte 0 is
    non-zero. Keep this rule here so the bot does not independently interpret
    raw WebSocket bytes.
    """
    cmd, off = command_id(data)
    if cmd != "MOVE":
        raise ValueError(f"not MOVE: {cmd!r}")
    source, off = u8(data, off)
    target, off = u8(data, off)
    payload = data[off:]
    reveal_byte = payload[2] if len(payload) >= 3 and payload[0] > 0 else None
    return {
        "command": "MOVE",
        "source": source,
        "target": target,
        "payload_hex": payload.hex(),
        "reveal_byte": reveal_byte,
        "revealed_piece": decode_face_byte(reveal_byte) if reveal_byte is not None else None,
    }


def decode_frame(data: bytes) -> dict[str, Any]:
    """Decode supported game events once for both bot logic and logging."""
    cmd, _ = command_id(data)
    if cmd == "START_MATCH":
        return parse_start_match(data)
    if cmd == "MOVE":
        return parse_move(data)
    return {"command": cmd, "length": len(data), "hex_prefix": data[:256].hex()}


def _rotate_if_needed(path: Path, max_bytes: int, keep: int) -> None:
    """Size-capped rotation: file -> file.1 -> file.2 ... (oldest dropped)."""
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return
        for i in range(keep, 0, -1):
            src = path.with_suffix(path.suffix + f".{i}")
            if i == keep:
                if src.exists():
                    src.unlink()
                continue
            if src.exists():
                src.replace(path.with_suffix(path.suffix + f".{i + 1}"))
        path.replace(path.with_suffix(path.suffix + ".1"))
    except OSError as exc:
        _warn_once(f"rotate:{path}", f"rotation failed for {path}: {exc}")


def _open_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Do not let a shared-readable log expose game frames.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    return os.fdopen(fd, "a", encoding="utf-8")


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Rotate (size-capped) then append one JSON record. Every log file,
    including events.jsonl / start_match.jsonl, goes through here so none of
    them can grow without bound."""
    _rotate_if_needed(path, _max_bytes(), _keep_copies())
    with _open_log(path) as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _truncate_log(path: Path) -> None:
    """Create or atomically empty a private log file while holding the caller's
    write lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)


def _start_new_match(out: Path) -> None:
    """Discard captures from the previous game.

    Called only after a valid START_MATCH command prefix has been decoded and
    while _WRITE_LOCK is held. GAMEOVER intentionally does not clear anything:
    the completed game's logs remain available until the next game begins.
    """
    for name in ("frames.jsonl", "start_match.jsonl", "events.jsonl"):
        _truncate_log(out / name)


def log_incoming_frame(data: bytes, directory: str | None = None) -> None:
    """Log one already-decrypted binary WebSocket message.

    LOGIN frames are intentionally not written because they can contain
    session-related values. Other frames get a bounded hex dump. START_MATCH
    additionally gets a parsed JSON record for raw_face analysis.

    Never raises: any I/O problem is reported once and then ignored so the
    bot's game logic is unaffected.
    """
    data = bytes(data)
    try:
        cmd, _ = command_id(data)
    except Exception as exc:
        cmd = f"parse_error:{exc}"

    if cmd == "LOGIN":
        return
    if cmd in ("PING", "PONG") and not _log_ping():
        return

    try:
        with _WRITE_LOCK:
            out = _resolve_dir(directory)
            # Reset before writing START_MATCH so all three files describe the
            # same current game. The bot decodes this live frame in memory; it
            # never reads these files back into game state.
            if cmd == "START_MATCH" and _per_match_logs():
                _start_new_match(out)

            _append_jsonl(out / "frames.jsonl", {
                "timestamp": time.time(),
                "bot": _bot_name(),
                "command": cmd,
                "length": len(data),
                # Enough to identify framing without a huge credential dump.
                "hex_prefix": data[:256].hex(),
            })

            if cmd in ("START_MATCH", "MOVE"):
                parsed = decode_frame(data)
                log_name = "start_match.jsonl" if cmd == "START_MATCH" else "events.jsonl"
                _append_jsonl(out / log_name, parsed)
                if cmd == "START_MATCH":
                    hidden = [p for p in parsed["pieces"] if not p["is_open"]]
                    print(f"[WS-CAPTURE] START_MATCH pieces={len(parsed['pieces'])} hidden={len(hidden)}")
                    for p in hidden:
                        print(
                            "[WS-CAPTURE] hidden piece "
                            f"pos={p['position']} sid={p['sid_decoded']} "
                            f"face={p['face_decoded']} raw_face=0x{p['raw_face_byte']:02x}"
                        )
    except OSError as exc:
        _warn_once(f"io:{type(exc).__name__}", f"log write failed: {exc}")
    except Exception as exc:  # defensive: never disturb the bot
        _warn_once(f"logic:{type(exc).__name__}", f"log logic failed: {exc}")


if __name__ == "__main__":
    print("Import log_incoming_frame() and call it from the authorized bot's on_message callback.")
    print("No network connection is created by this helper.")
