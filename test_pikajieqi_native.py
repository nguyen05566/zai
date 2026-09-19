import os
#!/usr/bin/env python3
"""test_pikajieqi_native.py — Test PikaJieQi Linux native engine.

Mục tiêu: tìm ra convention đúng giữa cup_bot và PikaJieQi.

Test:
  1. FEN echo — engine có hiểu mystery FEN (X/x + BAG)?
  2. Side-to-move — khi "w", engine trả RED hay BLACK move?
  3. Move application — engine có apply moves không?
  4. Bestmove validity — nước đi có hợp lệ không?
"""
import subprocess, time, threading, re, os

PIKAJIEQI = "/home/z/my-project/bin/pikajieqi-native"
NNUE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zai_cup_boost_v1.nnue")

BOARD = "xxxxkxxxx/9/1x5x1/x1x1x1x1x/9/9/X1X1X1X1X/1X5X1/9/XXXXKXXXX"
BAG = "A2B2N2R2C2P5a2b2n2r2c2p5"

# Standard cup_bot convention:
# - lowercase (x,k) at top (row 6-9) = BLACK
# - uppercase (X,K) at bottom (row 0-3) = RED
# - "w" = RED to move
# - RED moves at row 3→4 (pawns advance upward)
# - BLACK moves at row 6→5 (pawns advance downward)


class Engine:
    def __init__(self):
        self.proc = subprocess.Popen(
            [PIKAJIEQI],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, cwd=os.path.dirname(PIKAJIEQI),
        )
        self.lines = []
        self.lock = threading.Lock()
        
        def reader():
            while True:
                line = self.proc.stdout.readline()
                if not line: break
                with self.lock:
                    self.lines.append(line.strip())
        
        threading.Thread(target=reader, daemon=True).start()
        
        self._send("uci")
        self._wait_for("uciok", timeout=10)
        self._send(f"setoption name EvalFile value {NNUE}")
        self._send("isready")
        self._wait_for("readyok", timeout=10)
    
    def _send(self, cmd):
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()
    
    def _wait_for(self, prefix, timeout=10):
        t0 = time.time()
        while time.time() - t0 < timeout:
            with self.lock:
                for l in self.lines:
                    if l.startswith(prefix) or l == prefix:
                        return True
            time.sleep(0.05)
        return False
    
    def position(self, fen, moves=None):
        cmd = f"position fen {fen}"
        if moves:
            cmd += " moves " + " ".join(moves)
        self._send(cmd)
        time.sleep(0.1)  # let engine process
    
    def go(self, movetime_ms=3000):
        with self.lock:
            self.lines.clear()
        self._send("go infinite")
        time.sleep(movetime_ms / 1000.0)
        self._send("stop")
        
        t0 = time.time()
        while time.time() - t0 < 5:
            with self.lock:
                for l in self.lines:
                    if l.startswith("bestmove"):
                        return l
            time.sleep(0.02)
        return None
    
    def display(self):
        self._send("d")
        time.sleep(0.2)
        with self.lock:
            return [l for l in self.lines if l.startswith("|") or l.startswith("Fen:")]
    
    def get_fen(self):
        """Get the FEN the engine thinks it's at."""
        with self.lock:
            for l in reversed(self.lines):
                if l.startswith("Fen:"):
                    return l[5:]
        return None
    
    def quit(self):
        self._send("quit")
        self.proc.wait(timeout=2)


def test_fen_echo():
    """Test 1: Does PikaJieQi echo the mystery FEN correctly?"""
    print("\n=== Test 1: FEN echo ===")
    eng = Engine()
    
    fen = f"{BOARD} {BAG} w - - 0 1"
    eng.position(fen)
    eng._send("d")
    time.sleep(0.3)
    echoed = eng.get_fen()
    
    print(f"  Sent FEN:    {fen}")
    print(f"  Echoed FEN:  {echoed}")
    
    if BOARD in (echoed or ""):
        print("  ✅ Engine accepted mystery FEN (board part matches)")
    else:
        print("  ❌ Engine rejected or modified the board")
    
    eng.quit()
    return BOARD in (echoed or "")


def test_side_to_move():
    """Test 2: When side='w', does engine return RED or BLACK move?"""
    print("\n=== Test 2: Side-to-move (w = RED to move in cup_bot convention) ===")
    eng = Engine()
    
    fen = f"{BOARD} {BAG} w - - 0 1"
    eng.position(fen)
    bm = eng.go(3000)
    
    if not bm:
        print("  ❌ No bestmove returned")
        eng.quit()
        return
    
    move = bm.split()[1] if len(bm.split()) > 1 else "?"
    src_rank = int(move[1]) if len(move) >= 2 else -1
    
    print(f"  FEN side: w (RED to move in cup_bot)")
    print(f"  Bestmove: {move}")
    print(f"  Source rank: {src_rank}")
    
    # In cup_bot convention:
    # RED pieces at rows 0-3 (uppercase X at bottom)
    # BLACK pieces at rows 6-9 (lowercase x at top)
    # If engine returns move from row 3 → RED move ✓
    # If engine returns move from row 6 → BLACK move ✗
    
    if src_rank <= 4:
        print(f"  → Move from row {src_rank} (bottom half) = RED move ✅ CORRECT")
    else:
        print(f"  → Move from row {src_rank} (top half) = BLACK move ❌ WRONG (expected RED)")
    
    eng.quit()
    return src_rank <= 4


