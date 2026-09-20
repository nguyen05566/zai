"""
cup_bot_jieqi.py — Cờ Úp Bot dùng Jieqi AI engine (PikaJieQi)
Khác biệt vs cup_bot.py:
Engine: pikajieqi-native (C++ native, không cần wine)
Movetime: 3000ms (~3s/nước)
Tự restart engine khi cần

[BẢN SỬA] Bot KHÔNG tự kick/Thoát khi thua — luôn ở lại bàn và ready ván mới.

[BẢN SỬA 1+2] Lưu true_faces từ START_MATCH + detect capture lên quân úp.

[BẢN SỬA 3] Dùng `position fen` với BAG string + FEN động (flip + side-to-move).

[BẢN SỬA 4] FIX bug NotYourPiece:
- Restore dark_positions SAU set_base (trước đây bị xóa → FEN thiếu X/x)
- Thêm log [FEN-BUILD] để verify FEN có đủ X/x cho quân úp
"""
import struct
import threading
import time
import sys
import os
import re
import subprocess
import signal
import atexit
import tempfile
import json
import random
import traceback
import shutil
import urllib.request, urllib.parse, http.cookiejar

# ============================================================================
# HTTP SESSION
# ============================================================================
class _UrllibSession:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPRedirectHandler()
        )
        class _Headers:
            def __init__(self): self.d = {}
            def update(self, items): self.d.update(items)
            def __setitem__(self, k, v): self.d[k] = v
            def __getitem__(self, k): return self.d[k]
            def __contains__(self, k): return k in self.d
        self.headers = _Headers()
        self.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/139.0 Safari/537.36",
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
        })
        for k, v in self.headers.d.items():
            self.op.addheaders.append((k, v))
        self.last_url = ""
        self.cookies = self.cj

    def get(self, url, timeout=20, allow_redirects=True, **kw):
        h = list(self.op.addheaders)
        if 'headers' in kw and kw['headers']:
            for k, v in kw['headers'].items(): h.append((k, v))
        req = urllib.request.Request(url, headers=dict(h))
        r = self.op.open(req, timeout=timeout)
        self.last_url = r.geturl()
        class R:
            def __init__(self, r):
                self.text = r.read().decode("utf-8", "replace")
                self.url = r.geturl()
        return R(r)

    def post(self, url, data=None, timeout=20, headers=None, allow_redirects=True, **kw):
        body = urllib.parse.urlencode(data or {}).encode()
        h = dict(self.op.addheaders)
        if headers:
            for k, v in headers.items(): h[k] = v
        req = urllib.request.Request(url, data=body, headers=h)
        r = self.op.open(req, timeout=timeout)
        self.last_url = r.geturl()
        class R:
            def __init__(self, r):
                self.text = r.read().decode("utf-8", "replace")
                self.url = r.geturl()
        return R(r)

requests = type('R', (), {'Session': _UrllibSession})()

# ==================== TÀI KHOẢN ====================
CARO_USER_DIRECT = "nguyen13"
CARO_PASSWD_DIRECT = "******"

def _clean_env(val, default):
    if val and str(val).strip():
        return str(val).strip()
    return default

USER = _clean_env(os.environ.get("CARO_USER19"), CARO_USER_DIRECT)
PASSWD = _clean_env(os.environ.get("CARO_PASSWD19"), CARO_PASSWD_DIRECT)

COOKIE = ""
WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/mystery_xiangqi/0"
CURRENT_PLAYER_NICKNAME = USER
CURRENT_PLAYER_ID = 0
TOKEN = 0
GAME_ID = 'mystery_xiangqi'
PLACE_PATH = 'Lobby.mystery_xiangqi.0'

# === ENGINE CONFIG ===
PIKAJIEQI_BINARY_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "pikajieqi-native"),
]

ENGINE_MULTIPV = 1
MIN_MOVE_SECONDS = 3.0
MOVE_DEADLINE_SECONDS = 30.0
MAX_SAFE_MOVES = 250
TRUST_ENGINE_AFTER = 100
MAX_ENGINE_RESTARTS_PER_GAME = 2
MOVE_DEDUP_WINDOW = 0.1

KICK_MODE = "never"
KICK_DELAY = 5.0
SIT_ALONE_TIMEOUT = 300.0

BOT_BET_XU = 1000
BOT_USE_CREATE_TABLE = True
BOT_MATCH_DURATION = '5'
BOT_TURN_DURATION = '30'
BOT_ACC_DURATION = '0'
BOT_BLOCK_SOFTWARE = '0'

