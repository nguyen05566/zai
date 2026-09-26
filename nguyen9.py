#!/usr/bin/env python3
"""
Xiangqi Bot (gamevh.net) — mainline Pikafish (mistboard level 8)
Tài khoản: 
"""

import struct
import threading
import time
import sys
import os
import requests
import re
import subprocess
import signal
import atexit
import tempfile
import json
import random

# ==================== TÀI KHOẢN (KHÔNG CẦN COOKIE) ====================
CARO_USER_DIRECT = "nguyen9"
CARO_PASSWD_DIRECT = "nhat123456"

def _clean_env(val, default):
    if val and str(val).strip():
        return str(val).strip()
    return default

USER = _clean_env(os.environ.get("ZARO3_USER"), CARO_USER_DIRECT)
PASSWD = _clean_env(os.environ.get("ZARO3_PASSWD"), CARO_PASSWD_DIRECT)

COOKIE = ""

_venv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'venv', 'lib')
for _py_ver in ['python3.12', 'python3.13', 'python3.11']:
    _candidate = os.path.join(_venv_path, _py_ver, 'site-packages')
    if os.path.isdir(_candidate):
        sys.path.insert(0, _candidate)
        break

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/xiangqi/0"
CURRENT_PLAYER_NICKNAME = USER
CURRENT_PLAYER_ID = 0
TOKEN = 0
GAME_ID = 'xiangqi'

# ==================== 8 LOBBY CỜ TƯỚNG ====================
LOBBY_LIST = [f"Lobby.xiangqi.{i}" for i in range(8)]
_lobby_idx = 0
PLACE_PATH = LOBBY_LIST[_lobby_idx]

# ==================== ENGINE CONFIG ====================
ENGINE_MULTIPV = 1
ENGINE_MULTIPV_FIXED_PAWN = 4
FIXED_PAWN_NO_CAPTURE = True

PIKAFISH_LEVEL_8_NODES = 3_000_000
PIKAFISH_LEVEL_8_MOVETIME_MS = 4_000
PIKAFISH_FIXED_PAWN_MOVETIME_MS = 3_000

MIN_MOVE_SECONDS = 2.0

KICK_MODE = "when_lose"
KICK_DELAY = 5.0

# ==================== TẠO BÀN CỐ ĐỊNH 5000 XU ====================
# ★ CHỈ TẠO BÀN — KHÔNG TÌM BÀN, KHÔNG QUICK_PLAY
CREATE_BET_VALUE = 2000
BOT_BET_XU = CREATE_BET_VALUE

# Không dùng QUICK_PLAY nữa
QUICK_PLAY_MAX_ATTEMPTS = 0
BOT_USE_CREATE_TABLE = True

BOT_MATCH_DURATION = '5'
BOT_TURN_DURATION = '30'
BOT_ACC_DURATION = '0'
BOT_BLOCK_SOFTWARE = '0'

SIT_ALONE_TIMEOUT = 200.0
ENTER_FAIL_TIMEOUT = 200.0

CREATE_RETRY_DELAY = 5.0

VN_TEN_DAU = [
    "Tuấn", "Minh", "Đức", "Hoàng", "Huy", "Hùng", "Dũng", "Cường", "Long", "Nam",
    "Sơn", "Hải", "Phong", "Thắng", "Trung", "Kiên", "Quân", "Thanh", "Đạt", "Khoa",
    "Phúc", "Nghĩa", "Trọng", "Quang", "Bảo", "Khánh", "Hiếu", "Lâm", "Trí", "Thịnh",
    "Lộc", "Phát", "Tiến", "Việt", "Duy", "Vĩnh", "Phước", "Bình", "Đăng", "Tùng",
    "Vũ", "An", "Bách", "Công", "Đại", "Hiệp", "Hòa", "Khai", "Khang", "Khôi",
    "Mạnh", "Nhật", "Phi", "Phú", "Sang", "Tài", "Tâm", "Thái", "Thuận", "Toàn",
    "Triết", "Từ", "Linh", "Trang", "Lan", "Mai", "Hương", "Ngọc", "Thảo", "Vy",
    "Hân", "Châu", "Nhi", "Yến", "Quỳnh", "Ngân", "Trâm", "Phương", "Huyền", "Thủy",
    "Hằng", "Nga", "Tuyết", "Loan", "Oanh", "Bích", "Diễm", "Kiều", "Liên", "Giang",
    "Quyên", "Như", "Hà", "Xuân", "My", "Thu", "Anh", "Hiền", "Huế", "Ly",
    "Nhung", "Thương", "Tiên", "Trinh", "Trúc", "Uyên", "Vân"
]

VN_TEN_KHONG_DAU = [
    "Tuan", "Minh", "Duc", "Hoang", "Huy", "Hung", "Dung", "Cuong", "Long", "Nam",
    "Son", "Hai", "Phong", "Thang", "Trung", "Kien", "Quan", "Thanh", "Dat", "Khoa",
    "Phuc", "Nghia", "Trong", "Quang", "Bao", "Khanh", "Hieu", "Lam", "Tri", "Thinh",
    "Loc", "Phat", "Tien", "Viet", "Duy", "Vinh", "Phuoc", "Binh", "Dang", "Tung",
    "Vu", "An", "Bach", "Cong", "Dai", "Hiep", "Hoa", "Hung", "Khai", "Khang",
    "Khoi", "Manh", "Nhat", "Phi", "Phu", "Sang", "Tai", "Tam", "Thai", "Thuan",
    "Toan", "Triet", "Tu", "Linh", "Trang", "Lan", "Mai", "Huong", "Ngoc", "Thao",
    "Vy", "Han", "Chau", "Nhi", "Yen", "Quynh", "Ngan", "Tram", "Phuong", "Huyen",
    "Thuy", "Hang", "Nga", "Tuyet", "Loan", "Oanh", "Bich", "Diem", "Kieu", "Lien",
    "Giang", "Quyen", "Nhu", "Ha", "Xuan", "My", "Thu", "Anh", "Dung", "Hien",
    "Hoa", "Hue", "Ly", "Nhung", "Thu", "Thuong", "Thuy", "Tien", "Trinh", "Truc", "Uyen", "Van"
]

_IDENTITY_SYNCED = False

def generate_dotted_full_name():
    name = random.choice(VN_TEN_DAU if random.choice([True, False]) else VN_TEN_KHONG_DAU)
    if len(name) >= 2:
        pos = random.randint(1, len(name) - 1)
        name = name[:pos] + "." + name[pos:]
    return name

def sync_profile_name(session):
    try:
        edit_url = "https://gamevh.net/com/ftl/game/profile/update_profile.jsp"
        page = session.get(edit_url, timeout=15, allow_redirects=True)
        form_match = re.search(r'(?is)<form\b[^>]*name=["\']InputForm0["\'][^>]*>.*?</form>', page.text)
        if not form_match:
            return
        form = form_match.group(0)
        open_tag = re.search(r'(?is)<form\b[^>]*>', form).group(0)
        action_match = re.search(r'action=["\']([^"\']+)["\']', open_tag)
        action = action_match.group(1) if action_match else edit_url
        if not action.startswith('http'):
            from urllib.parse import urljoin
            action = urljoin(edit_url, action)

        data = {}
        for tag in re.findall(r'(?is)<input\b[^>]*>', form):
            nm = re.search(r'name=["\']([^"\']+)["\']', tag)
            val = re.search(r'value=["\']([^"\']*)["\']', tag)
            if nm:
                data[nm.group(1)] = val.group(1) if val else ''

        old_full_name = data.get('FULL_NAME', '')
        new_full_name = generate_dotted_full_name()
        data['FULL_NAME'] = new_full_name
        data['OLD_PASSWORD'] = PASSWD
        data['SAVE'] = '\uf046'

        session.post(
            action, timeout=15, data=data,
            headers={'Origin': 'https://gamevh.net',
                     'Referer': page.url,
                     'Content-Type': 'application/x-www-form-urlencoded'},
            allow_redirects=True)
        print(f"[PROFILE] 👤 Đổi tên hiển thị: '{old_full_name}' -> '{new_full_name}'")
    except Exception as e:
        print(f"[PROFILE] Lỗi cập nhật tên hiển thị: {e}")


def sync_random_avatar(session):
    try:
        profile_url = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
        before = session.get(profile_url, timeout=15)
        m = re.search(r'/avatar/builtin(\d+)\.(?:webp|png|jpg)', before.text, re.I)
        old_avatar = int(m.group(1)) if m else None

        catalog = []
        seen = set()
        pattern = re.compile(
            r'''buyAvatar\(\s*(["\']?)(\d+)\1\s*,\s*(["\'])(.*?)\3\s*,\s*(["\']?)([\d,.]+)\5\s*\)''',
            re.I | re.S)
        for category in range(1, 7):
            url = ("https://gamevh.net/com/ftl/game/profile/"
                   f"avatar_by_category.jsp?excludeLayout=true&category_id={category}")
            page = session.get(url, timeout=15)
            for match in pattern.finditer(page.text):
                avatar_id = int(match.group(2))
                if avatar_id in seen:
                    continue
                seen.add(avatar_id)
                catalog.append(avatar_id)

        choices = [a for a in catalog if a != old_avatar]
        if not choices:
            print("[PROFILE] 🎭 Không tải được catalog avatar")
            return

        selected = random.choice(choices)
        update_url = ("https://gamevh.net/com/ftl/game/profile/update_avatar.jsp"
                      f"?pk={selected}&redirect=/")
        session.post(update_url, timeout=20,
                     headers={"Origin": "https://gamevh.net",
                              "Referer": "https://gamevh.net/com/ftl/game/profile/avatar.jsp"},
                     allow_redirects=True)

        after = session.get(profile_url, timeout=15)
        m2 = re.search(r'/avatar/builtin(\d+)\.(?:webp|png|jpg)', after.text, re.I)
        new_avatar = int(m2.group(1)) if m2 else None
        print(f"[PROFILE] 🎭 Avatar: builtin{old_avatar} -> builtin{new_avatar}")
    except Exception as e:
        print(f"[PROFILE] Lỗi đổi avatar: {e}")