def test_move_application():
    """Test 3: Does PikaJieQi apply moves from the moves list?"""
    print("\n=== Test 3: Move application ===")
    eng = Engine()
    
    fen = f"{BOARD} {BAG} w - - 0 1"
    
    # Test A: no moves
    eng.position(fen)
    eng._send("d")
    time.sleep(0.3)
    fen_a = eng.get_fen()
    print(f"  A) No moves — FEN side: {fen_a.split()[4] if fen_a else '?'}")
    
    # Test B: 1 move c3c4 (RED pawn advance, no suffix)
    eng.position(fen, ["c3c4"])
    eng._send("d")
    time.sleep(0.3)
    fen_b = eng.get_fen()
    side_b = fen_b.split()[4] if fen_b else "?"
    print(f"  B) After c3c4 — FEN side: {side_b}")
    if side_b == "b":
        print("     ✅ Side changed to 'b' — move was applied!")
    else:
        print("     ❌ Side didn't change — move NOT applied")
    
    # Test C: 1 move with reveal suffix c3c4R
    eng.position(fen, ["c3c4R"])
    eng._send("d")
    time.sleep(0.3)
    fen_c = eng.get_fen()
    side_c = fen_c.split()[4] if fen_c else "?"
    print(f"  C) After c3c4R — FEN side: {side_c}")
    if side_c == "b":
        print("     ✅ Move with suffix was applied!")
    else:
        print("     ❌ Move with suffix NOT applied (engine doesn't understand suffix)")
    
    eng.quit()


def test_bestmove_sequence():
    """Test 4: Play a sequence of moves and check bestmove validity."""
    print("\n=== Test 4: Move sequence ===")
    eng = Engine()
    
    fen = f"{BOARD} {BAG} w - - 0 1"
    
    # Sequence: RED c3c4, BLACK c6c5, RED e3e4, BLACK e6e5
    moves = ["c3c4", "c6c5", "e3e4", "e6e5"]
    
    for i, m in enumerate(moves):
        eng.position(fen, moves[:i+1])
        bm = eng.go(2000)
        
        if not bm:
            print(f"  Move {i+1} ({m}): ❌ No bestmove")
            continue
        
        bestmove = bm.split()[1] if len(bm.split()) > 1 else "?"
        src_rank = int(bestmove[1]) if len(bestmove) >= 2 else -1
        
        # After even number of moves (0,2,4), it's RED's turn (bottom, rows 0-3)
        # After odd number of moves (1,3), it's BLACK's turn (top, rows 6-9)
        expected_side = "RED" if (i + 1) % 2 == 0 else "BLACK"
        actual_side = "RED(bottom)" if src_rank <= 4 else "BLACK(top)"
        
        ok = "✅" if (expected_side == "RED" and src_rank <= 4) or \
                     (expected_side == "BLACK" and src_rank >= 5) else "❌"
        
        print(f"  After {i+1} moves: bestmove={bestmove} src_rank={src_rank} "
              f"expected={expected_side} {ok}")
    
    eng.quit()


def test_flipped_fen():
    """Test 5: Try case-swapped FEN to see if PikaJieQi's convention is opposite."""
    print("\n=== Test 5: Case-swapped FEN (no row flip) ===")
    eng = Engine()
    
    # Swap case: X↔x, K↔k
    # Original: xxxxkxxxx/9/1x5x1/x1x1x1x1x/9/9/X1X1X1X1X/1X5X1/9/XXXXKXXXX
    # Swapped:  XXXXKXXXX/9/1X5X1/X1X1X1X1X/9/9/x1x1x1x1x/1x5x1/9/xxxxkxxxx
    # Also swap BAG case
    swapped_board = BOARD.swapcase()
    swapped_bag = BAG.swapcase()
    
    fen = f"{swapped_board} {swapped_bag} w - - 0 1"
    eng.position(fen)
    bm = eng.go(3000)
    
    if not bm:
        print("  ❌ No bestmove")
        eng.quit()
        return
    
    move = bm.split()[1] if len(bm.split()) > 1 else "?"
    src_rank = int(move[1]) if len(move) >= 2 else -1
    
    print(f"  Swapped FEN: {swapped_board}")
    print(f"  Swapped BAG: {swapped_bag}")
    print(f"  Bestmove: {move} (src_rank={src_rank})")
    
    # In swapped FEN: lowercase (x,k) at BOTTOM = RED in PikaJieQi's view
    # Row 3 has x (lowercase = RED dark pawns in PikaJieQi)
    # If engine returns row 3 move → RED move ✓
    if src_rank <= 4:
        print(f"  → Move from row {src_rank} (bottom, lowercase=RED in PikaJieQi) ✅")
    else:
        print(f"  → Move from row {src_rank} (top, uppercase=BLACK in PikaJieQi)")
    
    eng.quit()
    return src_rank


if __name__ == "__main__":
    print("PikaJieQi Native Engine Test")
    print("=" * 60)
    print(f"Binary: {PIKAJIEQI}")
    print(f"NNUE:   {NNUE}")
    
    test_fen_echo()
    test_side_to_move()
    test_move_application()
    test_bestmove_sequence()
    test_flipped_fen()
    
    print("\n" + "=" * 60)
    print("Test complete")
