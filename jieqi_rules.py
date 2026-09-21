#!/usr/bin/env python3
"""Gọn engine luật cờ úp (jieqi) cho bot — sinh danh sách nước đi hợp lệ.

Mục đích: truyền `searchmoves` cho PikaJieQi để engine chỉ trả nước hợp lệ,
hết bị server reject. CHỈ DÙNG THÔNG TIN CÔNG KHAI:
  - quân đã lật: đi theo loại thật (suffix reveal từ MOVE)
  - quân úp: đi theo loại CỦA Ô XUẤT PHÁT (luật cờ úp: ô pháo đi như pháo...)
  - không dùng identitas quân ẩn (raw_face) — legality không phụ thuộc nó.

Hệ tọa độ ENGINE của PikaJieQi (trùng bot):
  file a..i = cột 0..8, rank 0..9; ĐỎ rank 0-3, ĐỎ đi rank tăng (0 -> 9).
  index ô = rank * 9 + file.

API:
  rt = JieqiRulesTracker()
  rt.set_initial([(sq, color, revealed), ...])  -> True/False (False = bố cục lạ)
  rt.apply_uci('c3c4', 'N')                     -> True/False (False = mất đồng bộ)
  rt.legal_moves('r'|'b')                       -> ['c3c4', ...]
"""
from __future__ import annotations

FILES = "abcdefghi"
RED, BLACK = "r", "b"

# Vai trò ô chuẩn (luật cờ úp): rank 0 = hàng sau cùng của ĐỎ
_BORN = {}
_back = "rnbakabnr"
for _f, _t in enumerate(_back):
    _BORN[(0, _f)] = _t
    _BORN[(9, _f)] = _t
for _f in (1, 7):
    _BORN[(2, _f)] = "c"
    _BORN[(7, _f)] = "c"
for _f in (0, 2, 4, 6, 8):
    _BORN[(3, _f)] = "p"
    _BORN[(6, _f)] = "p"

_PIECE_NAMES = {"k": "king", "a": "advisor", "b": "elephant", "n": "horse",
                "r": "rook", "c": "cannon", "p": "pawn"}

# Sĩ đã lật: đi chéo tự do (= luật engine pikafish-jieqi, đã probe xác minh).
# Muốn theo luật chuẩn nghiêm ngặt (sĩ không bao giờ rời cung) -> đổi thành True.
STRICT_ADVISOR = False


def _sq(file: int, rank: int) -> int:
    return rank * 9 + file


def _file_rank(sq: int) -> tuple[int, int]:
    return sq % 9, sq // 9


def _uci(from_sq: int, to_sq: int) -> str:
    ff, fr = _file_rank(from_sq)
    tf, tr = _file_rank(to_sq)
    return f"{FILES[ff]}{fr}{FILES[tf]}{tr}"


def _parse_uci(uci: str) -> tuple[int, int]:
    return (_sq(ord(uci[0]) - 97, int(uci[1])), _sq(ord(uci[2]) - 97, int(uci[3])))


def _in_palace(color: str, file: int, rank: int) -> bool:
    if not 3 <= file <= 5:
        return False
    return 0 <= rank <= 2 if color == RED else 7 <= rank <= 9


def _own_side(color: str, rank: int) -> bool:
    return rank <= 4 if color == RED else rank >= 5


