#!/usr/bin/env python3
"""
research_coup.py — Nghiên cứu giao thức cờ úp GameVH

Mục đích: Login, vào phòng mystery_xiangqi, gửi các lệnh discovery
(ENTER_PLACE, LIST_ZONE_ROOM, LIST_BET_AMT, GET_TABLE_DATA) và
dump MỌI packet nhận được (hex + parsed) ra file để phân tích.

Không chơi thật — chỉ quan sát và thu thập dữ liệu.

Output: /home/z/my-project/download/coup_research/
  - packets.jsonl     — mọi packet với timestamp + cmd + hex + parsed
  - summary.txt        — bảng tổng kết packet types + count
  - start_matches.jsonl — riêng START_MATCH để dễ compare raw_face ↔ type thật
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
import threading
import time
import traceback
from pathlib import Path

# Reuse infra từ bot
import urllib.request, urllib.parse, http.cookiejar

# ============================================================================
# CONFIG (env vars, có default)
# ============================================================================
USER = os.environ.get("CARO_USER19", "arena7").strip()
PASSWD = os.environ.get("CARO_PASSWD19", "nhat123456").strip()

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/mystery_xiangqi/0"
GAME_ID = 'mystery_xiangqi'
PLACE_PATH = 'Lobby.mystery_xiangqi.0'

OBSERVE_SECONDS = int(os.environ.get("OBSERVE_SECONDS", "90"))

OUTPUT_DIR = Path(os.environ.get("RESEARCH_OUT",
    "/home/z/my-project/download/coup_research"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CMD_NAMES = {
    300: "PONG", 301: "PING", 302: "LOGIN", 303: "ALERT",
    311: "BROADCAST", 314: "SET_CLIENT_MODE", 315: "CONFIG",
    331: "CHAT.SEND", 335: "CHAT.MSG",
    401: "ENTER_PLACE", 405: "CREATE_RULE", 406: "PLAYER_ENTERED", 407: "PLAYER_EXITED",
    408: "QUICK_PLAY", 410: "KICK_PLAYER", 412: "LIST_ZONE_ROOM", 413: "LIST_BET_AMT",
    414: "GET_TABLE_DATA", 416: "SLOT_IN_TABLE_CHANGED",
    417: "START_MATCH", 418: "GAMEOVER", 419: "ENTER_STATE",
    420: "SET_TURN", 434: "SET_READY",
    502: "PLAY", 529: "MOVE", 533: "ASK_DRAW", 534: "SURRENDER", 601: "LOGIN_EX",
}

# ============================================================================
# HTTP SESSION (login + lấy cookie + token)
# ============================================================================
class HTTPSession:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPRedirectHandler()
        )
        self.op.addheaders = [
            ("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/139.0 Safari/537.36"),
            ("Accept-Language", "vi-VN,vi;q=0.9,en;q=0.7"),
        ]

    def get(self, url, timeout=20):
        req = urllib.request.Request(url)
        r = self.op.open(req, timeout=timeout)
        return r.geturl(), r.read().decode("utf-8", "replace")

    def post(self, url, data, timeout=20, headers=None):
        body = urllib.parse.urlencode(data).encode()
        h = dict(self.op.addheaders)
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=body, headers=h)
        r = self.op.open(req, timeout=timeout)
        return r.geturl(), r.read().decode("utf-8", "replace")

    def cookie_str(self):
        return "; ".join(f"{c.name}={c.value}" for c in self.cj)


def fetch_session_info():
    """Trả về (token, nickname, player_id, place_path, cookie_str) hoặc None."""
    s = HTTPSession()
    s.get(LOGIN_URL)
    s.post(LOGIN_URL, data={
        "redirect": "/", "USER_NAME": USER, "PASSWORD": PASSWD,
        "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"
    }, headers={
        "Origin": "https://gamevh.net",
        "Referer": LOGIN_URL,
        "Content-Type": "application/x-www-form-urlencoded"
    })
    _, html = s.get(GAME_URL)
    tm = re.search(r"var\s+token\s*=\s*(-?\d+)", html)
    nm = re.search(r'var\s+currentPlayerNickName\s*=\s*["\']([^"\']+)["\']', html)
    pid = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", html)
    pm = re.search(r'var\s+placePath\s*=\s*["\']([^"\']+)["\']', html)
    if not (tm and nm):
        return None
    return (
        int(tm.group(1)),
        nm.group(1).strip(),
        int(pid.group(1)) if pid else 0,
        pm.group(1) if pm else PLACE_PATH,
        s.cookie_str()
    )


# ============================================================================
# PACKET PARSER — tương tự InboundMessage nhưng dump mọi field
# ============================================================================
class PacketParser:
    def __init__(self, data: bytes):
        self.data = bytes(data)
        self.off = 0
        self.fields = []
        self.cmd = self._read_command()

    def _read_command(self):
        if not self.data:
            return "EMPTY"
        first = self.data[0]
        # Sign bit trong byte đầu: nếu âm (>=128) thì là string cmd
        if first & 0x80:
            n = 256 - first  # độ dài string
            end = 1 + n
            cmd_str = self.data[1:end].decode("ascii", errors="replace")
            self.off = end
            self.fields.append(("cmd_str", cmd_str))
            return cmd_str
        # Numeric command: 2 byte big-endian
        if len(self.data) < 2:
            return "?"
        num = (first << 8) | self.data[1]
        self.off = 2
        return CMD_NAMES.get(num, str(num))

    def try_read_all(self):
        """Cố đọc từng field cho đến hết buffer. Bắt exception khi truncated."""
        while self.off < len(self.data):
            start = self.off
            # Thử đọc byte trước
            try:
                v = struct.unpack_from('>b', self.data, self.off)[0]
                self.off += 1
                self.fields.append((f"byte@{start}", v))
            except struct.error:
                break

    def hex_full(self):
        return self.data.hex()

    def hex_prefix(self, n=256):
        return self.data[:n].hex()

    def to_dict(self):
        return {
            "cmd": self.cmd,
            "length": len(self.data),
            "hex_full": self.hex_full(),
            "fields": [(name, val) for name, val in self.fields],
        }


def parse_known_fields(parser: PacketParser):
    """Parse các field đã biết cho START_MATCH, MOVE, SLOT_IN_TABLE_CHANGED, etc."""
    cmd = parser.cmd
    data = parser.data
    off = parser.off
    parsed = {}

    def rd_byte():
        nonlocal off
        if off + 1 > len(data): return None
        v = data[off]
        off += 1
        return v

    def rd_short():
        nonlocal off
        if off + 2 > len(data): return None
        v = struct.unpack_from('>h', data, off)[0]
        off += 2
        return v

    def rd_ushort():
        nonlocal off
        if off + 2 > len(data): return None
        v = struct.unpack_from('>H', data, off)[0]
        off += 2
        return v

    def rd_int():
        nonlocal off
        if off + 4 > len(data): return None
        v = struct.unpack_from('>i', data, off)[0]
        off += 4
        return v

    def rd_long():
        nonlocal off
        if off + 8 > len(data): return None
        v = struct.unpack_from('>q', data, off)[0]
        off += 8
        return v

    def rd_ascii():
        nonlocal off
        if off + 1 > len(data): return None
        n = data[off]
        off += 1
        if off + n > len(data): return None
        s = data[off:off+n].decode("ascii", errors="replace")
        off += n
        return s

    def rd_string():
        nonlocal off
        if off + 2 > len(data): return None
        n = struct.unpack_from('>h', data, off)[0]
        off += 2
        if off + n*2 > len(data): return None
        s = data[off:off+n*2].decode("utf-16-be", errors="replace")
        off += n*2
        return s

    try:
        if cmd == "START_MATCH":
            player_count = rd_byte()
            parsed["player_count"] = player_count
            players = []
            for _ in range(player_count or 0):
                slot = rd_byte()
                pid = rd_int()
                players.append({"slot": slot, "player_id": pid})
            parsed["players"] = players
            piece_count = rd_byte()
            parsed["piece_count"] = piece_count
            pieces = []
            for i in range(piece_count or 0):
                raw_sid = rd_byte()
                raw_face = rd_byte()
                pos = rd_byte()
                is_open = rd_byte()
                # decode sid/face theo decode_piece_id
                sid_str = _decode_piece_id(raw_sid)
                face_str = _decode_piece_id(raw_face)
                face_fen = _decode_face_byte(raw_face)
                pieces.append({
                    "index": i,
                    "raw_sid_byte": raw_sid,
                    "raw_face_byte": raw_face,
                    "raw_face_hex": hex(raw_face),
                    "sid_decoded": sid_str,
                    "face_decoded": face_str,
                    "face_fen": face_fen,
                    "position": pos,
                    "is_open": bool(is_open) if is_open is not None else None,
                })
            parsed["pieces"] = pieces
            parsed["remaining_bytes_after_pieces"] = len(data) - off
            # cố đọc thêm
            extra = []
            for _ in range(10):
                b = rd_byte()
                if b is None: break
                extra.append(b)
            if extra:
                parsed["extra_bytes_after"] = extra
        elif cmd == "MOVE":
            src = rd_byte()
            tgt = rd_byte()
            parsed["source"] = src
            parsed["target"] = tgt
            payload = data[off:]
            parsed["payload_hex"] = payload.hex()
            parsed["payload_len"] = len(payload)
            if len(payload) >= 3 and payload[0] > 0:
                parsed["reveal_byte"] = payload[2]
                parsed["reveal_byte_hex"] = hex(payload[2])
                parsed["revealed_piece_fen"] = _decode_face_byte(payload[2])
        elif cmd == "SLOT_IN_TABLE_CHANGED":
            s = rd_string()
            parsed["str1"] = s
            slot = rd_byte()
            parsed["slot_id"] = slot
            l1 = rd_long()
            l2 = rd_long()
            b1 = rd_byte()
            sh = rd_short()
            asci = rd_ascii()
            b2 = rd_byte()
            b3 = rd_byte()
            pid = rd_long()
            parsed.update({
                "long1": l1, "long2": l2, "byte1": b1, "short1": sh,
                "ascii1": asci, "byte2": b2, "byte3": b3, "player_id": pid
            })
        elif cmd == "PLAYER_ENTERED":
            b = rd_byte()
            pid = rd_long()
            name = rd_string()
            parsed.update({"byte1": b, "player_id": pid, "name": name})
        elif cmd in ("ENTER_PLACE",):
            status = rd_byte()
            parsed["status"] = status
            # Cố đọc string
            s = rd_string()
            if s is not None:
                parsed["path_or_msg"] = s
        elif cmd == "LIST_BET_AMT":
            status = rd_byte()
            parsed["status"] = status
            count = rd_byte()
            parsed["count"] = count
            bets = []
            for _ in range(count or 0):
                v = rd_int()
                if v is None: break
                bets.append(v)
            parsed["bet_values"] = bets
        elif cmd == "LOGIN":
            status = rd_byte()
            parsed["status"] = status
            s = rd_string()
            if s is not None:
                parsed["msg"] = s
        elif cmd == "GAMEOVER":
            count = rd_byte()
            parsed["player_count"] = count
            results = []
            for _ in range(count or 0):
                sid = rd_byte()
                res = rd_byte()
                money = rd_long()
                results.append({"slot": sid, "result": res, "money_delta": money})
            parsed["results"] = results
    except Exception as e:
        parsed["__parse_error__"] = str(e)

    return parsed


def _decode_piece_id(raw: int) -> str:
    if raw is None: return None
    val = raw - 256 if raw > 127 else raw
    color = "r" if val >= 0 else "b"
    abs_val = abs(val)
    sub = abs_val & 7
    return f"{color}{abs_val >> 3}{'' if sub == 0 else sub}"


def _decode_face_byte(raw):
    if raw is None: return None
    value = raw - 256 if raw > 127 else raw
    if value == 0: return None
    PIECE_TYPE_TO_FEN = {1: "k", 2: "a", 3: "b", 4: "r", 5: "c", 6: "n", 7: "p"}
    piece = PIECE_TYPE_TO_FEN.get(abs(value) >> 3)
    if not piece: return None
    return piece.upper() if value > 0 else piece


# ============================================================================
# MAIN RESEARCH BOT
# ============================================================================
class ResearchBot:
    def __init__(self, token, nick, pid, place_path, cookie):
        self.token = token
        self.nick = nick
        self.pid = pid
        self.place_path = place_path
        self.cookie = cookie
        self.ws = None
        self.connected = False
        self.logged_in = False
        self.lock = threading.Lock()
        self.packets_file = open(OUTPUT_DIR / "packets.jsonl", "w", encoding="utf-8")
        self.start_match_file = open(OUTPUT_DIR / "start_matches.jsonl", "w", encoding="utf-8")
        self.packet_counts = {}
        self.start_time = time.time()

    def log(self, msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def connect(self):
        try:
            import websocket
        except ImportError:
            self.log("❌ Cài đặt: pip install websocket-client")
            sys.exit(1)
        self.ws = websocket.WebSocketApp(
            WS_URL, cookie=self.cookie,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            header={"Origin": "https://gamevh.net"}
        )
        t = threading.Thread(target=self.ws.run_forever,
                              kwargs={"ping_interval": 30, "ping_timeout": None},
                              daemon=True)
        t.start()
        for _ in range(25):
            if self.connected: break
            time.sleep(0.2)
        return self.connected

    def _on_open(self, ws):
        self.connected = True
        self.log(f"✅ WS Connected. Sending LOGIN (nick={self.nick}, token={self.token})")
        self._send_login()

    def _on_message(self, ws, message):
        if not isinstance(message, bytes):
            return
        self._handle_packet(message)

    def _on_error(self, ws, error):
        self.log(f"❌ WS Error: {type(error).__name__}: {error}")

    def _on_close(self, ws, code, msg):
        self.log(f"🔌 WS Closed (code={code})")
        self.connected = False

    # ---------- PACKET HANDLING ----------
    def _handle_packet(self, data: bytes):
        parser = PacketParser(data)
        parsed = parse_known_fields(parser)
        cmd = parser.cmd
        ts = time.time()
        record = {
            "ts": ts,
            "elapsed": round(ts - self.start_time, 2),
            "cmd": cmd,
            "length": len(data),
            "hex_full": data.hex(),
            "parsed": parsed,
        }
        with self.lock:
            self.packets_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.packets_file.flush()
            self.packet_counts[cmd] = self.packet_counts.get(cmd, 0) + 1
        # Log ngắn cho mỗi packet
        short = self._short_summary(cmd, parsed, len(data))
        self.log(f"← {cmd} ({len(data)}B) {short}")
        # Lưu riêng START_MATCH để compare raw_face vs type thật
        if cmd == "START_MATCH":
            with self.lock:
                self.start_match_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                self.start_match_file.flush()
        # Xử lý mờ cho LOGIN response
        if cmd == "LOGIN" and not self.logged_in:
            self.logged_in = True
            status = parsed.get("status")
            if status == 0:
                self.log("✅ Login OK — sending ENTER_PLACE")
                threading.Thread(target=self._delayed_enter_place, daemon=True).start()
            else:
                self.log(f"❌ Login failed (status={status})")

    def _short_summary(self, cmd, parsed, length):
        if cmd == "START_MATCH":
            pcs = parsed.get("pieces", [])
            hidden = sum(1 for p in pcs if not p.get("is_open"))
            return f"players={parsed.get('player_count')} pieces={len(pcs)} hidden={hidden}"
        if cmd == "MOVE":
            return f"src={parsed.get('source')} tgt={parsed.get('target')} reveal={parsed.get('revealed_piece_fen')}"
        if cmd == "PLAYER_ENTERED":
            return f"pid={parsed.get('player_id')} name={parsed.get('name')!r}"
        if cmd == "SLOT_IN_TABLE_CHANGED":
            return f"slot={parsed.get('slot_id')} pid={parsed.get('player_id')}"
        if cmd == "LOGIN":
            return f"status={parsed.get('status')}"
        if cmd == "ENTER_PLACE":
            return f"status={parsed.get('status')}"
        if cmd == "LIST_BET_AMT":
            return f"status={parsed.get('status')} count={parsed.get('count')} bets={parsed.get('bet_values')}"
        return ""

    # ---------- SEND ----------
    def _send_bytes(self, data: bytes):
        if self.ws and self.connected:
            try:
                self.ws.send(data, opcode=0x2)
            except Exception as e:
                self.log(f"❌ Send error: {e}")

    def _pack_cmd(self, cmd, payload: bytes = b"") -> bytes:
        """Đóng gói command: numeric (2 byte) hoặc string (1 byte len + bytes)."""
        if isinstance(cmd, str):
            cmd_bytes = cmd.encode("ascii")
            return bytes([(-len(cmd_bytes)) & 0xFF]) + cmd_bytes + payload
        elif isinstance(cmd, int):
            return struct.pack(">H", cmd) + payload
        return b""

    def _pack_byte(self, v): return struct.pack(">b", v)
    def _pack_int(self, v): return struct.pack(">i", v)
    def _pack_ascii(self, s):
        b = s.encode("ascii")[:255]
        return struct.pack(">b", len(b)) + b
    def _pack_string(self, s):
        b = s.encode("utf-16-be")
        return struct.pack(">h", len(b) // 2) + b

    def _send_login(self):
        payload = (
            self._pack_ascii(self.nick)
            + self._pack_int(self.token)
            + self._pack_ascii("5.0.2")
            + self._pack_ascii("")
            + self._pack_ascii(GAME_ID)
            + self._pack_byte(1)
        )
        self._send_bytes(self._pack_cmd("LOGIN", payload))

    def _delayed_enter_place(self, delay=0.5):
        time.sleep(delay)
        self.log(f"→ ENTER_PLACE path={self.place_path}")
        payload = (
            self._pack_ascii(self.place_path)
            + self._pack_string("")
            + self._pack_byte(1)
        )
        self._send_bytes(self._pack_cmd("ENTER_PLACE", payload))
        # Sau 2s, gửi LIST_ZONE_ROOM và LIST_BET_AMT
        threading.Thread(target=self._delayed_discovery, daemon=True).start()

    def _delayed_discovery(self):
        time.sleep(2.0)
        self.log("→ LIST_BET_AMT")
        self._send_bytes(self._pack_cmd("LIST_BET_AMT"))
        time.sleep(1.0)
        self.log("→ LIST_ZONE_ROOM")
        self._send_bytes(self._pack_cmd("LIST_ZONE_ROOM"))
        time.sleep(2.0)
        self.log("→ GET_TABLE_DATA (no params — discovery)")
        self._send_bytes(self._pack_cmd("GET_TABLE_DATA"))

    # ---------- CLEANUP ----------
    def close(self):
        try:
            self.packets_file.close()
            self.start_match_file.close()
        except Exception:
            pass
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        # Viết summary
        summary_path = OUTPUT_DIR / "summary.txt"
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(f"Research session summary\n")
            f.write(f"User: {USER}\n")
            f.write(f"Nickname: {self.nick}\n")
            f.write(f"Player ID: {self.pid}\n")
            f.write(f"Duration: {time.time() - self.start_time:.1f}s\n")
            f.write(f"Output dir: {OUTPUT_DIR}\n\n")
            f.write(f"=== Packet type counts (total {sum(self.packet_counts.values())}) ===\n")
            for cmd, n in sorted(self.packet_counts.items(), key=lambda x: -x[1]):
                f.write(f"  {cmd:30s} {n}\n")
        self.log(f"📊 Summary saved to {summary_path}")


# ============================================================================
# MAIN
# ============================================================================
def main():
    print(f"=" * 60)
    print(f"  Cờ Úp Protocol Research — arena7")
    print(f"  Observe {OBSERVE_SECONDS}s, capture all packets")
    print(f"  Output: {OUTPUT_DIR}")
    print(f"=" * 60)

    info = fetch_session_info()
    if not info:
        print("❌ Login failed")
        sys.exit(1)
    token, nick, pid, place_path, cookie = info
    print(f"✅ HTTP login OK | nick={nick} pid={pid} place={place_path}")

    bot = ResearchBot(token, nick, pid, place_path, cookie)
    if not bot.connect():
        print("❌ WS connect failed")
        sys.exit(1)

    print(f"\n⏱️  Observing for {OBSERVE_SECONDS}s...\n")
    end_time = time.time() + OBSERVE_SECONDS
    try:
        while time.time() < end_time:
            time.sleep(1.0)
            if not bot.connected:
                print("⚠️  WS disconnected — reconnecting...")
                bot.connect()
    except KeyboardInterrupt:
        print("\n⏹️  Interrupted by user")
    finally:
        bot.close()
        print(f"\n✅ Done. Artifacts at: {OUTPUT_DIR}")
        print(f"   - packets.jsonl  (mọi packet)")
        print(f"   - start_matches.jsonl (riêng START_MATCH)")
        print(f"   - summary.txt (thống kê)")


if __name__ == "__main__":
    main()
