#!/usr/bin/env python3
"""Safe WebSocket frame logger for an authorized GameVH bot session.

This does not intercept traffic, bypass TLS, or collect credentials. It is
intended to be called from the bot's on_message callback after websocket-client
has decrypted the WSS frame for the bot itself.
"""
from __future__ import annotations

import json
import os
import struct
import time
from pathlib import Path
from typing import Any

CMD_NAMES = {
    417: "START_MATCH",
    418: "GAMEOVER",
    420: "SET_TURN",
    529: "MOVE",
}


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


def _open_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Do not let a shared-readable log expose game frames.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    return os.fdopen(fd, "a", encoding="utf-8")


def log_incoming_frame(data: bytes, directory: str = "ws_capture") -> None:
    """Log one already-decrypted binary WebSocket message.

    LOGIN frames are intentionally not written because they can contain
    session-related values. Other frames get a bounded hex dump. START_MATCH
    additionally gets a parsed JSON record for raw_face analysis.
    """
    data = bytes(data)
    try:
        cmd, _ = command_id(data)
    except Exception as exc:
        cmd = f"parse_error:{exc}"

    out = Path(directory)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if cmd == "LOGIN":
        print("[WS-CAPTURE] LOGIN received; payload intentionally not logged")
        return

    meta = {
        "timestamp": time.time(),
        "command": cmd,
        "length": len(data),
        # Enough to identify framing without creating a huge credential dump.
        "hex_prefix": data[:256].hex(),
    }
    with _open_log(out / "frames.jsonl") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")

    if cmd == "START_MATCH":
        try:
            parsed = parse_start_match(data)
            with _open_log(out / "start_match.jsonl") as f:
                f.write(json.dumps(parsed, ensure_ascii=False) + "\n")
            hidden = [p for p in parsed["pieces"] if not p["is_open"]]
            print(f"[WS-CAPTURE] START_MATCH pieces={len(parsed['pieces'])} hidden={len(hidden)}")
            for p in hidden:
                print(
                    "[WS-CAPTURE] hidden piece "
                    f"pos={p['position']} sid={p['sid_decoded']} "
                    f"face={p['face_decoded']} raw_face=0x{p['raw_face_byte']:02x}"
                )
        except Exception as exc:
            print(f"[WS-CAPTURE] START_MATCH parse failed: {exc}")


if __name__ == "__main__":
    print("Import log_incoming_frame() and call it from the authorized bot's on_message callback.")
    print("No network connection is created by this helper.")