class JieqiRulesTracker:
    """Theo dõi thế cờ từ thông tin công khai; sinh nước hợp lệ."""

    def __init__(self):
        self.board: list[tuple[str, str | None] | None] = [None] * 90
        # mỗi ô: (color, loại nếu đã lật / None nếu úp)
        self.side_to_move = RED
        self.ok = True

    # ---------- khởi tạo ----------
    def set_initial(self, pieces) -> bool:
        """pieces: [(sq, color, revealed, type_char_or_None)].
        type chỉ dùng cho quân ĐÃ MỞ (thông tin công khai); quân úp bỏ qua.
        Trả False nếu bố cục không chuẩn."""
        self.board = [None] * 90
        self.side_to_move = RED
        kings = {RED: 0, BLACK: 0}
        for item in pieces:
            sq, color, revealed = item[0], item[1], item[2]
            ptype = item[3] if len(item) > 3 else None
            if not isinstance(sq, int) or not 0 <= sq < 90:
                return False
            if self.board[sq] is not None:
                return False
            f, r = _file_rank(sq)
            if not revealed and not _own_side(color, r):
                return False  # quân úp nằm sai nửa bàn -> bố cục lạ
            if revealed:
                t = ptype or _BORN.get((r, f))
                if not t:
                    return False
                self.board[sq] = (color, t)
                if t == "k":
                    kings[color] += 1
            else:
                if (r, f) not in _BORN:
                    return False  # ô không có vai trò chuẩn -> không suy được luật
                self.board[sq] = (color, None)
        if kings[RED] != 1 or kings[BLACK] != 1:
            self.ok = False
            return False
        return True

    # ---------- áp nước ----------
    def apply_uci(self, uci: str, revealed_char: str | None) -> bool:
        if not self.ok:
            return False
        try:
            src, dst = _parse_uci(uci[:4])
        except Exception:
            self.ok = False
            return False
        piece = self.board[src]
        if piece is None:
            self.ok = False
            return False
        color, ptype = piece
        if color != self.side_to_move:
            self.ok = False
            return False
        if src == dst:
            # lật tại chỗ: phải có suffix revealing
            if revealed_char:
                self.board[src] = (color, revealed_char.lower())
            else:
                self.ok = False
                return False
        else:
            self.board[dst] = piece
            self.board[src] = None
            if ptype is None:
                # quân úp vừa đi -> lật; không biết loại thì mất đồng bộ
                if revealed_char:
                    self.board[dst] = (color, revealed_char.lower())
                else:
                    self.ok = False
                    return False
        self.side_to_move = BLACK if color == RED else RED
        return True

    # ---------- sinh nước ----------
    def _role(self, sq: int) -> str | None:
        piece = self.board[sq]
        if piece is None:
            return None
        color, ptype = piece
        if ptype is not None:
            return ptype
        f, r = _file_rank(sq)
        return _BORN.get((r, f))  # quân úp: vai trò ô xuất phát (chưa đi bao giờ)

    def _pseudo(self, sq: int) -> list[int]:
        piece = self.board[sq]
        color, ptype = piece
        f, r = _file_rank(sq)
        role = self._role(sq)
        out: list[int] = []
        own = lambda s: self.board[s] is not None and self.board[s][0] == color

        if role == "k":
            for df, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nf, nr = f + df, r + dr
                if 0 <= nf < 9 and 0 <= nr < 10 and _in_palace(color, nf, nr):
                    s = _sq(nf, nr)
                    if not own(s):
                        out.append(s)
        elif role == "a":
            # Probe tren engine that: quan UP o o-si bi gioi han trong cung,
            # nhung si DA LAT di cheo tu do (dung hanh vi pikafish-jieqi).
            # Dao chieu strict: dat STRICT_ADVISOR = True.
            confine = _in_palace if (ptype is None or STRICT_ADVISOR) else (lambda c, f2, r2: True)
            for df, dr in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                nf, nr = f + df, r + dr
                if 0 <= nf < 9 and 0 <= nr < 10 and confine(color, nf, nr):
                    s = _sq(nf, nr)
                    if not own(s):
                        out.append(s)
        elif role == "b":
            for df, dr in ((2, 2), (2, -2), (-2, 2), (-2, -2)):
                nf, nr = f + df, r + dr
                if 0 <= nf < 9 and 0 <= nr < 10 and _own_side(color, nr) \
                        and self.board[_sq(f + df // 2, r + dr // 2)] is None:
                    s = _sq(nf, nr)
                    if not own(s):
                        out.append(s)
        elif role == "n":
            for df, dr, lf, lr in ((1, 2, 0, 1), (-1, 2, 0, 1), (1, -2, 0, -1), (-1, -2, 0, -1),
                                   (2, 1, 1, 0), (2, -1, 1, 0), (-2, 1, -1, 0), (-2, -1, -1, 0)):
                nf, nr = f + df, r + dr
                if 0 <= nf < 9 and 0 <= nr < 10 and self.board[_sq(f + lf, r + lr)] is None:
                    s = _sq(nf, nr)
                    if not own(s):
                        out.append(s)
        elif role == "r":
            for df, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nf, nr = f + df, r + dr
                while 0 <= nf < 9 and 0 <= nr < 10:
                    s = _sq(nf, nr)
                    if self.board[s] is None:
                        out.append(s)
                    else:
                        if not own(s):
                            out.append(s)
                        break
                    nf, nr = nf + df, nr + dr
        elif role == "c":
            for df, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nf, nr = f + df, r + dr
                screen = False
                while 0 <= nf < 9 and 0 <= nr < 10:
                    s = _sq(nf, nr)
                    if not screen:
                        if self.board[s] is None:
                            out.append(s)
                        else:
                            screen = True
                    else:
                        if self.board[s] is not None:
                            if not own(s):
                                out.append(s)
                            break
                    nf, nr = nf + df, nr + dr
        elif role == "p":
            fwd = 1 if color == RED else -1
            nf, nr = f, r + fwd
            if 0 <= nr < 10:
                s = _sq(nf, nr)
                if not own(s):
                    out.append(s)
            if not _own_side(color, r):  # đã qua sông: đi ngang
                for nf in (f - 1, f + 1):
                    if 0 <= nf < 9:
                        s = _sq(nf, r)
                        if not own(s):
                            out.append(s)
        return out

    def _find_king(self, color: str) -> int | None:
        for sq in range(90):
            p = self.board[sq]
            if p and p[0] == color and self._role(sq) == "k":
                return sq
        return None

    def _attacked(self, sq: int, by: str) -> bool:
        """Ô sq có bị bên `by` tấn công không (kể cả tướng đối diện)."""
        f, r = _file_rank(sq)
        # trượt 4 hướng: xe / pháo / tướng đối diện
        for df, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nf, nr = f + df, r + dr
            screen = False
            while 0 <= nf < 9 and 0 <= nr < 10:
                s = _sq(nf, nr)
                p = self.board[s]
                if p is not None:
                    role = self._role(s)
                    if not screen:
                        if p[0] == by and role == "r":
                            return True
                        if p[0] == by and role == "k":
                            # tướng đối diện thẳng hàng (không có quân chắn)
                            kf, kr = _file_rank(s)
                            if kf == f:  # cùng file với sq
                                return True
                        screen = True
                    else:
                        if p[0] == by and role == "c":
                            return True
                        break
                nf, nr = nf + df, nr + dr
        # mã
        for df, dr, lf, lr in ((1, 2, 0, 1), (-1, 2, 0, 1), (1, -2, 0, -1), (-1, -2, 0, -1),
                               (2, 1, 1, 0), (2, -1, 1, 0), (-2, 1, -1, 0), (-2, -1, -1, 0)):
            nf, nr = f + df, r + dr
            if 0 <= nf < 9 and 0 <= nr < 10:
                s = _sq(nf, nr)
                p = self.board[s]
                if p and p[0] == by and self._role(s) == "n" \
                        and self.board[_sq(nf - lf, nr - lr)] is None:
                    return True
        # tốt: tấn công theo hướng đi của bên `by`
        back = -1 if by == RED else 1   # tốt ĐỎ tấn công ô có rank cao hơn nó
        nr = r + back
        if 0 <= nr < 10:
            for nf in (f, f - 1, f + 1):
                if 0 <= nf < 9:
                    s = _sq(nf, nr)
                    p = self.board[s]
                    if p and p[0] == by and self._role(s) == "p":
                        if nf == f:
                            return True
                        # ăn ngang chỉ khi tốt đã qua sông (so với ô bị tấn công)
                        if not _own_side(by, nr - back):
                            return True
        return False

    def legal_moves(self, side: str) -> list[str]:
        if not self.ok or side != self.side_to_move:
            return []
        king = self._find_king(side)
        if king is None:
            return []
        result = []
        for src in range(90):
            p = self.board[src]
            if p is None or p[0] != side:
                continue
            for dst in self._pseudo(src):
                if dst == king:
                    continue
                captured = self.board[dst]
                self.board[dst] = self.board[src]
                self.board[src] = None
                bad = self._attacked(self._find_king(side) or dst, 
                                     BLACK if side == RED else RED)
                self.board[src] = self.board[dst]
                self.board[dst] = captured
                if not bad:
                    result.append(_uci(src, dst))
        return result


def build_initial_standard() -> JieqiRulesTracker:
    """Ván chuẩn: 2 tướng mở, còn lại úp đúng ô."""
    rt = JieqiRulesTracker()
    pieces = [(_sq(4, 0), RED, True), (_sq(4, 9), BLACK, True)]
    for (r, f), _t in _BORN.items():
        if f == 4 and r in (0, 9):
            continue
        color = RED if r <= 4 else BLACK
        pieces.append((_sq(f, r), color, False))
    rt.set_initial(pieces)
    return rt


if __name__ == "__main__":
    rt = build_initial_standard()
    moves = rt.legal_moves(RED)
    print(f"Nước hợp lệ ĐỎ ở thế khai cuộc: {len(moves)}")
    print(" ".join(sorted(moves)))