def is_block_software_message(raw_bytes):
    try:
        idx = raw_bytes.find(b"blockSoftware")
        if idx != -1:
            snippet = raw_bytes[idx:idx+40]
            if b"1" in snippet or b"true" in snippet.lower():
                return True
    except Exception:
        pass
    return False

ACTIVE_TABLES_FILE = os.path.join(tempfile.gettempdir(), "zaro_active_tables.json")

def get_active_bot_tables():
    try:
        if not os.path.exists(ACTIVE_TABLES_FILE):
            return {}
        with open(ACTIVE_TABLES_FILE, 'r') as f:
            content = f.read().strip()
            if not content:
                return {}
            data = json.loads(content)
        now = time.time()
        return {tp: info for tp, info in data.items() if isinstance(info, dict) and now - info.get("timestamp", 0) < 180}
    except Exception:
        return {}

def register_bot_table(table_path, user):
    if not table_path: return
    try:
        data = get_active_bot_tables()
        data[table_path] = {"user": user, "timestamp": time.time(), "pid": os.getpid()}
        with open(ACTIVE_TABLES_FILE, 'w') as f:
            json.dump(data, f)
    except Exception: pass

def unregister_bot_table(table_path):
    if not table_path: return
    try:
        data = get_active_bot_tables()
        if table_path in data:
            data.pop(table_path, None)
            with open(ACTIVE_TABLES_FILE, 'w') as f:
                json.dump(data, f)
    except Exception: pass