VN_TEN_DAU = [
    "Tuấn ",  "Minh ",  "Đức ",  "Hoàng ",  "Huy ",  "Hùng ",  "Dũng ",  "Cường ",  "Long ",  "Nam ",
    "Sơn ",  "Hải ",  "Phong ",  "Thắng ",  "Trung ",  "Kiên ",  "Quân ",  "Thanh ",  "Đạt ",  "Khoa ",
]
VN_TEN_KHONG_DAU = [
    "Tuan ",  "Minh ",  "Duc ",  "Hoang ",  "Huy ",  "Hung ",  "Dung ",  "Cuong ",  "Long ",  "Nam ",
    "Son ",  "Hai ",  "Phong ",  "Thang ",  "Trung ",  "Kien ",  "Quan ",  "Thanh ",  "Dat ",  "Khoa ",
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
        form_match = re.search(r'(?is)<form\b[^>]*name=["\']InputForm0["\'][^>]*>.*?</form\s*>', page.text)
        if not form_match: return
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
                k = nm.group(1)
                data[k] = val.group(1) if val else ''
        old_full_name = data.get('FULL_NAME', '')
        new_full_name = generate_dotted_full_name()
        data['FULL_NAME'] = new_full_name
        data['OLD_PASSWORD'] = PASSWD
        data['SAVE'] = '\uf046'
        session.post(action, timeout=15, data=data,
                     headers={'Origin': 'https://gamevh.net',
                              'Referer': page.url,
                              'Content-Type': 'application/x-www-form-urlencoded'},
                     allow_redirects=True)
        print(f"[PROFILE] 👤 '{old_full_name}' -> '{new_full_name}'")
    except Exception as e:
        print(f"[PROFILE] Lỗi: {e}")

def sync_random_avatar(session):
    try:
        profile_url = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
        before = session.get(profile_url, timeout=15)
        m = re.search(r'/avatar/builtin(\d+).(?:webp|png|jpg)', before.text, re.I)
        old_avatar = int(m.group(1)) if m else None
        catalog = []
        seen = set()
        pattern = re.compile(
            r'''buyAvatar\(\s*(["']?)(\d+)\1\s*,\s*(["'])(.*?)\3\s*,\s*(["']?)([\d,.]+)\5\s*\)''',
            re.I | re.S)
        for category in range(1, 7):
            url = f"https://gamevh.net/com/ftl/game/profile/avatar_by_category.jsp?excludeLayout=true&category_id={category}"
            page = session.get(url, timeout=15)
            for match in pattern.finditer(page.text):
                avatar_id = int(match.group(2))
                if avatar_id not in seen:
                    seen.add(avatar_id)
                    catalog.append(avatar_id)
        choices = [a for a in catalog if a != old_avatar]
        if not choices: return
        selected = random.choice(choices)
        update_url = f"https://gamevh.net/com/ftl/game/profile/update_avatar.jsp?pk={selected}&redirect=/"
        session.post(update_url, timeout=20,
                     headers={"Origin": "https://gamevh.net",
                              "Referer": "https://gamevh.net/com/ftl/game/profile/avatar.jsp"},
                     allow_redirects=True)
    except Exception as e:
        print(f"[PROFILE] Lỗi avatar: {e}")

def is_block_software_message(raw_bytes):
    try:
        idx = raw_bytes.find(b"blockSoftware")
        if idx != -1:
            snippet = raw_bytes[idx:idx + 40]
            if b"1" in snippet or b"true" in snippet.lower():
                return True
    except Exception:
        pass
    return False

ACTIVE_TABLES_FILE = os.path.join(tempfile.gettempdir(), "zaro_active_tables.json")

def get_active_bot_tables():
    try:
        if not os.path.exists(ACTIVE_TABLES_FILE): return {}
        with open(ACTIVE_TABLES_FILE, 'r') as f:
            content = f.read().strip()
        if not content: return {}
        data = json.loads(content)
        now = time.time()
        return {tp: info for tp, info in data.items()
                if isinstance(info, dict) and now - info.get("timestamp", 0) < 180}
    except Exception:
        return {}

def register_bot_table(table_path, user):
    if not table_path: return
    try:
        data = get_active_bot_tables()
        data[table_path] = {"user": user, "timestamp": time.time(), "pid": os.getpid()}
        with open(ACTIVE_TABLES_FILE, 'w') as f: json.dump(data, f)
    except Exception:
        pass

def unregister_bot_table(table_path):
    if not table_path: return
    try:
        data = get_active_bot_tables()
        if table_path in data:
            data.pop(table_path, None)
        with open(ACTIVE_TABLES_FILE, 'w') as f: json.dump(data, f)
    except Exception:
        pass

def fetch_session_info():
    global COOKIE, TOKEN, CURRENT_PLAYER_NICKNAME, CURRENT_PLAYER_ID, PLACE_PATH, _IDENTITY_SYNCED
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/139.0 Safari/537.36",
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
        })
        session.get(LOGIN_URL, timeout=20)
        session.post(
            LOGIN_URL, timeout=20,
            data={"redirect": "/", "USER_NAME": USER, "PASSWORD": PASSWD,
                  "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
            headers={"Origin": "https://gamevh.net",
                     "Referer": LOGIN_URL,
                     "Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=True)
        if not _IDENTITY_SYNCED:
            _IDENTITY_SYNCED = True
            sync_profile_name(session)
            sync_random_avatar(session)
        game_resp = session.get(GAME_URL, timeout=20)
        page_html = game_resp.text
        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", page_html)
        if not tm: return False
        TOKEN = int(tm.group(1))
        nm = re.search(r'var\s+currentPlayerNickName\s*=\s*["\']([^"\']+)["\']', page_html)
        if not nm: return False
        CURRENT_PLAYER_NICKNAME = nm.group(1).strip()
        pid = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", page_html)
        if pid: CURRENT_PLAYER_ID = int(pid.group(1))
        pm = re.search(r'var\s+placePath\s*=\s*["\']([^"\']+)["\']', page_html)
        if pm: PLACE_PATH = pm.group(1)
        try:
            cookie_parts = [f"{c.name}={c.value}" for c in session.cookies]
            COOKIE = "; ".join(cookie_parts)
        except Exception:
            COOKIE = "; ".join(f"{k}={v}" for k, v in session.cookies.items())
        print(f"[SESSION] Login OK | Token: {TOKEN} | Nick: {CURRENT_PLAYER_NICKNAME} | ID: {CURRENT_PLAYER_ID}")
        return True
    except Exception as e:
        print(f"[SESSION] Lỗi: {e}")
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
    def rem(self):
        return len(self.data) - self.offset

STANDARD_PAWN_POSITIONS = set()
for _c in [0, 2, 4, 6, 8]:
    STANDARD_PAWN_POSITIONS.add(6 * 9 + _c)
    STANDARD_PAWN_POSITIONS.add(3 * 9 + _c)

INITIAL_BAG = {'A': 2, 'B': 2, 'N': 2, 'R': 2, 'C': 2, 'P': 5,
               'a': 2, 'b': 2, 'n': 2, 'r': 2, 'c': 2, 'p': 5}
BAG_ORDER = ['A', 'B', 'N', 'R', 'C', 'P', 'a', 'b', 'n', 'r', 'c', 'p']

UCI_MOVE_RE = re.compile(r'^[a-i]\d[a-i]\d$')
UCI_MOVE_WITH_SUFFIX_RE = re.compile(r'^[a-i]\d[a-i]\d[a-zA-Z]?$')

TYPE_TO_FEN = {1: 'k', 2: 'a', 3: 'b', 4: 'r', 5: 'c', 6: 'n', 7: 'p'}

class XiangqiBoardTracker:
    INITIAL_FEN = "xxxxkxxxx/9/1x5x1/x1x1x1x1x/9/9/X1X1X1X1X/1X5X1/9/XXXXKXXXX w"

    def __init__(self):
        self.reset()

    def reset(self):
        self.start_fen = self.INITIAL_FEN.split(' ')[0]
        self.start_side = 'w'
        self.uci_moves = []
        self.revealed_chars = []
        self.my_slot_id = -1
        self.first_turn_slot_id = 0
        self.is_my_turn = False
        self.is_playing = False
        self.is_red = None
        self.dark_positions = set()
        self.flip = False
        self.flip_known = False
        self.side_to_move = 'w'
        self.true_faces = {}
        self.lost_pieces = set()

    def true_face_at(self, pos):
        return self.true_faces.get(pos)

    def set_true_face(self, pos, fen_char):
        if fen_char:
            self.true_faces[pos] = fen_char

    def move_true_face(self, source_pos, target_pos):
        face = self.true_faces.pop(source_pos, None)
        eaten = self.true_faces.pop(target_pos, None)
        if eaten:
            self.lost_pieces.add(target_pos)
        if face:
            self.true_faces[target_pos] = face
        return face, eaten

    def remove_true_face(self, pos):
        face = self.true_faces.pop(pos, None)
        if face:
            self.lost_pieces.add(pos)
        return face

    def pos_to_rc(self, pos):
        s_row, col = pos // 9, pos % 9
        return ((9 - s_row) if self.flip else s_row), col

    def rc_to_pos(self, fen_row, col):
        s_row = (9 - fen_row) if self.flip else fen_row
        return s_row * 9 + col

    def pos_to_engine_move(self, source_pos, target_pos):
        s_row, s_col = self.pos_to_rc(source_pos)
        t_row, t_col = self.pos_to_rc(target_pos)
        return (f"{chr(ord('a') + s_col)}{9 - s_row}"
                f"{chr(ord('a') + t_col)}{9 - t_row}")

    def engine_move_to_pos(self, engine_move):
        move = engine_move[:4]
        s_col, s_rank = ord(move[0]) - ord('a'), int(move[1])
        t_col, t_rank = ord(move[2]) - ord('a'), int(move[3])
        return (self.rc_to_pos(9 - s_rank, s_col),
                self.rc_to_pos(9 - t_rank, t_col))

    def bag_string(self):
        bag = dict(INITIAL_BAG)
        for ch in self.revealed_chars:
            if ch in bag:
                bag[ch] = max(0, bag[ch] - 1)
        return "".join(f"{k}{bag[k]}" for k in BAG_ORDER)

    # ★ SỬA 3+4: Build FEN động với log debug
    def get_current_fen(self, visible_board):
        """Build FEN cờ úp cho vị trí HIỆN TẠI, đã tính flip + side-to-move.
        
        Format: <board_fen> <bag_string> <side_to_move> - - 0 1
        - Board: quân úp = X (Đỏ) / x (Đen), quân lật = tên thật
        - BAG: số quân CHƯA LẬT còn lại
        - side_to_move: 'w' (Đỏ) hoặc 'b' (Đen) — bên sắp đi
        """
        board = [['.' for _ in range(9)] for _ in range(10)]
        n_masked = 0
        for pos, cell in enumerate(visible_board.cells):
            if cell == '.':
                continue
            fen_row, col = self.pos_to_rc(pos)
            if not (0 <= fen_row < 10 and 0 <= col < 9):
                continue
            # Nếu pos vẫn còn úp → dùng X/x (giữ màu từ cell)
            if pos in self.dark_positions:
                fen_char = 'X' if cell.isupper() else 'x'
                n_masked += 1
            else:
                fen_char = cell
            board[fen_row][col] = fen_char
        fen_rows = []
        for row in board:
            fen_row = ""
            empty = 0
            for cell in row:
                if cell == '.':
                    empty += 1
                else:
                    if empty > 0:
                        fen_row += str(empty)
                        empty = 0
                    fen_row += cell
            if empty > 0:
                fen_row += str(empty)
            fen_rows.append(fen_row)
        board_fen = '/'.join(fen_rows)
        bag = self.bag_string()
        side = self.side_to_move
        fen = f"{board_fen} {bag} {side} - - 0 1"
        moves = [m for m in self.uci_moves if UCI_MOVE_WITH_SUFFIX_RE.match(m)]
        # ★ SỬA 4: log debug
        n_X = board_fen.count('X') + board_fen.count('x')
        print(f"[FEN-BUILD] dark={len(self.dark_positions)} | masked={n_masked} | "
              f"X/x={n_X} | side={side} | bag={bag[:20]}...", flush=True)
        return fen, moves

    def set_base(self, board_fen, side='w'):
        board_fen = board_fen.split(' ')[0] if ' ' in board_fen else board_fen
        self.start_fen = board_fen
        self.start_side = side
        self.side_to_move = side
        self.uci_moves = []
        self.revealed_chars = []
        self.dark_positions.clear()
        self.true_faces.clear()
        self.lost_pieces.clear()

    def record_move(self, mv, revealed_char=None):
        uci = mv + (revealed_char or "")
        self.uci_moves.append(uci)
        if revealed_char:
            self.revealed_chars.append(revealed_char)
        self.side_to_move = 'b' if self.side_to_move == 'w' else 'w'
        return uci

    def set_my_slot(self, slot_id, first_turn_slot_id):
        self.my_slot_id = slot_id
        self.first_turn_slot_id = first_turn_slot_id
        self.is_red = (self.my_slot_id == self.first_turn_slot_id)

    def detect_flip(self, pieces):
        red_rows, black_rows = [], []
        red_king_row = black_king_row = None
        for sid, face, position, is_open in pieces:
            if position is None or position < 0 or position >= 90:
                continue
            row = position // 9
            color = face[0] if face else (sid[0] if sid else 'r')
            ptype = int(face[1]) if len(face) > 1 and str(face[1]).isdigit() else 0
            if ptype == 0 and len(sid) > 1 and str(sid[1]).isdigit():
                ptype = int(sid[1])
            if color == 'r':
                red_rows.append(row)
                if ptype == 1: red_king_row = row
            else:
                black_rows.append(row)
                if ptype == 1: black_king_row = row
        if red_king_row is not None and black_king_row is not None:
            self.flip = red_king_row < black_king_row
            self.flip_known = True
        elif red_king_row is not None:
            self.flip = red_king_row <= 4
            self.flip_known = True
        elif black_king_row is not None:
            self.flip = black_king_row >= 5
            self.flip_known = True
        elif red_rows and black_rows:
            self.flip = (sum(red_rows) / len(red_rows)) < (sum(black_rows) / len(black_rows))
            self.flip_known = True
        else:
            self.flip = bool(self.is_red)
            self.flip_known = False
        return self.flip

    def sanity_check_fen(self, board_fen):
        rows = board_fen.split(' ')[0].split('/')
        if len(rows) != 10:
            return False, f"FEN có {len(rows)} hàng"
        K_row = k_row = None
        for i, r in enumerate(rows):
            if 'K' in r: K_row = i
            if 'k' in r: k_row = i
        if K_row is None or k_row is None:
            return False, "thiếu tướng"
        if K_row < 7 or k_row > 2:
            return False, f"tướng sai chiều (K hàng {K_row}, k hàng {k_row})"
        return True, "ok"


class VisibleBoard:
    """Tracks actual piece positions."""
    TYPE_TO_FEN = {1: 'k', 2: 'a', 3: 'b', 4: 'r', 5: 'c', 6: 'n', 7: 'p'}

    def __init__(self):
        self.cells = ['.'] * 90
        self.side_to_move = 'w'

    def reset(self):
        self.cells = ['.'] * 90
        self.side_to_move = 'w'

    def set_from_pieces(self, pieces, flip):
        self.cells = ['.'] * 90
        for sid, face, position, is_open in pieces:
            if position < 0 or position >= 90:
                continue
            if len(face) >= 2:
                color = face[0]
                piece_type = int(face[1])
                fen_char = self.TYPE_TO_FEN.get(piece_type, '?')
                if color == 'r':
                    fen_char = fen_char.upper()
                self.cells[position] = fen_char

    def apply_move(self, source_pos, target_pos):
        if 0 <= source_pos < 90 and 0 <= target_pos < 90:
            self.cells[target_pos] = self.cells[source_pos]
            self.cells[source_pos] = '.'

    def flip_side(self):
        self.side_to_move = 'b' if self.side_to_move == 'w' else 'w'


class JieqiEngine:
    def __init__(self):
        self.proc = None
        self.binary_path = None
        self.engine_lock = threading.Lock()
        self._readyok = False
        self._latest_bestmove = None
        self._engine_searching = False
        self._last_depth = "?"
        self._last_score = "?"
        self._restart_count = 0
        self._stdout_lines = []
        self._lines_lock = threading.Lock()
        for path in PIKAJIEQI_BINARY_CANDIDATES:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                self.binary_path = path
                break
        if not self.binary_path:
            print(f"[ENGINE] ❌ Không tìm thấy pikajieqi-native binary. Đã thử: {PIKAJIEQI_BINARY_CANDIDATES}")
            self.engine = False
            return
        print(f"[ENGINE] 🎯 pikajieqi-native = {self.binary_path}")

        self._init_engine()
        self.engine = self.proc is not None

    def _init_engine(self):
        if self.proc:
            try:
                self.proc.stdin.write("quit\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=2)
            except Exception:
                pass
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None
        try:
            self.proc = subprocess.Popen(
                [self.binary_path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
                cwd=os.path.dirname(self.binary_path),
            )
        except Exception as e:
            print(f"[ENGINE] ❌ Popen error: {e}")
            return

        def consume_stderr(proc):
            try:
                while proc.poll() is None:
                    line = proc.stderr.readline()
                    if not line: break
                    line = line.strip()
                    if line:
                        print(f"[PKJQ-DBG] {line}")
            except Exception:
                pass
        threading.Thread(target=consume_stderr, args=(self.proc,), daemon=True).start()

        def consume_stdout(proc):
            while True:
                try:
                    line = proc.stdout.readline()
                except Exception:
                    break
                if not line: break
                line = line.strip()
                if not line: continue
                with self._lines_lock:
                    self._stdout_lines.append(line)
                    if len(self._stdout_lines) > 200:
                        self._stdout_lines = self._stdout_lines[-100:]
                if line == "readyok":
                    self._readyok = True
                if line.startswith("bestmove"):
                    self._latest_bestmove = line
                    self._engine_searching = False
        threading.Thread(target=consume_stdout, args=(self.proc,), daemon=True).start()

        with self.engine_lock:
            try:
                self.proc.stdin.write("uci\n")
                self.proc.stdin.flush()
            except Exception as e:
                print(f"[ENGINE] ❌ Send uci error: {e}")
                return
        if not self._wait_for_line("uciok", timeout=30):
            print("[ENGINE] ❌ uciok timeout")
            self._kill()
            return
        nnue_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pikafish.nnue")
        with self.engine_lock:
            try:
                _threads = max(1, min(2, (os.cpu_count() or 2) - 1))
                self.proc.stdin.write(f"setoption name Threads value {_threads}\n")
                self.proc.stdin.write("setoption name Hash value 128\n")
                self.proc.stdin.write(f"setoption name EvalFile value {nnue_path}\n")
                self.proc.stdin.write("setoption name MultiPV value 1\n")
                self.proc.stdin.write("isready\n")
                self.proc.stdin.flush()
            except Exception as e:
                print(f"[ENGINE] ❌ Config error: {e}")
                return
        if not self._wait_for_readyok(timeout=30):
            print("[ENGINE] ❌ readyok timeout")
            self._kill()
            return
        print("[ENGINE] ✅ pikajieqi-native ready")

    def _wait_for_line(self, prefix, timeout=10):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc and self.proc.poll() is not None:
                return False
            with self._lines_lock:
                for l in self._stdout_lines:
                    if l.startswith(prefix) or l == prefix:
                        return True
            time.sleep(0.05)
        return False

    def _wait_for_readyok(self, timeout=10):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._readyok: return True
            if self.proc and self.proc.poll() is not None: return False
            time.sleep(0.05)
        return False

    def _kill(self):
        if self.proc:
            try: self.proc.kill()
            except: pass
            self.proc = None

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def restart(self):
        if self._restart_count >= MAX_ENGINE_RESTARTS_PER_GAME:
            print(f"[ENGINE] ❌ Quá {MAX_ENGINE_RESTARTS_PER_GAME} restarts")
            return False
        self._restart_count += 1
        print(f"[ENGINE] 🔄 Restart (#{self._restart_count})")
        self._init_engine()
        return self.alive()

    # ★ SỬA 3: Dùng position fen + banmoves
    def get_best_move(self, fen, moves, movetime_ms=3000, banmoves=None):
        """Gửi position fen (có BAG) cho PikaJieQi."""
        if not self.alive():
            if not self.restart():
                return None
        self._latest_bestmove = None
        self._engine_searching = True
        with self._lines_lock:
            self._stdout_lines.clear()
        try:
            cmd = f"position fen {fen}"
            if moves:
                cmd += " moves " + " ".join(moves)
            print(f"[ENGINE-CMD] {cmd[:160]}...", flush=True)
            with self.engine_lock:
                self.proc.stdin.write(cmd + "\n")
                self.proc.stdin.flush()
                if banmoves:
                    try:
                        self.proc.stdin.write("banmoves " + " ".join(banmoves) + "\n")
                        self.proc.stdin.flush()
                    except Exception:
                        pass
                self.proc.stdin.write("go infinite\n")
                self.proc.stdin.flush()
        except Exception as e:
            print(f"[ENGINE] Send error: {e}")
            self._engine_searching = False
            return None
        time.sleep(movetime_ms / 1000.0)
        try:
            with self.engine_lock:
                self.proc.stdin.write("stop\n")
                self.proc.stdin.flush()
        except Exception:
            pass
        t0 = time.time()
        timeout = 2.0
        while time.time() - t0 < timeout:
            if self._latest_bestmove:
                self._engine_searching = False
                with self._lines_lock:
                    for l in reversed(self._stdout_lines):
                        if l.startswith("info") and "depth" in l:
                            m = re.search(r'depth (\d+)', l)
                            if m: self._last_depth = m.group(1)
                            sm = re.search(r'score (cp|mate) (-?\d+)', l)
                            if sm:
                                if sm.group(1) == "mate":
                                    self._last_score = f"M{sm.group(2)}"
                                else:
                                    self._last_score = f"{int(sm.group(2))/100:+.2f}"
                            break
                return self._latest_bestmove
            if not self.alive():
                self._engine_searching = False
                return None
            time.sleep(0.02)
        print(f"[ENGINE] bestmove timeout after stop")
        self._engine_searching = False
        return self._latest_bestmove


class JieqiCupBot:
    def __init__(self):
        self.conn = Conn()
        self.board = XiangqiBoardTracker()
        self.visible_board = VisibleBoard()
        self.engine = None
        self.ws = None
        self.connected = False
        self.logged_in = False
        self.in_game = False
        self._joining_table = False
        self._last_quick_play_time = 0
        self._QUICK_PLAY_INTERVAL = 3.0
        self.ROOM_LIST = ["0", "1", "2", "3"]
        _bot_num = re.search(r"\d+", USER)
        _offset = int(_bot_num.group(0)) if _bot_num else 0
        self._search_room_idx = _offset % len(self.ROOM_LIST)
        self._quick_play_attempts = 0
        self._sit_alone_since = None
        self._table_created_by_me = False
        self.bet_amts = []
        self._resolved_bet_id = None
        self._bet_amts_loaded = False
        self.fixed_pawn_positions = set()
        self.last_action_timestamp = time.time()
        self.last_recv_timestamp = time.time()
        self.slot_players = {}
        self._pending_kick_id = None
        self._table_path = None
        self._table_path_ts = 0.0
        self._reconnect_streak = 0
        self._connected_since = 0.0
        self._enter_fail_at = 0.0
        self.player_names = {}
        self._thinking = False
        self._turn_started_at = 0.0
        self._turn_deadline = 0.0
        self._played_this_turn = False
        self._last_sent_move = None
        self._rejected_moves = set()
        self._play_reject_count = 0
        self._move_lock = threading.Lock()
        self._last_move_uci = None
        self._last_move_time = 0.0
        self._move_recv_count = 0
        self._move_skip_count = 0
        self._move_error_count = 0
        self._game_seq = 0
        self._moves_len_at_turn_start = 0

        self.engine = JieqiEngine()
        if not self.engine.engine:
            print("[BOT] ❌ Engine not ready — bot will not be able to think")
        else:
            print("[BOT] ✅ Engine ready")

    def connect(self):
        import websocket
        self.connected = False
        self.ws = websocket.WebSocketApp(
            WS_URL, cookie=COOKIE,
            on_open=self._on_open, on_message=self._on_message,
            on_error=self._on_error, on_close=self._on_close,
            header={"Origin": "https://gamevh.net"})
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
        if isinstance(message, bytes):
            try:
                from ws_frame_dump import log_incoming_frame
                log_incoming_frame(message)
            except Exception:
                pass
            self._handle_binary_message(message)

    def _on_error(self, ws, error):
        print(f"[WS] ❌ Error: {type(error).__name__}: {error}")

    def _on_close(self, ws, code, msg):
        if self.board.is_playing:
            print(f"[WS] ⚠️ DISCONNECTED INGAME (code={code})")
        else:
            print(f"[WS] Closed (code={code})")
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
        self._thinking = False
        self._played_this_turn = False
        self.board.reset()
        self.visible_board.reset()

    def send_message(self, cmd, data=b''):
        if self.ws and self.connected:
            try:
                self.ws.send(self.conn.pack(cmd, data), opcode=0x2)
            except Exception:
                pass

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
        data.extend(self.conn.pack_ascii(path or PLACE_PATH))
        data.extend(self.conn.pack_string(""))
        data.extend(self.conn.pack_byte(mode))
        self.send_message("ENTER_PLACE", bytes(data))

    def send_list_bet_amt(self):
        self.send_message("LIST_BET_AMT")

    def get_1k_to_5k_bet_objs(self):
        if not self.bet_amts: return []
        valid = [ba for ba in self.bet_amts if 1000 <= ba["value"] <= 5000]
        if valid:
            random.shuffle(valid)
            return valid
        return [self.bet_amts[0]] if self.bet_amts else []

    def is_family_bot(self, name):
        if not name or name.strip().lower() == CURRENT_PLAYER_NICKNAME.lower():
            return False
        return "." in name

    def leave_table(self):
        if self.board.is_playing:
            print("[TABLE] ⚠️ Ingame, cannot leave!")
            return
        print("[TABLE] 🚪 Leaving...")
        if self._table_path:
            unregister_bot_table(self._table_path)
        self.in_game = False
        self._joining_table = False
        self._table_path = None
        self._table_created_by_me = False
        self._sit_alone_since = None
        self.slot_players.clear()
        self.board.reset()
        self.visible_board.reset()
        self._quick_play_attempts = 0
        self._enter_fail_at = 0.0
        self.send_enter_place(PLACE_PATH)

    def resolve_bet_amt_id(self):
        if not self.bet_amts: return None
        exact = [ba for ba in self.bet_amts if ba["value"] == BOT_BET_XU]
        if exact: return exact[0]['id']
        above = [ba for ba in self.bet_amts if ba["value"] >= BOT_BET_XU]
        if above: return min(above, key=lambda x: x['value'])['id']
        return self.bet_amts[-1]['id'] if self.bet_amts else 0

    def send_create_table(self, bet_amt_id=None):
        now = time.time()
        if now - self._last_quick_play_time < self._QUICK_PLAY_INTERVAL:
            return
        self._last_quick_play_time = now
        if bet_amt_id is None:
            bet_amt_id = self._resolved_bet_id if self._resolved_bet_id is not None else self.resolve_bet_amt_id()
        if bet_amt_id is None:
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
        self.send_message("CREATE_RULE", bytes(data))

    def send_quick_play(self, room_id="", bet_amt_id=-1):
        now = time.time()
        if now - self._last_quick_play_time < self._QUICK_PLAY_INTERVAL:
            return
        self._last_quick_play_time = now
        data = bytearray()
        data.extend(self.conn.pack_ascii(room_id))
        data.extend(self.conn.pack_byte(bet_amt_id))
        self.send_message("QUICK_PLAY", bytes(data))

    def send_play(self, source_pos, target_pos):
        self._played_this_turn = True
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
        print(f"[KICK] Sending playerId={player_id}")
        self.send_message(410, bytes(data))

    def send_ready(self, is_ready=1):
        if self.board.is_playing: return
        print("[GAME] ⏳ READY")
        data = bytearray()
        data.extend(self.conn.pack_byte(is_ready))
        self.send_message("SET_READY", bytes(data))

    def _handle_binary_message(self, data):
        cmd_for_log = "?"
        try:
            msg = InboundMessage(data)
            cmd = msg.command
            cmd_for_log = cmd
            if cmd == "PING":
                self.send_message("PONG")
            elif cmd == "LOGIN":
                self._handle_login_response(msg)
            elif cmd == "ENTER_PLACE":
                self._handle_enter_place_response(msg)
            elif cmd == "QUICK_PLAY":
                self._handle_quick_play_response(msg)
            elif cmd == "LIST_BET_AMT":
                self._handle_list_bet_amt_response(msg)
            elif cmd == "CREATE_RULE":
                self._handle_create_rule_response(msg)
            elif cmd == "SLOT_IN_TABLE_CHANGED":
                self._handle_slot_changed(msg)
            elif cmd == "PLAYER_ENTERED":
                self._handle_player_entered(msg)
            elif cmd == "START_MATCH":
                self._handle_start_match(msg)
            elif cmd == "MOVE":
                self._handle_move(msg)
            elif cmd == "PLAY" or cmd == "502":
                self._handle_play_response(msg)
            elif cmd == "SET_TURN":
                self._handle_set_turn(msg)
            elif cmd == "GAMEOVER":
                self._handle_gameover(msg)
            elif cmd == "KICK_PLAYER":
                self._handle_kick_response(msg)
            elif cmd == "ALERT":
                try:
                    print(f"[SERVER] ALERT: {msg.read_string()}")
                except Exception:
                    pass
        except Exception as e:
            print(f"[RECV ERROR] cmd={cmd_for_log} err={e}", flush=True)
            traceback.print_exc()

    def _handle_login_response(self, msg):
        if msg.read_byte() == 0:
            self.logged_in = True
            path = msg.read_string()
            if path == 'REFRESH':
                fetch_session_info()
                self._send_login()
                return
            self.send_enter_place()

    def _handle_enter_place_response(self, msg):
        status = msg.read_byte()
        if status != 0:
            if self._joining_table:
                print(f"[TABLE] ENTER_PLACE status={status}")
                self._joining_table = False
                self.in_game = True
                self._enter_fail_at = time.time()
                threading.Thread(target=lambda: (time.sleep(3.0), self.send_ready(1)),
                                 daemon=True).start()
            return
        if self._joining_table:
            if is_block_software_message(msg.data):
                print("[GAME] 🛡️ Anti-software")
            self._joining_table = False
            self.in_game = True
            self._enter_fail_at = 0.0
            self.last_action_timestamp = time.time()
            threading.Thread(target=lambda: (time.sleep(3.0), self.send_ready(1)),
                             daemon=True).start()
        elif not self.in_game:
            if self._table_path and time.time() - self._table_path_ts < 180:
                print(f"[TABLE] Rejoin: {self._table_path}")
                self.in_game = True
                self._joining_table = True
                path = self._table_path
                threading.Thread(target=lambda: (time.sleep(0.5),
                                                  self.send_enter_place(path=path, mode=1)),
                                 daemon=True).start()
                return
            self._bet_amts_loaded = False
            self._resolved_bet_id = None
            self.send_list_bet_amt()

    def _handle_quick_play_response(self, msg):
        status = msg.read_byte()
        if status == 0:
            table_path = msg.read_ascii()
            active_tables = get_active_bot_tables()
            if table_path in active_tables:
                owner = active_tables[table_path].get("user", "")
                if owner.lower() != USER.lower():
                    print(f"[AVOID] 🛑 Bot table {owner}")
                    self.in_game = False
                    self._joining_table = False
                    return
            self.in_game = True
            self._joining_table = True
            self._table_created_by_me = False
            self._sit_alone_since = time.time()
            self._table_path = table_path
            self._table_path_ts = time.time()
            register_bot_table(table_path, USER)
            print(f"[SEARCH] ✅ Table: {table_path}")
            threading.Thread(target=lambda: (time.sleep(0.5),
                                              self.send_enter_place(path=table_path, mode=1)),
                             daemon=True).start()
        else:
            self._joining_table = False

    def _handle_list_bet_amt_response(self, msg):
        if msg.read_byte() != 0: return
        count = msg.read_byte()
        self.bet_amts = [{"id": i, "value": msg.read_int()} for i in range(count)]
        self._resolved_bet_id = self.resolve_bet_amt_id()
        self._bet_amts_loaded = True

    def _handle_create_rule_response(self, msg):
        status = msg.read_byte()
        if status == 0:
            table_path = msg.read_ascii()
            self.in_game = True
            self._joining_table = True
            self._table_created_by_me = True
            self._sit_alone_since = time.time()
            self._table_path = table_path
            self._table_path_ts = time.time()
            register_bot_table(table_path, USER)
            print(f"[CREATE] 🎉 {table_path}")
            threading.Thread(target=lambda: (time.sleep(0.5),
                                              self.send_enter_place(path=table_path, mode=1)),
                             daemon=True).start()
        else:
            print(f"[CREATE] ❌ status={status}")
            self._joining_table = False

    def _handle_player_entered(self, msg):
        try:
            _ = msg.read_byte()
            pid = msg.read_long()
            name = msg.read_string()
            if pid > 0 and pid != CURRENT_PLAYER_ID:
                self.player_names[pid] = name
                print(f"[PLAYER] 👤 '{name}' (id={pid})")
                if not self.board.is_playing and self.is_family_bot(name):
                    if self.opponent_player_id() == pid:
                        print(f"[AVOID] Ally bot -> leave")
                        self.leave_table()
        except Exception:
            pass

    def _handle_slot_changed(self, msg):
        try:
            _ = msg.read_string()
            slot_id = msg.read_byte()
            msg.read_long(); msg.read_long(); msg.read_byte(); msg.read_short()
            msg.read_ascii(); msg.read_byte(); msg.read_byte()
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
                    print(f"[TABLE] Opponent: pid={player_id}{f', {name}' if name else ''}")
                    if not self.board.is_playing and self.is_family_bot(name):
                        print(f"[AVOID] Ally -> leave")
                        self.leave_table()
                        return
                    self._sit_alone_since = None
                    if not self.board.is_playing:
                        threading.Thread(target=lambda: (time.sleep(3.0), self.send_ready(1)),
                                         daemon=True).start()
                else:
                    if not self.board.is_playing and self.opponent_player_id() is None:
                        print(f"[TABLE] No opponent, waiting {int(SIT_ALONE_TIMEOUT)}s...")
                        self._sit_alone_since = time.time()
        except Exception:
            pass

    def _handle_start_match(self, msg):
        self._game_seq += 1
        print(f"[GAME] 🎮 Match #{self._game_seq}")
        if self.engine and hasattr(self.engine, "_restart_count"):
            self.engine._restart_count = 0
        self._thinking = False
        self._played_this_turn = False
        self._turn_started_at = 0.0
        self._turn_deadline = 0.0
        self._play_reject_count = 0
        self._rejected_moves = set()
        self._last_sent_move = None
        self._reconnect_streak = 0
        self._enter_fail_at = 0.0
        self._sit_alone_since = None
        self._move_recv_count = 0
        self._move_skip_count = 0
        self._move_error_count = 0
        self._last_move_uci = None
        self._last_move_time = 0.0
        self.board.reset()
        self.visible_board.reset()
        self.fixed_pawn_positions.clear()
        self.board.is_playing = True
        self.in_game = True
        self._joining_table = False
        self.last_action_timestamp = time.time()
        if self.engine and self.engine.alive():
            try:
                with self.engine.engine_lock:
                    self.engine.proc.stdin.write("ucinewgame\n")
                    self.engine.proc.stdin.flush()
            except Exception:
                pass
        try:
            player_count = msg.read_byte()
            for _ in range(player_count):
                msg.read_byte(); msg.read_int()
            piece_count = msg.read_byte()
            board_pieces = []
            for _ in range(piece_count):
                raw_sid = msg.read_byte(); raw_face = msg.read_byte()
                pos = msg.read_byte(); is_open = msg.read_byte()
                board_pieces.append((self._decode_piece_id(raw_sid),
                                     self._decode_piece_id(raw_face),
                                     pos, is_open))
            msg.read_byte(); mystery_count = msg.read_byte()
            for _ in range(mystery_count): msg.read_byte()
            msg.read_byte(); msg.read_byte()
            first_turn_slot_id = msg.read_byte()
            my_slot_id = msg.read_byte()
            if my_slot_id < 0 or my_slot_id == 255:
                my_slot_id = (self.board.my_slot_id
                              if self.board.my_slot_id >= 0
                              else first_turn_slot_id)
            self.board.set_my_slot(my_slot_id, first_turn_slot_id)
            _built_fen = self._build_fen_from_pieces(board_pieces)
            _ok, _why = self.board.sanity_check_fen(_built_fen)
            if not _ok:
                print(f"[FEN] ⚠️ Bad orientation ({_why}) -> flip")
                self.board.flip = not self.board.flip
                _rebuilt = self._rebuild_fen_with_current_flip(board_pieces)
                _ok2, _why2 = self.board.sanity_check_fen(_rebuilt)
                if _ok2:
                    _built_fen = _rebuilt
                    print(f"[FEN] ✅ flip={self.board.flip}")
                else:
                    print(f"[FEN] ❌ Still bad ({_why2})")
                    self.board.flip = not self.board.flip

            # ★ Set visible board từ piece data (face thật)
            self.visible_board.set_from_pieces(board_pieces, self.board.flip)
            self.visible_board.side_to_move = 'w'

            # ★ SỬA 3+4: Set base TRƯỚC, rồi restore dark_positions + true_faces
            _first_side = 'w' if first_turn_slot_id == 0 else 'b'
            self.board.set_base(_built_fen, _first_side)
            # ★ SỬA 4: Restore CẢ dark_positions VÀ true_faces sau set_base
            for sid, face, position, is_open in board_pieces:
                if position < 0 or position >= 90:
                    continue
                # ★ Restore dark_positions
                if not is_open:
                    self.board.dark_positions.add(position)
                # ★ Restore true_faces
                if len(face) > 1:
                    color = face[0]
                    piece_type = int(face[1])
                    fen_char = TYPE_TO_FEN.get(piece_type, '?')
                    if color == 'r':
                        fen_char = fen_char.upper()
                    self.board.set_true_face(position, fen_char)
                # Đánh dấu tốt cố định
                piece_type = int(face[1]) if len(face) > 1 else 0
                if piece_type == 7 and position not in STANDARD_PAWN_POSITIONS:
                    self.fixed_pawn_positions.add(position)

            if self.board.true_faces:
                n_dark_known = sum(1 for p in self.board.dark_positions
                                   if p in self.board.true_faces)
                print(f"[TRACK] 🧠 Đã giải mã {len(self.board.true_faces)} quân, "
                      f"trong đó {n_dark_known} quân úp | "
                      f"dark_positions={len(self.board.dark_positions)}")
            if self.fixed_pawn_positions:
                print(f"[GAME] 🛡️ {len(self.fixed_pawn_positions)} locked pawns")

            # ★ SỬA 4: Verify FEN có X/x
            _init_fen, _ = self.board.get_current_fen(self.visible_board)
            print(f"[START] BAG={self.board.bag_string()}")
            print(f"[START] dark={len(self.board.dark_positions)} | "
                  f"my_slot={my_slot_id} | first={first_turn_slot_id} | "
                  f"flip={self.board.flip} | side={self.board.side_to_move}")
        except Exception as e:
            print(f"[START_MATCH ERROR] {e}")
            traceback.print_exc()

    def _build_fen_from_pieces(self, pieces):
        self.board.detect_flip(pieces)
        return self._rebuild_fen_with_current_flip(pieces)

    def _rebuild_fen_with_current_flip(self, pieces):
        board = [['.' for _ in range(9)] for _ in range(10)]
        for sid, face, position, is_open in pieces:
            if position < 0 or position >= 90: continue
            fen_row, col = self.board.pos_to_rc(position)
            if len(face) > 1:
                color = face[0]; piece_type = int(face[1])
                fen_char = TYPE_TO_FEN.get(piece_type, '?')
                if color == 'r': fen_char = fen_char.upper()
            else:
                fen_char = 'X' if sid.startswith('r') else 'x'
            board[fen_row][col] = fen_char
        fen_rows = []
        for row in board:
            fen_row = ""; empty = 0
            for cell in row:
                if cell == '.':
                    empty += 1
                else:
                    if empty > 0: fen_row += str(empty); empty = 0
                    fen_row += cell
            if empty > 0: fen_row += str(empty)
            fen_rows.append(fen_row)
        return '/'.join(fen_rows) + ' w'

    PIECE_TYPE_MAP = {1: 'k', 2: 'a', 3: 'b', 4: 'r', 5: 'c', 6: 'n', 7: 'p'}

    @classmethod
    def _sid_to_fen_char(cls, byte_val):
        v = byte_val - 256 if byte_val > 127 else byte_val
        if v == 0: return None
        ch = cls.PIECE_TYPE_MAP.get(abs(v) >> 3)
        if not ch: return None
        return ch.upper() if v > 0 else ch

    def _handle_move(self, msg):
        with self._move_lock:
            self._move_recv_count += 1
            try:
                if not self.board.is_playing:
                    self._move_skip_count += 1
                    return
                source_pos = msg.read_byte()
                target_pos = msg.read_byte()
                if not (0 <= source_pos < 90 and 0 <= target_pos < 90):
                    self._move_skip_count += 1
                    return
                engine_move = self.board.pos_to_engine_move(source_pos, target_pos)
                self.last_action_timestamp = time.time()
                if not UCI_MOVE_RE.match(engine_move):
                    self._move_error_count += 1
                    return
                rest = list(msg.data[msg.offset:]) if msg.offset < len(msg.data) else []
                mover_dark = source_pos in self.board.dark_positions
                is_flip_move = (source_pos == target_pos)
                captured_dark = target_pos in self.board.dark_positions
                captured_face_known = self.board.true_face_at(target_pos)

                revealed_char = None
                if (mover_dark or is_flip_move) and rest and rest[0] > 0 and len(rest) >= 3:
                    cand = self._sid_to_fen_char(rest[2])
                    if cand and cand not in ('k', 'K'):
                        revealed_char = cand

                # ★ SỬA 2: Xử lý quân úp BỊ ĂN
                if captured_dark and captured_face_known:
                    server_reveal = (self._sid_to_fen_char(rest[2])
                                     if rest and len(rest) >= 3 and rest[0] > 0 else None)
                    print(f"[CAPTURE] 🎯 Quân úp ở {target_pos} bị ăn, "
                          f"thực tế là '{captured_face_known}' "
                          f"(server_reveal={server_reveal or 'None'})", flush=True)
                    if not revealed_char:
                        self.board.revealed_chars.append(captured_face_known)

                self.board.dark_positions.discard(source_pos)
                self.board.dark_positions.discard(target_pos)
                self.board.move_true_face(source_pos, target_pos)

                full_uci = engine_move + (revealed_char or "")
                now = time.time()
                if (self._last_move_uci == full_uci
                        and (now - self._last_move_time) < MOVE_DEDUP_WINDOW):
                    self._move_skip_count += 1
                    return
                self.board.record_move(engine_move, revealed_char)
                # ★ Update visible board
                src_pos, tgt_pos = self.board.engine_move_to_pos(engine_move)
                self.visible_board.apply_move(src_pos, tgt_pos)
                self.visible_board.flip_side()
                self._last_move_uci = full_uci
                self._last_move_time = now
                self._played_this_turn = False
                print(f"[MOVE] #{self._move_recv_count} {engine_move} -> "
                      f"'{full_uci}' | uci={len(self.board.uci_moves)} "
                      f"revealed={len(self.board.revealed_chars)} "
                      f"| BAG={self.board.bag_string()} "
                      f"| side={self.board.side_to_move} "
                      f"| dark={len(self.board.dark_positions)}", flush=True)
            except Exception as e:
                self._move_error_count += 1
                print(f"[MOVE ERROR] err={e}", flush=True)
                traceback.print_exc()

    def _handle_play_response(self, msg):
        status = msg.read_byte()
        err_text = ""
        try:
            if msg.rem() >= 2: err_text = msg.read_string()
        except Exception: pass
        print(f"[PLAY-RESP] status={status} err={err_text!r}", flush=True)
        if status != 0:
            self._play_reject_count += 1
            self.board.is_my_turn = True
            self._played_this_turn = False
            print(f"[PLAY] ⚠️ Reject #{self._play_reject_count} err={err_text!r}", flush=True)
            if self._last_sent_move:
                self._rejected_moves.add(self._last_sent_move)
            if self._play_reject_count <= 3:
                threading.Thread(target=lambda: (time.sleep(0.5), self._make_auto_move()),
                                 daemon=True).start()
        else:
            self._play_reject_count = 0
            self._rejected_moves.clear()

    def _handle_set_turn(self, msg):
        try:
            slot_id = msg.read_byte()
            try:
                turn_timeout = msg.read_short()
            except Exception:
                turn_timeout = 0
            if slot_id == -2 or slot_id == -1 or not self.board.is_playing: return
            self.turn_timeout = turn_timeout
            was_my_turn = self.board.is_my_turn
            self.board.is_my_turn = (slot_id == self.board.my_slot_id)

            # ★ SỬA 3: Cập nhật side_to_move theo slot_id
            if slot_id == self.board.first_turn_slot_id:
                self.board.side_to_move = 'w'
            else:
                self.board.side_to_move = 'b'

            self.last_action_timestamp = time.time()
            if not self.board.is_my_turn: return
            self._turn_started_at = time.time()
            self._turn_deadline = self._turn_started_at + max(turn_timeout - 5, 10)
            self._played_this_turn = False
            self._moves_len_at_turn_start = len(self.board.uci_moves)
            if not was_my_turn:
                print(f"[TURN] My turn | uci={len(self.board.uci_moves)} "
                      f"| timeout={turn_timeout}s | side={self.board.side_to_move}", flush=True)
            threading.Thread(target=self._make_auto_move, daemon=True).start()
        except Exception as e:
            print(f"[SET_TURN ERROR] {e}")
            traceback.print_exc()

    def _handle_kick_response(self, msg):
        try:
            status = msg.read_byte(); content = msg.read_string()
        except Exception: status, content = None, ""
        if self._pending_kick_id is not None:
            pid = self._pending_kick_id; self._pending_kick_id = None
            print(f"[KICK] {'✅' if status == 0 else '❌'} pid={pid}")
            return
        print(f"[KICK] Kicked: {content}")
        self.in_game = False; self._joining_table = False
        self._table_path = None; self.board.reset()

    def _handle_gameover(self, msg):
        my_result, results = None, {}
        try:
            count = msg.read_byte()
            for _ in range(count):
                sid = msg.read_byte(); res = msg.read_byte(); msg.read_long()
                results[sid] = res
                if sid == self.board.my_slot_id: my_result = res
        except Exception:
            results = {}

        bot_won = my_result in (1, 11)
        bot_lost = my_result in (2, 4, 12)
        if bot_won:
            print("[GAME] 🏁 WIN")
        elif bot_lost:
            print("[GAME] 🏁 LOSE")
        elif my_result is None:
            print("[GAME] 🏁 END")
        else:
            print("[GAME] 🏁 DRAW")

        print(f"[SUMMARY] #{self._game_seq} | uci={len(self.board.uci_moves)} | "
              f"revealed={len(self.board.revealed_chars)} | "
              f"recv={self._move_recv_count} skip={self._move_skip_count} "
              f"err={self._move_error_count} reject={self._play_reject_count}",
              flush=True)

        self.board.is_playing = False
        self.board.is_my_turn = False
        self.fixed_pawn_positions.clear()
        self.board.reset()
        self.visible_board.reset()
        self.in_game = True
        self._joining_table = False
        self.last_action_timestamp = time.time()
        self._thinking = False
        self._played_this_turn = False
        self._turn_started_at = 0.0
        self._turn_deadline = 0.0

        if self.engine and self.engine.alive():
            try:
                with self.engine.engine_lock:
                    self.engine.proc.stdin.write("ucinewgame\n")
                    self.engine.proc.stdin.flush()
            except Exception:
                pass

        def after_gameover():
            print("[GAME] 🔄 Ở lại bàn, sẵn sàng cho ván mới...")
            time.sleep(3.0)
            if self.connected and not self.board.is_playing:
                self.send_ready(1)

        threading.Thread(target=after_gameover, daemon=True).start()

    def _make_auto_move(self):
        if not self.board.is_my_turn or not self.board.is_playing: return
        if self._thinking: return
        self._thinking = True
        try:
            self._do_auto_move()
        except Exception as e:
            print(f"[MOVE-THREAD] Crash: {e}")
            traceback.print_exc()
        finally:
            self._thinking = False

    def _do_auto_move(self):
        if not self.engine:
            print("[ENGINE] ❌ Không có engine")
            return
        if not self.engine.alive():
            print("[ENGINE] ❌ Engine chết giữa ván — thử restart...", flush=True)
            if not self.engine.restart():
                print("[ENGINE] ❌ Restart thất bại — bỏ lượt", flush=True)
                return
            print("[ENGINE] ✅ Restart OK — đi tiếp", flush=True)
        now = time.time()
        deadline = self._turn_deadline if self._turn_deadline > 0 else (now + MOVE_DEADLINE_SECONDS)
        remain = deadline - now
        if remain < 4.0:
            print(f"[TURN] Sắp hết giờ (remain={remain:.1f}s) — bỏ lượt")
            return
        movetime_ms = 3000
        # ★ SỬA 3: Build FEN động
        fen, moves = self.board.get_current_fen(self.visible_board)
        print(f"[ENGINE-IN] FEN: {fen}", flush=True)
        print(f"[ENGINE-IN] moves({len(moves)}), movetime={movetime_ms}ms, remain={remain:.1f}s",
              flush=True)
        print(f"[ENGINE-IN] my_turn={self.board.is_my_turn} | "
              f"side={self.board.side_to_move} | flip={self.board.flip} | "
              f"my_slot={self.board.my_slot_id} | first={self.board.first_turn_slot_id} | "
              f"dark={len(self.board.dark_positions)}", flush=True)

        # ★ SỬA 3: Truyền banmoves
        banmoves = list(self._rejected_moves) if self._rejected_moves else None
        raw = self.engine.get_best_move(fen, moves, movetime_ms=movetime_ms,
                                        banmoves=banmoves)
        if not raw:
            print("[ENGINE] -> no bestmove, retrying...", flush=True)
            if self.engine.restart():
                raw = self.engine.get_best_move(fen, moves, movetime_ms=movetime_ms,
                                                banmoves=banmoves)
            if not raw:
                print("[ENGINE] ❌ Không có nước — bỏ lượt", flush=True)
                return
        parts = raw.split()
        if len(parts) < 2: return
        best_move = parts[1]
        print(f"[ENGINE-OUT] bestmove: {best_move} [d{self.engine._last_depth} {self.engine._last_score}]",
              flush=True)
        if best_move in self._rejected_moves:
            print(f"[ENGINE] Rejected move {best_move}, skip", flush=True)
            return
        if best_move in ("(none)", "0000"):
            self.board.is_my_turn = False
            return
        try:
            source_pos, target_pos = self.board.engine_move_to_pos(best_move)
            _turn_start = self._turn_started_at if self._turn_started_at > 0 else time.time()
            _remain_min = MIN_MOVE_SECONDS - (time.time() - _turn_start)
            if _remain_min > 0: time.sleep(_remain_min)
            if not (self.board.is_my_turn and self.board.is_playing): return
            if self._played_this_turn: return
            print(f"-> Đi: {best_move} (pos {source_pos}->{target_pos}) "
                  f"| uci={len(self.board.uci_moves)}", flush=True)
            self._last_sent_move = best_move
            self.send_play(source_pos, target_pos)
        except Exception as e:
            print(f"[BOT ERROR] {e}")
            traceback.print_exc()

    def _decode_piece_id(self, encoded_id):
        color = 'r'
        if encoded_id < 0: encoded_id = -encoded_id; color = 'b'
        return f"{color}{encoded_id >> 3}{'' if (encoded_id & 7) == 0 else (encoded_id & 7)}"

    def start_keep_alive(self):
        def loop():
            while self.connected:
                time.sleep(10)
                if self.connected: self.send_message("PING")
        threading.Thread(target=loop, daemon=True).start()

    def run(self):
        print("[BOT] Khởi chạy cờ úp Jieqi v1.4 (position fen + FIX NotYourPiece)...")
        while True:
            try:
                now_ts = time.time()
                if self.connected and now_ts - self.last_recv_timestamp > 120:
                    print("[WS] 120s không nhận dữ liệu -> kết nối lại")
                    if self.ws:
                        try: self.ws.close()
                        except: pass
                    time.sleep(2)
                elif self.connected and self.board.is_playing:
                    if now_ts - self.last_action_timestamp > 300:
                        print("[WS] Ván treo 300s -> kết nối lại")
                        if self.ws:
                            try: self.ws.close()
                            except: pass
                        time.sleep(2)

                if not self.connected:
                    if self._reconnect_streak >= 3:
                        print(f"[BOT] ⚠️ Tài khoản {USER} có thể đăng nhập chỗ khác")
                    if self._reconnect_streak > 0:
                        delay = min(60, 5 * (2 ** min(self._reconnect_streak - 1, 4)))
                        print(f"[WS] Rớt liên tiếp {self._reconnect_streak} -> chờ {delay}s")
                        time.sleep(delay)
                    if not fetch_session_info():
                        time.sleep(5); continue
                    self.logged_in = False; self.in_game = False
                    self._joining_table = False
                    self._bet_amts_loaded = False
                    self._resolved_bet_id = None
                    self.bet_amts = []; self.fixed_pawn_positions = set()
                    self._thinking = False; self._played_this_turn = False
                    self.board.reset(); self.visible_board.reset()
                    if not self.connect():
                        time.sleep(5); continue
                    self.start_keep_alive()
                    time.sleep(2)

                if (self.board.is_playing and self.board.is_my_turn
                        and self._turn_deadline > 0
                        and time.time() > self._turn_deadline - 4.0
                        and not self._played_this_turn):
                    if not self._thinking:
                        print(f"[DEADLINE] Còn {int(self._turn_deadline - time.time())}s")
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
                                    print(f"[TABLE] Chờ {int(elapsed)}s -> rời bàn")
                                    self.leave_table()
                        else:
                            self._sit_alone_since = None

                if (self._enter_fail_at and self.in_game
                        and not self.board.is_playing
                        and time.time() - self._enter_fail_at > 60):
                    print("[TABLE] 60s không vào ván -> bỏ bàn cũ")
                    self._enter_fail_at = 0.0
                    self.leave_table()

                if (self.connected and self.logged_in and not self.in_game
                        and not self._joining_table):
                    now = time.time()
                    if now - self._last_quick_play_time >= self._QUICK_PLAY_INTERVAL:
                        if not self._bet_amts_loaded:
                            self.send_list_bet_amt()
                        elif BOT_USE_CREATE_TABLE:
                            bid = (self._resolved_bet_id
                                   if self._resolved_bet_id is not None
                                   else self.resolve_bet_amt_id())
                            print(f"[CREATE] 🪑 Tạo bàn {BOT_BET_XU} xu (bet_id={bid})")
                            self.send_create_table(bet_amt_id=bid)
                        else:
                            valid_bets = self.get_1k_to_5k_bet_objs()
                            if valid_bets:
                                bet_obj = random.choice(valid_bets)
                                room = random.choice(self.ROOM_LIST)
                                print(f"[SEARCH] 🔍 Dò bàn {bet_obj['value']} xu phòng '{room}'")
                                self.send_quick_play(room_id=room, bet_amt_id=bet_obj['id'])
                                self._quick_play_attempts += 1
                            else:
                                self.send_create_table()
                                self._quick_play_attempts = 0
                time.sleep(1)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[RUN ERROR] {e}")
                traceback.print_exc()
                time.sleep(5)

    def cleanup(self):
        if self.engine:
            try:
                if self.engine.proc:
                    self.engine.proc.stdin.write("quit\n")
                    self.engine.proc.stdin.flush()
                    self.engine.proc.wait(timeout=2)
            except Exception:
                pass
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
            print(f"[BOT] ❌ Đã có bot khác chạy tài khoản {USER}")
            sys.exit(1)
        f.write(str(os.getpid())); f.flush()
        atexit.register(lambda: (fcntl.flock(f, fcntl.LOCK_UN), f.close()))
        return f
    except ImportError:
        return None


if __name__ == "__main__":
    _lock = acquire_single_instance_lock()
    bot = JieqiCupBot()

    def signal_handler(sig, frame):
        bot.cleanup(); sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    try:
        bot.run()
    finally:
        bot.cleanup()
