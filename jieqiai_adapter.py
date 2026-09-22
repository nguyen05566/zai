#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adapter điều khiển engine JieqiAI (ElephantEye 揭棋 fork, GPLv3).

https://github.com/arthuryangcs/JieqiAI

Giao thức UCCI (khác UCI của pikafish):
  - handshake:  "ucci" -> "ucciok"
  - position:   "position fen <board> <w|b> <bagR> <bagREaten> <bagB> <bagBEaten>"
                (luôn gửi FEN ĐẦY ĐỦ mỗi nước — không dùng "moves" vì
                 macro TRUE_PC_MOVED/UNKNOWN_CPT của engine trả nhầm side
                 thay vì piece-code => suffix reveal qua moves list bị hỏng)
  - go:         "go time <ms>"  (usemillisec mặc định true)
  - cấm nước:   "banmoves <mv1> <mv2> ..." (áp dụng cho lượt go kế tiếp)
  - dừng:       "stop" (engine trả bestmove của trận số tốt nhất đã search)

Lưu ý output: engine in RẤT NHIỀU dòng debug (bàn cờ, "aaa ...", "move:",
"count:", "position fen ...") — chỉ tin dòng "bestmove xxxx" / "nobestmove".
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time

_BESTMOVE_RE = re.compile(r'^bestmove\s+([a-i]\d[a-i]\d)', re.IGNORECASE)

# Cách build + patch: xem build_engine_jieqiai.sh (clone repo gốc, vá 2 dòng
# FromFen chống overread bag, g++ -O2). Binaries ứng viên:
JIEQIAI_BINARY_CANDIDATES = [
    os.environ.get("JIEQIAI_ENGINE", ""),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "jieqiai"),
    "./jieqiai",
]


class JieqiAIEngine:
    """Trình bao UCCI cho binary jieqiai (thread-safe qua lock)."""

    def __init__(self, debug_log_path=None):
        self.proc = None
        self.binary_path = None
        self.debug_log = open(debug_log_path, "a", buffering=1) if debug_log_path else None
        self._lines = []
        self._lines_lock = threading.Lock()
        self._latest_bestmove = None
        self._last_info = ""
        self.binary_path = self._find_binary()
        if not self.binary_path:
            raise FileNotFoundError(
                "Không tìm thấy binary jieqiai. Đã thử: "
                + str([c for c in JIEQIAI_BINARY_CANDIDATES if c]))
        self._spawn()

    # ---------- vòng đời ----------

    @staticmethod
    def _find_binary():
        for path in JIEQIAI_BINARY_CANDIDATES:
            if path and os.path.isfile(path):
                if not os.access(path, os.X_OK):
                    try:
                        os.chmod(path, 0o755)  # checkout Git mất exec-bit
                    except OSError:
                        pass
                if os.access(path, os.X_OK):
                    return path
        return None

    def _spawn(self):
        self.proc = subprocess.Popen(
            [self.binary_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
            cwd=os.path.dirname(os.path.abspath(self.binary_path)) or ".",
        )
        threading.Thread(target=self._reader, daemon=True).start()
        self._send("ucci")
        if not self._wait("ucciok", timeout=20):
            self.kill()
            raise RuntimeError("jieqiai: không nhận được ucciok")
        self._send("isready")
        self._wait("readyok", timeout=5)

    def _reader(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            if self.debug_log:
                self.debug_log.write(f"<<< {line}\n")
                self.debug_log.flush()
            with self._lines_lock:
                self._lines.append(line)
                if len(self._lines) > 400:
                    self._lines = self._lines[-200:]
            if line.startswith("bestmove") or line.startswith("nobestmove"):
                self._latest_bestmove = line
            elif line.startswith("info ") and "depth" in line:
                self._last_info = line

    def _send(self, cmd):
        with self._lines_lock:
            self._lines = []
        self._latest_bestmove = None
        if self.debug_log:
            self.debug_log.write(f">>> {cmd}\n")
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()

    def _wait(self, token, timeout=10.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                return False
            with self._lines_lock:
                if any(token in l for l in self._lines):
                    return True
            time.sleep(0.01)
        return False

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def kill(self):
        try:
            self.proc.stdin.write("quit\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=2)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass

    # ---------- đi nước ----------

    def bestmove(self, jieqiai_fen, time_ms, banned_moves=None, hard_cap_s=None):
        """Trả về nước đi 'a0i9' (4 ký tự) hoặc None.

        jieqiai_fen : FEN đầy đủ từ jieqi_rules.to_jieqai_fen()
        time_ms     : ngân sách tư duy cho nước này ("go time")
        banned_moves: danh sách nước CẤM (để ép engine vào nước hợp lệ
                      theo luật GameVH nếu engine đề xuất nước lệch luật)
        hard_cap_s  : trần cứng (giây) — vượt thì force "stop"
        """
        if not self.alive():
            return None
        cmds = [f"position fen {jieqiai_fen}"]
        if banned_moves:
            cmds.append("banmoves " + " ".join(banned_moves))
        cmds.append(f"go time {int(max(50, time_ms))}")
        for c in cmds:
            self._send(c)
        budget = max(0.15, time_ms / 1000.0)
        cap = hard_cap_s if hard_cap_s else min(30.0, budget * 4 + 2.0)
        t0 = time.time()
        while time.time() - t0 < cap:
            if self._latest_bestmove:
                m = _BESTMOVE_RE.match(self._latest_bestmove)
                return m.group(1).lower() if m else None
            if not self.alive():
                return None
            time.sleep(0.01)
        # vượt trần -> force stop, chờ bestmove tạm tốt nhất
        try:
            self.proc.stdin.write("stop\n")
            self.proc.stdin.flush()
        except Exception:
            pass
        t1 = time.time()
        while time.time() - t1 < 3.0:
            if self._latest_bestmove:
                m = _BESTMOVE_RE.match(self._latest_bestmove)
                return m.group(1).lower() if m else None
            if not self.alive():
                break
            time.sleep(0.01)
        return None

    def bestmove_legal(self, jieqiai_fen, time_ms, legal_moves, hard_cap_s=None,
                       max_retries=12):
        """bestmove + tự xác thực/bổ sung banmoves cho tới khi hợp lệ.

        legal_moves: danh sách nước hợp lệ (set/list) theo jieqi_rules.
        Trả về (move, banned_used). None chỉ khi danh sách hợp lệ rỗng.
        Engine thỉnh thoảng: (a) đề xuất nước lệch luật — ban + retry;
        (b) trả nobestmove khi tin rằng mọi nước thua — fallback về nước
        hợp lệ của trọng tài để không bao giờ bỏ lượt.
        """
        legal = list(legal_moves or [])
        legal_set = set(legal)
        if not legal_set:
            return None, []
        banned = []
        for _ in range(max_retries):
            mv = self.bestmove(jieqiai_fen, time_ms,
                               banned_moves=banned, hard_cap_s=hard_cap_s)
            if mv is None:
                break
            if mv in legal_set:
                return mv, banned
            banned.append(mv)
        # fallback: nước hợp lệ đầu tiên (không bao giờ bỏ lượt)
        import random as _random
        mv = legal[_random.randrange(len(legal))]
        return mv, banned