def fetch_session_info():
    global COOKIE, TOKEN, CURRENT_PLAYER_NICKNAME, CURRENT_PLAYER_ID, PLACE_PATH, _IDENTITY_SYNCED
    try:
        session = requests.Session()
        ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
        session.headers.update({
            "User-Agent": ua,
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
        })

        session.get(LOGIN_URL, timeout=20)

        resp = session.post(
            LOGIN_URL, timeout=20,
            data={"redirect": "/", "USER_NAME": USER, "PASSWORD": PASSWD,
                  "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
            headers={"Origin": "https://gamevh.net",
                     "Referer": LOGIN_URL,
                     "Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=True)
        if "login.jsp" in resp.url:
            print(f"[SESSION] Đăng nhập thất bại: {resp.url}")
            return False

        if not _IDENTITY_SYNCED:
            _IDENTITY_SYNCED = True
            sync_profile_name(session)
            sync_random_avatar(session)

        game_resp = session.get(GAME_URL, timeout=20)
        page_html = game_resp.text

        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", page_html)
        if not tm:
            print("[SESSION] Không tìm thấy token")
            return False
        TOKEN = int(tm.group(1))

        nm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", page_html)
        if not nm:
            print("[SESSION] Không tìm thấy currentPlayerNickName")
            return False
        CURRENT_PLAYER_NICKNAME = nm.group(1).strip()

        pid = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", page_html)
        if pid:
            CURRENT_PLAYER_ID = int(pid.group(1))

        COOKIE = "; ".join(f"{k}={v}" for k, v in session.cookies.items())

        if CURRENT_PLAYER_NICKNAME != USER:
            print(f"[SESSION] Cảnh báo: nickname server={CURRENT_PLAYER_NICKNAME!r} khác USER={USER!r}")
        print(f"[SESSION] Login OK | Token: {TOKEN} | NickName: {CURRENT_PLAYER_NICKNAME} | PlayerID: {CURRENT_PLAYER_ID}")
        return True
    except Exception as e:
        print(f"[SESSION] Lỗi đăng nhập: {e}")
        return False

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

class Conn:
    def pack(self, cmd, data=b''):
        result = bytearray()
        if isinstance(cmd, str):
            cmd_bytes = cmd.encode('ascii')
            result.append((-len(cmd_bytes)) & 0xFF)
            result.extend(cmd_bytes)
        elif isinstance(cmd, int):
            result.extend(struct.pack('>H', cmd))
        result.extend(data)
        return bytes(result)
    def pack_byte(self, value): return struct.pack('>b', value)
    def pack_int(self, value): return struct.pack('>i', value)
    def pack_ascii(self, value):
        encoded = value.encode('ascii')[:255]
        return struct.pack('>b', len(encoded)) + encoded
    def pack_string(self, value):
        encoded = value.encode('utf-16-be')
        return struct.pack('>h', len(encoded) // 2) + encoded

class InboundMessage:
    def __init__(self, data):
        self.data = bytes(data)
        self.offset = 0
        self.command = self._parse_command()
    def _parse_command(self):
        length = self.read_byte()
        if length < 0:
            cmd = self.data[self.offset:self.offset + (-length)].decode('ascii', errors='replace')
            self.offset += (-length)
            return cmd
        else:
            next_byte = self.data[self.offset] & 0xFF
            self.offset += 1
            return CMD_NAMES.get((length << 8) | next_byte, str((length << 8) | next_byte))
    def read_byte(self):
        val = struct.unpack_from('>b', self.data, self.offset)[0]
        self.offset += 1
        return val
    def read_short(self):
        val = struct.unpack_from('>h', self.data, self.offset)[0]
        self.offset += 2
        return val
    def read_int(self):
        val = struct.unpack_from('>i', self.data, self.offset)[0]
        self.offset += 4
        return val
    def read_long(self):
        val = struct.unpack_from('>q', self.data, self.offset)[0]
        self.offset += 8
        return val
    def read_ascii(self):
        length = self.read_byte()
        if length < 0: length += 256
        s = self.data[self.offset:self.offset + length].decode('ascii', errors='replace')
        self.offset += length
        return s
    def read_string(self):
        char_count = self.read_short()
        s = self.data[self.offset:self.offset + char_count * 2].decode('utf-16-be', errors='replace')
        self.offset += char_count * 2
        return s

STANDARD_PAWN_POSITIONS = set()
for _c in [0, 2, 4, 6, 8]:
    STANDARD_PAWN_POSITIONS.add(6 * 9 + _c)
    STANDARD_PAWN_POSITIONS.add(3 * 9 + _c)

class XiangqiBoardTracker:
    INITIAL_FEN = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w"
    def __init__(self): self.reset()
    def reset(self):
        self.fen = self.INITIAL_FEN
        self.move_history = []
        self.base_fen = self.INITIAL_FEN.split(' ')[0]
        self.base_side = 'w'
        self.moves_since_base = []
        self.my_slot_id = -1
        self.first_turn_slot_id = 0
        self.is_my_turn = False
        self.is_playing = False
        self.is_red = None
    def pos_to_engine_move(self, source_pos, target_pos):
        s_col, s_row = source_pos % 9, source_pos // 9
        t_col, t_row = target_pos % 9, target_pos // 9
        return f"{chr(ord('a') + s_col)}{s_row}{chr(ord('a') + t_col)}{t_row}"
    def engine_move_to_pos(self, engine_move):
        s_col, s_row = ord(engine_move[0]) - ord('a'), int(engine_move[1])
        t_col, t_row = ord(engine_move[2]) - ord('a'), int(engine_move[3])
        return s_row * 9 + s_col, t_row * 9 + t_col
    @staticmethod
    def _apply_move_to_fen(board_fen, move):
        try:
            grid = []
            for r in board_fen.split('/'):
                line = []
                for ch in r:
                    if ch.isdigit(): line.extend(['.'] * int(ch))
                    else: line.append(ch)
                if len(line) != 9: return None
                grid.append(line)
            if len(grid) != 10: return None
            s_col, s_rank = ord(move[0]) - 97, int(move[1])
            t_col, t_rank = ord(move[2]) - 97, int(move[3])
            s_row, t_row = 9 - s_rank, 9 - t_rank
            if not (0 <= s_row < 10 and 0 <= t_row < 10 and 0 <= s_col < 9 and 0 <= t_col < 9):
                return None
            piece = grid[s_row][s_col]
            if piece == '.': return None
            grid[t_row][t_col] = piece; grid[s_row][s_col] = '.'
            rows = []
            for line in grid:
                out, empty = "", 0
                for c in line:
                    if c == '.': empty += 1
                    else:
                        if empty: out += str(empty); empty = 0
                        out += c
                if empty: out += str(empty)
                rows.append(out)
            return '/'.join(rows)
        except Exception:
            return None

    def get_current_fen(self):
        my_side = 'w' if self.is_red else 'b'
        turn_side = my_side if self.is_my_turn else ('b' if my_side == 'w' else 'w')
        n = len(self.moves_since_base)
        expected = self.base_side if n % 2 == 0 else ('b' if self.base_side == 'w' else 'w')

        if expected == turn_side:
            return f"{self.base_fen} {self.base_side}", self.moves_since_base

        cur = self.base_fen
        for mv in self.moves_since_base:
            nxt = self._apply_move_to_fen(cur, mv)
            if nxt is None:
                print(f"[BOARD] ⚠️ Không áp được nước {mv}, giữ nguyên mốc cũ")
                return f"{self.base_fen} {self.base_side}", self.moves_since_base
            cur = nxt
        print(f"[BOARD] Đối phương bỏ lượt -> chốt mốc thế cờ mới, bên đi = {turn_side}")
        self.base_fen = cur
        self.base_side = turn_side
        self.moves_since_base = []
        return f"{cur} {turn_side}", []

    def set_base(self, board_fen, side='w'):
        self.fen = board_fen
        self.base_fen = board_fen.split(' ')[0] if ' ' in board_fen else board_fen
        self.base_side = side
        self.moves_since_base = []
        self.move_history = []

    def record_move(self, mv):
        self.move_history.append(mv)
        self.moves_since_base.append(mv)

    def set_my_slot(self, slot_id, first_turn_slot_id):
        self.my_slot_id = slot_id
        self.first_turn_slot_id = first_turn_slot_id
        self.is_red = (self.my_slot_id == self.first_turn_slot_id)

class TrendAnalyzer:
    def __init__(self):
        self.pv_ram_cache = {}
        self.enabled = True
        self.info_regex = re.compile(r"info .* score cp (-?\d+) .* pv (.+)")
        self.mate_regex = re.compile(r"info .* score mate (-?\d+) .* pv (.+)")

    def clear(self):
        self.pv_ram_cache.clear()

    def parse_line(self, line_str):
        if not self.enabled:
            return
        mate_match = self.mate_regex.search(line_str)
        if mate_match:
            mate_score = int(mate_match.group(1))
            pv_line = mate_match.group(2).split()
            if pv_line:
                first_move = pv_line[0]
                self.pv_ram_cache[first_move] = {
                    "current_score": 99999 if mate_score > 0 else -99999,
                    "mate_in": mate_score,
                    "pv_chain": pv_line
                }
                return

        match = self.info_regex.search(line_str)
        if match:
            score = int(match.group(1))
            pv_line = match.group(2).split()
            if len(pv_line) >= 1:
                first_move = pv_line[0]
                self.pv_ram_cache[first_move] = {
                    "current_score": score,
                    "mate_in": None,
                    "pv_chain": pv_line
                }

    def select_best_trend_move(self):
        if not self.pv_ram_cache:
            return None
        for move, data in self.pv_ram_cache.items():
            if data["mate_in"] is not None and data["mate_in"] > 0:
                print(f"[RAM-MATE] 🔥 Sát cục tuyệt đối! Dứt điểm ngay: {move}")
                return move
        best_move = None
        avg_score = sum(d["current_score"] for d in self.pv_ram_cache.values()) / len(self.pv_ram_cache)
        is_negative = avg_score < 0
        if is_negative:
            max_recovery = -999999
            for move, data in self.pv_ram_cache.items():
                if data["current_score"] > max_recovery:
                    max_recovery = data["current_score"]
                    best_move = move
            print(f"[RAM-LEARN] Đang lép vế ({int(avg_score)}). Chọn nước phòng thủ tốt nhất: {best_move}")
        else:
            max_growth = -999999
            for move, data in self.pv_ram_cache.items():
                if data["current_score"] > max_growth:
                    max_growth = data["current_score"]
                    best_move = move
            print(f"[RAM-LEARN] Đang ưu thế (+{int(avg_score)}). Chọn nước tăng điểm tốt nhất: {best_move}")
        return best_move

    def top_moves(self, n=5):
        items = sorted(self.pv_ram_cache.items(),
                       key=lambda kv: kv[1].get("current_score", -99999),
                       reverse=True)
        return [mv for mv, _ in items[:n]]


class PikafishBot:
    def __init__(self):
        self.conn = Conn()
        self.board = XiangqiBoardTracker()
        self.trend_analyzer = TrendAnalyzer()
        self.engine = None
        self.ws = None
        self.connected = False
        self.logged_in = False
        self.in_game = False
        self._joining_table = False
        self._last_quick_play_time = 0
        self._QUICK_PLAY_INTERVAL = 3.0
        self.ROOM_LIST = ["0", "1", "2", "3"]
        self.player_names = {}
        _bot_num = re.search(r"\d+", USER)
        _offset = int(_bot_num.group(0)) if _bot_num else 0
        self._search_room_idx = _offset % len(self.ROOM_LIST)
        self._search_bet_idx = 0
        self._quick_play_attempts = 0
        self._current_lobby_idx = _offset % len(LOBBY_LIST)
        self._lobbies_tried = 0
        self._sit_alone_since = None
        self._table_created_by_me = False
        self.bet_amts = []
        self._resolved_bet_id = None
        self._bet_amts_loaded = False
        self.fixed_pawn_positions = set()
        self.last_action_timestamp = time.time()
        self.last_recv_timestamp = time.time()
        self._thinking = False
        self._played_this_turn = False
        self._turn_started_at = 0.0
        self._last_play_sent_at = 0.0
        self.turn_timeout = 0
        self.slot_players = {}
        self._pending_kick_id = None
        self._table_path = None
        self._table_path_ts = 0.0
        self._reconnect_streak = 0
        self._connected_since = 0.0
        self._enter_fail_at = 0.0
        self._banned_moves = set()
        self._last_sent_move = None
        self._play_reject_count = 0
        self._latest_bestmove = None
        self._mate_status = None
        self._mate_regex = re.compile(r"score mate (-?\d+)")
        self._score_regex = re.compile(r"depth (\d+).*score (cp|mate) (-?\d+)")
        self._last_score = "?"
        self._last_depth = "?"
        self._engine_multipv = ENGINE_MULTIPV
        self._last_create_attempt = 0.0
        self._create_pending = False
        self._init_engine()

    # ==================== LOBBY MANAGEMENT ====================
    def _current_lobby(self) -> str:
        return LOBBY_LIST[self._current_lobby_idx % len(LOBBY_LIST)]

    def _advance_lobby(self) -> str:
        global PLACE_PATH
        self._current_lobby_idx = (self._current_lobby_idx + 1) % len(LOBBY_LIST)
        self._lobbies_tried += 1
        new_lobby = self._current_lobby()
        PLACE_PATH = new_lobby
        print(f"[LOBBY] 🔄 Chuyển sang lobby '{new_lobby}' "
              f"(đã dò {self._lobbies_tried}/{len(LOBBY_LIST)} lobby)")
        self._quick_play_attempts = 0
        self._search_room_idx = 0
        self._search_bet_idx = 0
        self.send_enter_place(new_lobby)
        return new_lobby

    def _reset_lobby_cycle(self):
        self._lobbies_tried = 0
        self._quick_play_attempts = 0

    # ==================== ENGINE INIT ====================
    def _init_engine(self):
        _script_dir = os.path.dirname(os.path.abspath(__file__))
        possible_paths = [
            os.environ.get("MISTBOARD_PIKAFISH_XIANGQI_PATH"),
            os.path.join(_script_dir, "pikafish"),
            os.path.join(_script_dir, "pikafish_x86_64"),
            os.path.expanduser("~/pikafish"),
            os.path.expanduser("~/Android/pikafish-armv8"),
            "/data/data/com.termux/files/home/pikafish",
            "/usr/local/bin/pikafish",
            "/app/bin/pikafish",
        ]
        possible_paths = [p for p in possible_paths if p]
        pikafish_path = next((p for p in possible_paths if os.path.isfile(p) and os.access(p, os.X_OK)), None)
        if not pikafish_path:
            print("[ENGINE] ❌ KHÔNG TÌM THẤY pikafish! Đã tìm ở: " + ", ".join(possible_paths))
            return

        nnue_candidates = [
            os.environ.get("MISTBOARD_PIKAFISH_XIANGQI_NET"),
            os.path.join(_script_dir, "pikafish.nnue"),
            os.path.join(os.path.dirname(pikafish_path), "pikafish.nnue"),
            os.path.expanduser("~/pikafish.nnue"),
        ]
        nnue_candidates = [p for p in nnue_candidates if p]
        nnue_path = next((p for p in nnue_candidates if os.path.isfile(p)), None)
        if not nnue_path:
            print("[ENGINE] ❌ KHÔNG TÌM THẤY pikafish.nnue! Đã tìm ở: " + ", ".join(nnue_candidates))
            return
        print(f"[ENGINE] 🎯 Pikafish binary = {pikafish_path}")
        print(f"[ENGINE] 🎯 Pikafish NNUE net = {nnue_path}")

        try:
            self._engine_proc = subprocess.Popen(
                [pikafish_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1
            )

            def consume_stderr(proc):
                try:
                    while proc.poll() is None:
                        if not proc.stderr.readline(): break
                except: pass
            threading.Thread(target=consume_stderr, args=(self._engine_proc,), daemon=True).start()

            def consume_stdout_and_filter(proc):
                try:
                    while proc.poll() is None:
                        line = proc.stdout.readline()
                        if not line: break
                        line_str = line.strip()
                        self.trend_analyzer.parse_line(line_str)
                        _m = self._score_regex.search(line_str)
                        if _m:
                            self._last_depth = _m.group(1)
                            self._last_score = ("mate " + _m.group(3)) if _m.group(2) == "mate" \
                                               else f"{int(_m.group(3)):+d}"
                        if "score mate" in line_str:
                            match = self._mate_regex.search(line_str)
                            if match:
                                val = int(match.group(1))
                                if val > 0: self._mate_status = f"WIN_IN_{val}"
                                elif val < 0: self._mate_status = f"LOSE_IN_{abs(val)}"
                        if line_str.startswith("bestmove"):
                            self._latest_bestmove = line_str
                except: pass
            threading.Thread(target=consume_stdout_and_filter, args=(self._engine_proc,), daemon=True).start()

            self._fsf_cmd("uci")
            _threads = max(1, min(4, (os.cpu_count() or 2) - 1))
            self._fsf_cmd(f"setoption name Threads value {_threads}")
            self._fsf_cmd("setoption name Hash value 256")
            self._fsf_cmd(f"setoption name MultiPV value {ENGINE_MULTIPV}")
            self._engine_multipv = ENGINE_MULTIPV
            self._fsf_cmd(f"setoption name EvalFile value {nnue_path}")
            self._fsf_cmd("setoption name UseNNUE value true")
            self._fsf_cmd("ucinewgame")
            self._fsf_cmd("isready")
            self.engine = True
            print(f"[ENGINE] ✅ Sẵn sàng (mistboard level 8) | Threads={_threads} | MultiPV={ENGINE_MULTIPV}")
        except Exception as e:
            print(f"[ENGINE] ❌ Lỗi khởi tạo: {e}")

    def _fsf_cmd(self, text):
        if getattr(self, '_engine_proc', None) and self._engine_proc.poll() is None:
            self._engine_proc.stdin.write(text + "\n")
            self._engine_proc.stdin.flush()

    def _set_multipv(self, value):
        if getattr(self, '_engine_proc', None) and self._engine_proc.poll() is None:
            if self._engine_multipv != value:
                self._fsf_cmd(f"setoption name MultiPV value {value}")
                self._engine_multipv = value

    # ==================== MOVE SEARCH ====================
    def get_best_move(self, fen, moves, fixed_positions=None, banned_moves=None):
        try:
            if not getattr(self, '_engine_proc', None) or self._engine_proc.poll() is not None:
                return None
            if fixed_positions or banned_moves:
                return self._get_move_avoiding_fixed(fen, moves, fixed_positions, banned_moves)
            self._set_multipv(ENGINE_MULTIPV)
            self.trend_analyzer.clear()
            pos_cmd = f"position fen {fen}"
            if moves: pos_cmd += " moves " + " ".join(moves)
            self._fsf_cmd(pos_cmd)
            self._fsf_cmd(f"go nodes {PIKAFISH_LEVEL_8_NODES} movetime {PIKAFISH_LEVEL_8_MOVETIME_MS}")
            return self._read_bestmove(timeout=5.5)
        except Exception as e:
            print(f"[ENGINE] Lỗi tính toán: {e}")
            return None

    def _read_bestmove(self, timeout=3):
        _go_start = time.time()
        self._latest_bestmove = None
        self._mate_status = None
        while True:
            if self._engine_proc.poll() is not None: return None
            if self._latest_bestmove:
                return self._latest_bestmove
            if time.time() - _go_start > timeout:
                self._fsf_cmd("stop")
                time.sleep(0.1)
                if self._latest_bestmove:
                    return self._latest_bestmove
                break
            time.sleep(0.02)
        return None

    def _get_move_avoiding_fixed(self, fen, moves, fixed_positions, banned_moves=None):
        banned_moves = banned_moves or set()
        self._set_multipv(ENGINE_MULTIPV_FIXED_PAWN)
        time.sleep(0.05)
        try:
            self.trend_analyzer.clear()
            self._latest_bestmove = None
            pos_cmd = f"position fen {fen}"
            if moves: pos_cmd += " moves " + " ".join(moves)
            self._fsf_cmd(pos_cmd)
            self._fsf_cmd(f"go nodes {PIKAFISH_LEVEL_8_NODES} movetime {PIKAFISH_FIXED_PAWN_MOVETIME_MS}")
            _wait_start = time.time()
            while time.time() - _wait_start < 4.5:
                if self._latest_bestmove: break
                time.sleep(0.05)
            self._fsf_cmd("stop")
            time.sleep(0.15)

            if not self._latest_bestmove or self._latest_bestmove in ("(none)", "0000"):
                return self._latest_bestmove

            parts = self._latest_bestmove.split()
            best_move = parts[1] if len(parts) >= 2 else None

            if best_move and not self._move_is_forbidden(best_move, fixed_positions, banned_moves):
                print(f"[ENGINE] ✅ Bestmove {best_move} không dính chốt liệt/cấm")
                return self._latest_bestmove

            if best_move:
                print(f"[ENGINE] ⚠️ Bestmove {best_move} dính chốt liệt/bị cấm -> quét PV khác...")

            for mv in self.trend_analyzer.top_moves(n=8):
                if not self._move_is_forbidden(mv, fixed_positions, banned_moves):
                    print(f"[ENGINE] ✅ Chọn từ MultiPV: {mv}")
                    return f"bestmove {mv}"

            legal = self._generate_legal_non_fixed_moves(fen, fixed_positions, banned_moves)
            if legal:
                print(f"[ENGINE] 🔄 Chấm điểm {len(legal)} nước hợp lệ...")
                self.trend_analyzer.enabled = False
                best_alt, best_score = None, -10**9
                try:
                    for mv in legal[:15]:
                        score = self._score_single_move(fen, moves, mv)
                        if score is not None and score > best_score:
                            best_score = score
                            best_alt = mv
                finally:
                    self.trend_analyzer.enabled = True
                    self.trend_analyzer.clear()
                if best_alt:
                    print(f"[ENGINE] ✅ Fallback chọn nước hợp lệ: {best_alt} (score={best_score})")
                    return f"bestmove {best_alt}"

            if legal:
                print(f"[ENGINE] 🆘 Chọn nước hợp lệ bất kỳ: {legal[0]}")
                return f"bestmove {legal[0]}"

            print("[ENGINE] ❌ KHÔNG có nước hợp lệ nào")
            return None
        finally:
            self._set_multipv(ENGINE_MULTIPV)

    def _move_hits_fixed_pawn(self, move_str, fixed_positions):
        if not fixed_positions or not move_str or len(move_str) < 4:
            return False
        try:
            src_file = ord(move_str[0]) - ord('a')
            src_rank = int(move_str[1])
            src_pos = src_rank * 9 + src_file
            if src_pos in fixed_positions:
                return True
            if FIXED_PAWN_NO_CAPTURE:
                t_file = ord(move_str[2]) - ord('a')
                t_rank = int(move_str[3])
                if t_rank * 9 + t_file in fixed_positions:
                    return True
            return False
        except (ValueError, IndexError):
            return False

    def _move_is_forbidden(self, move_str, fixed_positions, banned_moves=None):
        if not move_str:
            return True
        if banned_moves and move_str in banned_moves:
            return True
        return self._move_hits_fixed_pawn(move_str, fixed_positions)

    # ==================== SINH NƯỚC HỢP LỆ ====================
    def _generate_legal_non_fixed_moves(self, fen, fixed_positions, banned_moves=None):
        banned_moves = banned_moves or set()
        try:
            board_part = fen.split()[0]
            side = fen.split()[1] if len(fen.split()) > 1 else 'w'
            grid = []
            for r in board_part.split('/'):
                line = []
                for ch in r:
                    if ch.isdigit(): line.extend(['.'] * int(ch))
                    else: line.append(ch)
                if len(line) != 9: return []
                grid.append(line)
            if len(grid) != 10: return []

            my_color = side
            moves = []
            for row in range(10):
                for col in range(9):
                    piece = grid[row][col]
                    if piece == '.': continue
                    is_mine = (piece.isupper() and my_color == 'w') or \
                              (piece.islower() and my_color == 'b')
                    if not is_mine: continue
                    src_rank = 9 - row
                    src_pos = src_rank * 9 + col
                    if fixed_positions and src_pos in fixed_positions:
                        continue
                    for tr, tc in self._piece_targets(grid, row, col, piece, my_color):
                        if not (0 <= tr < 10 and 0 <= tc < 9): continue
                        dst = grid[tr][tc]
                        if dst != '.':
                            dst_mine = (dst.isupper() and my_color == 'w') or \
                                       (dst.islower() and my_color == 'b')
                            if dst_mine: continue
                        if FIXED_PAWN_NO_CAPTURE and fixed_positions \
                                and (9 - tr) * 9 + tc in fixed_positions:
                            continue
                        mv = f"{chr(ord('a')+col)}{src_rank}{chr(ord('a')+tc)}{9-tr}"
                        if mv in banned_moves:
                            continue
                        moves.append(mv)
            return moves
        except Exception as e:
            print(f"[ENGINE] Lỗi sinh nước hợp lệ: {e}")
            return []

    def _piece_targets(self, grid, row, col, piece, my_color):
        targets = []
        p = piece.lower()
        def in_palace(r, c):
            if not (3 <= c <= 5): return False
            if my_color == 'w': return 7 <= r <= 9
            else: return 0 <= r <= 2
        if p == 'k':
            for dr, dc in [(-1,0),(1,0),(0,-1),(0,1)]:
                nr, nc = row+dr, col+dc
                if in_palace(nr, nc): targets.append((nr, nc))
        elif p == 'a':
            for dr, dc in [(-1,-1),(-1,1),(1,-1),(1,1)]:
                nr, nc = row+dr, col+dc
                if in_palace(nr, nc): targets.append((nr, nc))
        elif p == 'b':
            for dr, dc in [(-2,-2),(-2,2),(2,-2),(2,2)]:
                nr, nc = row+dr, col+dc
                if not (0 <= nr < 10 and 0 <= nc < 9): continue
                if my_color == 'w' and nr < 5: continue
                if my_color == 'b' and nr > 4: continue
                if grid[(row+nr)//2][(col+nc)//2] != '.': continue
                targets.append((nr, nc))
        elif p == 'r':
            for dr, dc in [(-1,0),(1,0),(0,-1),(0,1)]:
                nr, nc = row+dr, col+dc
                while 0 <= nr < 10 and 0 <= nc < 9:
                    targets.append((nr, nc))
                    if grid[nr][nc] != '.': break
                    nr += dr; nc += dc
        elif p == 'c':
            for dr, dc in [(-1,0),(1,0),(0,-1),(0,1)]:
                nr, nc = row+dr, col+dc
                jumped = False
                while 0 <= nr < 10 and 0 <= nc < 9:
                    cell = grid[nr][nc]
                    if not jumped:
                        if cell == '.': targets.append((nr, nc))
                        else: jumped = True
                    else:
                        if cell != '.':
                            targets.append((nr, nc))
                            break
                    nr += dr; nc += dc
        elif p == 'n':
            for dr, dc in [(-2,-1),(-2,1),(-1,-2),(-1,2),(1,-2),(1,2),(2,-1),(2,1)]:
                nr, nc = row+dr, col+dc
                if not (0 <= nr < 10 and 0 <= nc < 9): continue
                if abs(dr) == 2:
                    if grid[row+dr//2][col] != '.': continue
                else:
                    if grid[row][col+dc//2] != '.': continue
                targets.append((nr, nc))
        elif p == 'p':
            if my_color == 'w':
                targets.append((row-1, col))
                if row <= 4:
                    targets.append((row, col-1)); targets.append((row, col+1))
            else:
                targets.append((row+1, col))
                if row >= 5:
                    targets.append((row, col-1)); targets.append((row, col+1))
        return [(r, c) for (r, c) in targets if 0 <= r < 10 and 0 <= c < 9]

    def _score_single_move(self, fen, moves, candidate):
        try:
            self._latest_bestmove = None
            self._last_score = "?"
            self.trend_analyzer.clear()
            all_moves = list(moves) + [candidate]
            pos_cmd = f"position fen {fen} moves " + " ".join(all_moves)
            self._fsf_cmd(pos_cmd)
            self._fsf_cmd("go depth 8")
            _t0 = time.time()
            while time.time() - _t0 < 0.8:
                if self._latest_bestmove: break
                time.sleep(0.03)
            self._fsf_cmd("stop")
            time.sleep(0.05)
            if self._last_score and self._last_score not in ("?",):
                s = self._last_score.replace("+", "")
                try:
                    if s.startswith("mate"):
                        return 99999 if "-" not in s else -99999
                    return int(s)
                except ValueError:
                    return None
            return None
        except Exception as e:
            print(f"[ENGINE] Lỗi chấm điểm nước {candidate}: {e}")
            return None

    # ==================== WEBSOCKET ====================
    def connect(self):
        import websocket
        self.connected = False
        self.ws = websocket.WebSocketApp(
            WS_URL, cookie=COOKIE,
            on_open=self._on_open, on_message=self._on_message,
            on_error=self._on_error, on_close=self._on_close,
            header={"Origin": "https://gamevh.net"}
        )
        self.ws_thread = threading.Thread(
            target=lambda: self.ws.run_forever(ping_interval=30, ping_timeout=None),
            daemon=True)
        self.ws_thread.start()
        for _ in range(25):
            if self.connected: break
            time.sleep(0.2)
        return self.connected

    def _on_open(self, ws):
        self.connected = True
        self.last_action_timestamp = time.time()
        self.last_recv_timestamp = time.time()
        self._connected_since = time.time()
        self._send_login()

    def _on_message(self, ws, message):
        self.last_recv_timestamp = time.time()
        if isinstance(message, bytes): self._handle_binary_message(message)

    def _on_error(self, ws, error):
        print(f"[WS] ❌ Lỗi kết nối: {type(error).__name__}: {error}")

    def _on_close(self, ws, code, msg):
        if self.board.is_playing:
            print(f"[WS] ⚠️ MẤT KẾT NỐI GIỮA VÁN (code={code}, msg={msg})")
        else:
            print(f"[WS] Đóng kết nối (code={code}, msg={msg})")
        if self._connected_since and time.time() - self._connected_since < 60:
            self._reconnect_streak += 1
        else:
            self._reconnect_streak = 0
        self.connected = False
        self.logged_in = False
        self.in_game = False
        self._joining_table = False
        self._bet_amts_loaded = False
        self._resolved_bet_id = None
        self.bet_amts = []
        self.fixed_pawn_positions = set()
        self._create_pending = False
        self.board.reset()

    def send_message(self, cmd, data=b''):
        if self.ws and self.connected:
            try: self.ws.send(self.conn.pack(cmd, data), opcode=0x2)
            except: pass

    def _send_login(self):
        data = bytearray()
        data.extend(self.conn.pack_ascii(CURRENT_PLAYER_NICKNAME))
        data.extend(self.conn.pack_int(TOKEN))
        data.extend(self.conn.pack_ascii("5.0.2"))
        data.extend(self.conn.pack_ascii(""))
        data.extend(self.conn.pack_ascii(GAME_ID))
        data.extend(self.conn.pack_byte(1))
        self.send_message("LOGIN", bytes(data))

    def send_enter_place(self, path=None, mode=1):
        data = bytearray()
        data.extend(self.conn.pack_ascii(path or self._current_lobby()))
        data.extend(self.conn.pack_string(""))
        data.extend(self.conn.pack_byte(mode))
        self.send_message("ENTER_PLACE", bytes(data))

    def send_list_bet_amt(self): self.send_message("LIST_BET_AMT")

    # ==================== BET & TABLE ====================
    def get_valid_bet_objs(self):
        if not self.bet_amts:
            return []
        return [ba for ba in self.bet_amts
                if isinstance(ba.get("value"), int) and ba["value"] == CREATE_BET_VALUE]

    def is_family_bot(self, name):
        if not name or name.strip().lower() == CURRENT_PLAYER_NICKNAME.lower():
            return False
        return "." in name

    def leave_table(self):
        if self.board.is_playing:
            print("[TABLE] ⚠️ Đang trong ván đấu -> Khóa không rời bàn!")
            return
        print(f"[TABLE] 🚪 Rời bàn, quay lại lobby {self._current_lobby()} để tạo bàn {CREATE_BET_VALUE} xu...")
        if getattr(self, '_table_path', None):
            unregister_bot_table(self._table_path)
        self.in_game = False
        self._joining_table = False
        self._table_path = None
        self._table_created_by_me = False
        self._sit_alone_since = None
        self.slot_players.clear()
        self.board.reset()
        self.fixed_pawn_positions.clear()
        self._quick_play_attempts = 0
        self._lobbies_tried = 0
        self._search_room_idx = 0
        self._search_bet_idx = 0
        self._enter_fail_at = 0.0
        self._create_pending = False
        self.send_enter_place(self._current_lobby())

    def _lower_bet_level(self):
        print(f"[BET] ⚠️ Không hạ cược — bot chỉ tạo bàn {CREATE_BET_VALUE} xu. Reset bộ đếm.")
        self._bet_amts_loaded = False
        self.send_list_bet_amt()

    def resolve_bet_amt_id(self):
        if not self.bet_amts:
            return None
        for ba in self.bet_amts:
            if ba.get("value") == CREATE_BET_VALUE:
                return ba["id"]
        return None

    def send_create_table(self, bet_amt_id=None):
        now = time.time()
        if now - self._last_create_attempt < CREATE_RETRY_DELAY:
            return
        self._last_create_attempt = now
        if bet_amt_id is None:
            bet_amt_id = self._resolved_bet_id if self._resolved_bet_id is not None else self.resolve_bet_amt_id()
        if bet_amt_id is None:
            print(f"[CREATE] ⚠️ Chưa resolve được bet_id cho {CREATE_BET_VALUE} xu -> lấy lại LIST_BET_AMT")
            self._bet_amts_loaded = False
            self.send_list_bet_amt()
            return
        args = [
            ("matchDuration", str(BOT_MATCH_DURATION)),
            ("turnDuration", str(BOT_TURN_DURATION)),
            ("accDuration", str(BOT_ACC_DURATION)),
            ("blockSoftware", str(BOT_BLOCK_SOFTWARE)),
        ]
        data = bytearray()
        data.extend(self.conn.pack_byte(bet_amt_id))
        data.extend(self.conn.pack_byte(len(args)))
        for arg_name, arg_value in args:
            data.extend(self.conn.pack_ascii(arg_name))
            data.extend(self.conn.pack_string(arg_value))
        print(f"[CREATE] 🪑 Tạo bàn {CREATE_BET_VALUE} xu (bet_id={bet_amt_id}) ở lobby '{self._current_lobby()}'")
        self._create_pending = True
        self.send_message("CREATE_RULE", bytes(data))

    def send_quick_play(self, room_id="", bet_amt_id=-1):
        # Không dùng nữa
        return

    def _next_quick_play_target(self):
        return None, None, "DISABLED"

    def send_play(self, source_pos, target_pos):
        self._last_play_sent_at = time.time()
        self._played_this_turn = True
        try:
            self._last_sent_move = self.board.pos_to_engine_move(source_pos, target_pos)
        except Exception:
            self._last_sent_move = None
        data = bytearray()
        data.extend(self.conn.pack_byte(source_pos))
        data.extend(self.conn.pack_byte(target_pos))
        self.send_message("PLAY", bytes(data))

    def opponent_player_id(self):
        for sid, pid in self.slot_players.items():
            if pid and pid != CURRENT_PLAYER_ID and sid != self.board.my_slot_id:
                return pid
        return None

    def send_kick_player(self, player_id):
        self._pending_kick_id = player_id
        data = bytearray()
        data.extend(struct.pack('>q', int(player_id)))
        print(f"[KICK] Gửi KICK_PLAYER playerId={player_id}")
        self.send_message(410, bytes(data))

    def send_ready(self, is_ready=1):
        if self.board.is_playing: return
        print("[GAME] ⏳ Gửi trạng thái READY...")
        data = bytearray()
        data.extend(self.conn.pack_byte(is_ready))
        self.send_message("SET_READY", bytes(data))

    # ==================== MESSAGE HANDLERS ====================
    def _handle_binary_message(self, data):
        try:
            msg = InboundMessage(data)
            cmd = msg.command
            if cmd == "PING": self.send_message("PONG")
            elif cmd == "LOGIN": self._handle_login_response(msg)
            elif cmd == "ENTER_PLACE": self._handle_enter_place_response(msg)
            elif cmd == "QUICK_PLAY": self._handle_quick_play_response(msg)
            elif cmd == "LIST_BET_AMT": self._handle_list_bet_amt_response(msg)
            elif cmd == "CREATE_RULE": self._handle_create_rule_response(msg)
            elif cmd == "SLOT_IN_TABLE_CHANGED": self._handle_slot_changed(msg)
            elif cmd == "PLAYER_ENTERED": self._handle_player_entered(msg)
            elif cmd == "START_MATCH": self._handle_start_match(msg)
            elif cmd == "MOVE": self._handle_move(msg)
            elif cmd == "PLAY" or cmd == "502": self._handle_play_response(msg)
            elif cmd == "SET_TURN": self._handle_set_turn(msg)
            elif cmd == "GAMEOVER": self._handle_gameover(msg)
            elif cmd == "KICK_PLAYER": self._handle_kick_response(msg)
            elif cmd == "ALERT":
                try: print(f"[SERVER] ALERT: {msg.read_string()}")
                except Exception: pass
        except Exception as e: print(f"[RECV ERROR] {e}")

    def _handle_login_response(self, msg):
        if msg.read_byte() == 0:
            self.logged_in = True
            path = msg.read_string()
            if path == 'REFRESH':
                fetch_session_info()
                self._send_login()
                return
            self.send_enter_place(self._current_lobby())

    def _handle_enter_place_response(self, msg):
        status = msg.read_byte()
        if status != 0:
            if self._joining_table:
                print(f"[TABLE] ENTER_PLACE trả status={status} -> coi như đã ở trong bàn, bấm Sẵn sàng")
                self._joining_table = False
                self.in_game = True
                self._enter_fail_at = time.time()
                threading.Thread(
                    target=lambda: (time.sleep(3.0), self.send_ready(1)), daemon=True).start()
            return

        if self._joining_table:
            if is_block_software_message(msg.data):
                print("[GAME] 🛡️ Bàn chơi có chế độ Chống Software (blockSoftware=1). Vẫn sẵn sàng thi đấu!")
            self._joining_table = False
            self.in_game = True
            self._enter_fail_at = 0.0
            self.last_action_timestamp = time.time()
            def delay_initial_ready():
                time.sleep(3.0)
                self.send_ready(1)
            threading.Thread(target=delay_initial_ready, daemon=True).start()
        elif not self.in_game:
            if self._table_path and time.time() - self._table_path_ts < 180:
                print(f"[TABLE] Thử ngồi lại bàn cũ: {self._table_path}")
                self.in_game = True
                self._joining_table = True
                path = self._table_path
                threading.Thread(
                    target=lambda: (time.sleep(0.5), self.send_enter_place(path=path, mode=1)),
                    daemon=True).start()
                return
            self._bet_amts_loaded = False
            self._resolved_bet_id = None
            self.send_list_bet_amt()

    def _handle_quick_play_response(self, msg):
        # Không dùng QUICK_PLAY nữa -> chỉ đọc để tránh lỗi buffer
        try:
            status = msg.read_byte()
            if status == 0:
                _ = msg.read_ascii()
        except Exception:
            pass

    def _handle_list_bet_amt_response(self, msg):
        if msg.read_byte() != 0:
            return
        count = msg.read_byte()
        self.bet_amts = [{"id": i, "value": msg.read_int()} for i in range(count)]
        self._resolved_bet_id = self.resolve_bet_amt_id()
        self._bet_amts_loaded = True

        if self._resolved_bet_id is not None:
            print(f"[BET] 📋 Đã resolve bet_id={self._resolved_bet_id} cho mức {CREATE_BET_VALUE} xu")
        else:
            server_values = sorted(set(ba['value'] for ba in self.bet_amts))
            print(f"[BET] ⚠️ Server KHÔNG có mức {CREATE_BET_VALUE} xu. "
                  f"Server có: {server_values}. Bot sẽ chờ và thử lại.")

    def _handle_create_rule_response(self, msg):
        self._create_pending = False
        status = msg.read_byte()
        if status == 0:
            table_path = msg.read_ascii()
            self.in_game = True
            self._joining_table = True
            self._table_created_by_me = True
            self._sit_alone_since = time.time()
            self._table_path = table_path; self._table_path_ts = time.time()
            register_bot_table(table_path, USER)
            print(f"[CREATE] 🎉 Tạo bàn thành công ở lobby '{self._current_lobby()}': {table_path}. Đang vào bàn...")
            def async_join():
                time.sleep(0.5)
                self.send_enter_place(path=table_path, mode=1)
            threading.Thread(target=async_join, daemon=True).start()
        else:
            print(f"[CREATE] ❌ Tạo bàn thất bại (status={status}). Sẽ thử lại sau {int(CREATE_RETRY_DELAY)}s...")
            self._joining_table = False

    def _handle_player_entered(self, msg):
        try:
            place_level = msg.read_byte()
            pid = msg.read_long()
            name = msg.read_string()
            if pid > 0 and pid != CURRENT_PLAYER_ID:
                self.player_names[pid] = name
                print(f"[PLAYER] 👤 Người chơi '{name}' (id={pid}) vào bàn/phòng (level={place_level})")
                if not self.board.is_playing and self.is_family_bot(name) and self.opponent_player_id() == pid:
                    print(f"[AVOID] ⚠️ Đồng đội '{name}' ở ghế đối diện! Rời bàn...")
                    self.leave_table()
        except Exception: pass

    def _handle_slot_changed(self, msg):
        try:
            _ = msg.read_string()
            slot_id = msg.read_byte()
            msg.read_long(); msg.read_long(); msg.read_byte(); msg.read_short(); msg.read_ascii(); msg.read_byte(); msg.read_byte()
            player_id = msg.read_long()
            if player_id > 0:
                self.slot_players[slot_id] = player_id
            else:
                self.slot_players.pop(slot_id, None)
            if player_id == CURRENT_PLAYER_ID:
                self.board.my_slot_id = slot_id
            else:
                if player_id > 0:
                    name = self.player_names.get(player_id, "")
                    print(f"[TABLE] 👤 Ghế đối diện (slot={slot_id}): playerId={player_id}{f', name={name}' if name else ''}")
                    if not self.board.is_playing and self.is_family_bot(name):
                        print(f"[AVOID] ⚠️ Đối thủ '{name}' là bot đồng đội! Rời bàn...")
                        self.leave_table()
                        return
                    self._sit_alone_since = None
                    if not self.board.is_playing:
                        def delay_ready_on_player():
                            time.sleep(3.0)
                            self.send_ready(1)
                        threading.Thread(target=delay_ready_on_player, daemon=True).start()
                else:
                    if not self.board.is_playing and self.opponent_player_id() is None:
                        print("[TABLE] 🚪 Không còn đối thủ ở ghế chơi. Bắt đầu đếm ngược chờ người chơi...")
                        self._sit_alone_since = time.time()
        except: pass

    def _handle_start_match(self, msg):
        print(f"[GAME] 🎮 Trận chiến bắt đầu!")
        self._play_reject_count = 0
        self._thinking = False
        self._turn_started_at = 0.0
        self._last_play_sent_at = 0.0
        self._played_this_turn = False
        self._reconnect_streak = 0
        self._enter_fail_at = 0.0
        self._sit_alone_since = None
        self.board.reset()
        self.fixed_pawn_positions.clear()
        self._banned_moves = set()
        self.board.is_playing = True
        self.in_game = True
        self._joining_table = False
        self.last_action_timestamp = time.time()

        try:
            player_count = msg.read_byte()
            for _ in range(player_count): msg.read_byte(); msg.read_int()
            piece_count = msg.read_byte()
            board_pieces = []
            for _ in range(piece_count):
                raw_sid = msg.read_byte(); raw_face = msg.read_byte(); pos = msg.read_byte(); is_open = msg.read_byte()
                board_pieces.append((self._decode_piece_id(raw_sid), self._decode_piece_id(raw_face), pos, is_open))

            msg.read_byte(); mystery_count = msg.read_byte()
            for _ in range(mystery_count): msg.read_byte()
            msg.read_byte(); msg.read_byte()

            first_turn_slot_id = msg.read_byte()
            my_slot_id = msg.read_byte()
            if my_slot_id < 0 or my_slot_id == 255:
                my_slot_id = self.board.my_slot_id if self.board.my_slot_id >= 0 else first_turn_slot_id

            self.board.set_my_slot(my_slot_id, first_turn_slot_id)

            for sid, face, position, is_open in board_pieces:
                piece_type = int(face[1]) if len(face) > 1 else 0
                if piece_type == 7 and position not in STANDARD_PAWN_POSITIONS:
                    self.fixed_pawn_positions.add(position)

            if self.fixed_pawn_positions:
                print(f"[GAME] 🛡️ Bàn đấu có {len(self.fixed_pawn_positions)} chốt bị liệt/khóa! "
                      f"Bot sẽ tạm bật MultiPV={ENGINE_MULTIPV_FIXED_PAWN} để tìm nước thay thế.")
            else:
                print("[GAME] ✅ Bàn không có chốt liệt -> chơi MultiPV=1 (mistboard level 8)")

            self.board.set_base(self._build_fen_from_pieces(board_pieces), 'w')
            if my_slot_id == first_turn_slot_id:
                self.board.is_my_turn = True
                self._turn_started_at = time.time()
                threading.Thread(target=self._make_auto_move, daemon=True).start()
        except Exception as e: print(f"[START_MATCH ERROR] {e}")

    def _build_fen_from_pieces(self, pieces):
        board = [['.' for _ in range(9)] for _ in range(10)]
        for sid, face, position, is_open in pieces:
            if position < 0 or position >= 90: continue
            game_row, col = position // 9, position % 9
            fen_row = 9 - game_row
            color = face[0]
            piece_type = int(face[1]) if len(face) > 1 else 0
            type_to_fen = {1: 'k', 2: 'a', 3: 'b', 4: 'r', 5: 'c', 6: 'n', 7: 'p'}
            fen_char = type_to_fen.get(piece_type, '?')
            if color == 'r': fen_char = fen_char.upper()
            board[fen_row][col] = fen_char
        fen_rows = []
        for row in board:
            fen_row = ""
            empty = 0
            for cell in row:
                if cell == '.': empty += 1
                else:
                    if empty > 0: fen_row += str(empty); empty = 0
                    fen_row += cell
            if empty > 0: fen_row += str(empty)
            fen_rows.append(fen_row)
        return '/'.join(fen_rows) + ' w'

    def _handle_move(self, msg):
        try:
            source_pos = msg.read_byte()
            target_pos = msg.read_byte()
            engine_move = self.board.pos_to_engine_move(source_pos, target_pos)
            self.last_action_timestamp = time.time()
            if not self.board.move_history or self.board.move_history[-1] != engine_move:
                self.board.record_move(engine_move)
                self._played_this_turn = False
        except Exception as e: print(f"[MOVE ERROR] {e}")

    def _handle_play_response(self, msg):
        if msg.read_byte() != 0:
            self.board.is_my_turn = True
            self._played_this_turn = False
            self._play_reject_count = getattr(self, '_play_reject_count', 0) + 1
            bad = getattr(self, '_last_sent_move', None)
            if bad:
                self._banned_moves.add(bad)
                print(f"[PLAY] ⚠️ Server từ chối nước {bad} (lần {self._play_reject_count}) "
                      f"-> CẤM nước này ({len(self._banned_moves)} nước bị cấm), tính nước khác")
            else:
                print(f"[PLAY] ⚠️ Server từ chối nước đi (lần {self._play_reject_count}) -> tính lại")
            if self._play_reject_count <= 8:
                threading.Thread(
                    target=lambda: (time.sleep(0.5), self._make_auto_move()), daemon=True).start()
            else:
                print("[PLAY] ❌ Bị từ chối quá nhiều trong 1 lượt -> để watchdog 12s tính lại với danh sách cấm")
        else:
            self._play_reject_count = 0

    def _handle_set_turn(self, msg):
        try:
            slot_id = msg.read_byte()
            try:
                turn_timeout = msg.read_short()
            except Exception:
                turn_timeout = 0
            if slot_id == -2:
                return
            if slot_id == -1 or not self.board.is_playing:
                return
            self.turn_timeout = turn_timeout
            was_my_turn = self.board.is_my_turn
            self.board.is_my_turn = (slot_id == self.board.my_slot_id)
            self.last_action_timestamp = time.time()
            if not self.board.is_my_turn:
                return
            self._turn_started_at = time.time()
            if not was_my_turn:
                self._played_this_turn = False
            if not self._thinking:
                threading.Thread(target=self._make_auto_move, daemon=True).start()
        except Exception as e:
            print(f"[SET_TURN ERROR] {e}")

    def _handle_kick_response(self, msg):
        try:
            status = msg.read_byte()
            content = msg.read_string()
        except Exception:
            status, content = None, ""
        if self._pending_kick_id is not None:
            pid = self._pending_kick_id; self._pending_kick_id = None
            if status == 0: print(f"[KICK] ✅ Đã đuổi playerId={pid}. {content}")
            else:           print(f"[KICK] ❌ Đuổi playerId={pid} thất bại (status={status}): {content}")
            return
        print(f"[KICK] ⚠️ Bot bị đuổi khỏi bàn: {content}")
        self.in_game = False
        self._joining_table = False
        self._table_path = None
        self.board.reset()

    def _handle_gameover(self, msg):
        my_result, results = None, {}
        try:
            count = msg.read_byte()
            for _ in range(count):
                sid = msg.read_byte(); res = msg.read_byte(); msg.read_long()
                results[sid] = res
                if sid == self.board.my_slot_id:
                    my_result = res
        except Exception:
            results = {}

        bot_won  = my_result in (1, 11)
        bot_lost = my_result in (2, 4, 12)
        if bot_won:    print("[GAME] 🏁 Kết thúc. >>> THẮNG <<<")
        elif bot_lost: print("[GAME] 🏁 Kết thúc. >>> THUA <<<")
        elif my_result is None: print("[GAME] 🏁 Kết thúc.")
        else: print("[GAME] 🏁 Kết thúc. >>> HOÀ <<<")

        should_kick = (KICK_MODE == "always"
                       or (KICK_MODE == "when_lose" and bot_lost)
                       or (KICK_MODE == "when_win" and bot_won))
        victim = None
        if should_kick:
            want = (1, 11) if KICK_MODE == "when_lose" else (2, 4, 12)
            target_sid = next((sid for sid, res in results.items()
                               if sid != self.board.my_slot_id and res in want), None)
            victim = self.slot_players.get(target_sid) if target_sid is not None else None
            if not victim:
                victim = self.opponent_player_id()
            if not victim:
                print("[KICK] Không xác định được playerId đối phương -> bỏ qua")

        self.fixed_pawn_positions.clear()
        self._banned_moves = set()
        self.board.reset()
        self.board.is_playing = False
        self.board.is_my_turn = False
        self.in_game = True
        self._joining_table = False
        self.last_action_timestamp = time.time()

        if getattr(self, '_engine_proc', None) and self._engine_proc.poll() is None:
            self._fsf_cmd("ucinewgame")
            self._fsf_cmd("isready")

        def after_gameover():
            is_guest = not getattr(self, '_table_created_by_me', False)
            if bot_lost:
                if victim and not is_guest:
                    time.sleep(KICK_DELAY)
                    if self.connected and not self.board.is_playing:
                        self.send_kick_player(victim)
                        time.sleep(2.0)
                elif is_guest:
                    print("[GAME] 👤 Khách vào bàn -> không có quyền kick, rời bàn ngay...")
                print(f"[GAME] 🔄 Thua trận -> Rời bàn -> Tạo bàn mới {CREATE_BET_VALUE} xu...")
                time.sleep(1.0)
                self.leave_table()
            else:
                print("[GAME] ✅ Thắng/Hoà -> Ở lại bàn, sẵn sàng ván tiếp...")
                time.sleep(3.0)
                self.send_ready(1)
        threading.Thread(target=after_gameover, daemon=True).start()

    def _make_auto_move(self):
        if not self.board.is_my_turn or not self.board.is_playing: return
        if self._thinking: return
        self._thinking = True
        try:
            self._do_auto_move()
        finally:
            self._thinking = False

    def _do_auto_move(self):
        if not getattr(self, '_engine_proc', None) or self._engine_proc.poll() is not None:
            self._init_engine()
            if not self.engine: return

        fen, moves = self.board.get_current_fen()
        fixed = self.fixed_pawn_positions if self.fixed_pawn_positions else None
        banned = set(self._banned_moves)

        raw_bestmove_line = self.get_best_move(fen, moves, fixed_positions=fixed, banned_moves=banned)
        if not raw_bestmove_line: return

        parts = raw_bestmove_line.split()
        if len(parts) < 2: return
        best_move = parts[1]

        if fixed and ENGINE_MULTIPV_FIXED_PAWN > 1 and self.trend_analyzer.enabled:
            trend_move = self.trend_analyzer.select_best_trend_move()
            if trend_move and best_move not in ["(none)", "0000"] \
                    and trend_move not in banned \
                    and not self._move_hits_fixed_pawn(trend_move, fixed):
                print(f"[RAM-LEARN] 🧠 Thay thế '{best_move}' bằng nước tối ưu: '{trend_move}'")
                best_move = trend_move

        if best_move in ["(none)", "0000"]:
            print("\n[HỆ THỐNG TÀN CUỘC] ⚠️ Pikafish báo: bestmove (none) - Hết nước hợp lệ.")
            self.board.is_my_turn = False
            return

        if best_move:
            try:
                source_pos, target_pos = self.board.engine_move_to_pos(best_move)
                _turn_start = getattr(self, '_turn_started_at', 0.0) or time.time()
                _remain = MIN_MOVE_SECONDS - (time.time() - _turn_start)
                if _remain > 0:
                    time.sleep(_remain)
                if self.board.is_my_turn and self.board.is_playing:
                    tag = " [CHỐT LIỆT]" if fixed else ""
                    print(f"-> Hành động{tag}: Xuất quân: {best_move} "
                          f"[điểm {self._last_score} depth {self._last_depth}]")
                    self.send_play(source_pos, target_pos)
            except Exception as e: print(f"[BOT ERROR] Dịch tọa độ lỗi: {e}")

    def _decode_piece_id(self, encoded_id):
        color = 'r'
        if encoded_id < 0: encoded_id = -encoded_id; color = 'b'
        return f"{color}{encoded_id >> 3}{'' if (encoded_id & 7) == 0 else (encoded_id & 7)}"

    def start_keep_alive(self):
        def keep_alive_loop():
            while self.connected:
                time.sleep(10)
                if self.connected: self.send_message("PING")
        threading.Thread(target=keep_alive_loop, daemon=True).start()

    # ==================== MAIN LOOP ====================
    def run(self):
        print("[BOT] Khởi chạy hệ thống giám sát tự động...")
        print(f"[BOT] ⚙️ Engine: mainline Pikafish (mistboard level 8, MultiPV mặc định = {ENGINE_MULTIPV})")
        print(f"[BOT] 🛡️ Khi bàn có chốt liệt: tạm bật MultiPV={ENGINE_MULTIPV_FIXED_PAWN} + fallback sinh nước hợp lệ")
        print(f"[BOT] 🎯 Chiến lược: TẠO BÀN cố định {CREATE_BET_VALUE} xu — KHÔNG tìm bàn, KHÔNG QUICK_PLAY")
        print(f"[BOT] ⏱️ Chờ trong bàn {int(SIT_ALONE_TIMEOUT)}s trước khi rời tạo bàn mới")

        while True:
            try:
                now_ts = time.time()
                if self.connected and now_ts - self.last_recv_timestamp > 120:
                    print("[WS] Không nhận dữ liệu 120s -> coi như chết, kết nối lại")
                    if self.ws: self.ws.close()
                    time.sleep(2)
                elif self.connected and self.board.is_playing:
                    if now_ts - self.last_action_timestamp > 300:
                        print("[WS] Ván treo 300s không có nước đi -> kết nối lại")
                        if self.ws: self.ws.close()
                        time.sleep(2)

                if not self.connected:
                    if self._reconnect_streak >= 3:
                        print("[BOT] ⚠️ Bị ngắt kết nối liên tục ngay sau khi đăng nhập.")
                        print(f"[BOT] ⚠️ Nhiều khả năng tài khoản {USER} đang được ĐĂNG NHẬP Ở NƠI KHÁC.")
                    if self._reconnect_streak > 0:
                        delay = min(60, 5 * (2 ** min(self._reconnect_streak - 1, 4)))
                        print(f"[WS] Rớt liên tiếp lần {self._reconnect_streak} -> chờ {delay}s rồi đăng nhập lại")
                        time.sleep(delay)
                    if not fetch_session_info():
                        time.sleep(5); continue
                    self.logged_in = False
                    self.in_game = False
                    self._joining_table = False
                    self._bet_amts_loaded = False
                    self._resolved_bet_id = None
                    self.bet_amts = []
                    self.fixed_pawn_positions = set()
                    self._create_pending = False
                    self.board.reset()
                    if not self.connect():
                        time.sleep(5); continue
                    self.start_keep_alive()
                    time.sleep(2)

                if (self.board.is_playing and self.board.is_my_turn and not self._thinking
                        and self._turn_started_at
                        and time.time() - self._turn_started_at > 12
                        and not self._played_this_turn):
                    print("[TURN] Tới lượt nhưng 12s chưa đi được -> tính lại")
                    self._turn_started_at = time.time()
                    threading.Thread(target=self._make_auto_move, daemon=True).start()

                if self.board.is_playing:
                    self._sit_alone_since = None
                else:
                    if self.in_game and not self._joining_table:
                        opp_id = self.opponent_player_id()
                        if opp_id is None:
                            if self._sit_alone_since is None:
                                self._sit_alone_since = time.time()
                            else:
                                elapsed = time.time() - self._sit_alone_since
                                if elapsed >= SIT_ALONE_TIMEOUT:
                                    print(f"[TABLE] ⏱️ Đã chờ {int(elapsed)}s không có người chơi -> Rời bàn tạo bàn mới")
                                    self.leave_table()
                        else:
                            self._sit_alone_since = None

                if (self._enter_fail_at and self.in_game and not self.board.is_playing
                        and time.time() - self._enter_fail_at > ENTER_FAIL_TIMEOUT):
                    print(f"[TABLE] Chờ {int(ENTER_FAIL_TIMEOUT)}s không vào được ván nào -> bỏ bàn cũ, tạo bàn mới")
                    self._enter_fail_at = 0.0
                    self.leave_table()

                # ============================================================
                # ★ CHIẾN LƯỢC TẠO BÀN (KHÔNG TÌM BÀN):
                #   • Mỗi chu kỳ: đảm bảo có LIST_BET_AMT -> resolve bet_id 5000 xu.
                #   • Gửi CREATE_RULE (bet_id=5000, matchDuration=5, turnDuration=60, blockSoftware=1).
                #   • Chờ server trả table_path -> vào bàn.
                #   • Nếu thất bại -> retry sau CREATE_RETRY_DELAY giây.
                #   • TUYỆT ĐỐI KHÔNG gọi QUICK_PLAY.
                # ============================================================
                if (self.connected and self.logged_in and not self.in_game
                        and not self._joining_table and not self._create_pending):
                    if not self._bet_amts_loaded:
                        self.send_list_bet_amt()
                    else:
                        if self._resolved_bet_id is None:
                            self._resolved_bet_id = self.resolve_bet_amt_id()
                        if self._resolved_bet_id is None:
                            print(f"[CREATE] ⚠️ Server chưa có mức {CREATE_BET_VALUE} xu. Lấy lại LIST_BET_AMT...")
                            time.sleep(5)
                            self._bet_amts_loaded = False
                            self.send_list_bet_amt()
                        else:
                            self.send_create_table(bet_amt_id=self._resolved_bet_id)
                time.sleep(1)
            except KeyboardInterrupt: break
            except Exception as e:
                print(f"[RUN ERROR] {e}")
                time.sleep(5)

    def cleanup(self):
        proc = getattr(self, '_engine_proc', None)
        if proc:
            try:
                if proc.poll() is None:
                    proc.stdin.write("quit\n"); proc.stdin.flush(); proc.wait(timeout=2)
            except:
                try: proc.terminate()
                except: pass
        if self.ws:
            try: self.ws.close()
            except: pass

def acquire_single_instance_lock():
    try:
        import fcntl
        path = os.path.join(tempfile.gettempdir(), f"xiangqi_bot_{USER}.lock")
        f = open(path, "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"[BOT] ❌ Đã có một bot khác đang chạy với tài khoản {USER} (khoá: {path}).")
            print("[BOT] Thoát để tránh hai phiên đá nhau. Hãy tắt bot kia trước.")
            sys.exit(1)
        f.write(str(os.getpid())); f.flush()
        atexit.register(lambda: (fcntl.flock(f, fcntl.LOCK_UN), f.close()))
        return f
    except ImportError:
        return None

if __name__ == "__main__":
    _lock = acquire_single_instance_lock()
    bot = PikafishBot()
    def signal_handler(sig, frame): bot.cleanup(); sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    try: bot.run()
    finally: bot.cleanup()
