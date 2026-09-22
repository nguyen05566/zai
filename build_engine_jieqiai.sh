#!/usr/bin/env bash
# Build engine JieqiAI (ElephantEye 揭棋 fork) cho bot cờ úp.
#
# Nguồn: https://github.com/arthuryangcs/JieqiAI (GPLv3)
# Pin commit: 1a22a21 (main, 2021) — xem jieqiai.ref
#
# Patch (jieqiai_patches.diff, GPLv3) — sửa 5 bug nghiêm trọng của bản gốc:
#   1. FromFen: vòng đọc bag cuối chạy qua hết chuỗi (global-buffer-overflow,
#      segfault ngay khi khởi động với -O2)
#   2. pipe.cpp: memcpy chồng lấn khi dời buffer lệnh (UB) — làm hỏng lệnh
#      position/go khi bot gửi nhiều lệnh liên tiếp => engine parse sai bàn
#   3. FromFen: bag gán piece-code TRÙNG mã quân đã lật trên bàn — khi search
#      "lật thử" quân úp, nó chiếm slot rồi undo zero slot => quân thật
#      (vd pháo đang chiếu) BIẾN MẤT khỏi ucsqPieces => engine đi nước
#      tự sát không thấy chiếu
#   4. search.cpp: khối debug MakeMove ở root làm bẩn trạng thái unknown +
#      crash OOB PIECE_TYPE(-1)
#   5. MovePiece: FPE (chia %0) và assert hủy engine khi bộ đếm unknown
#      drift — chuyển thành từ chối nước an toàn
#
# Sau patch: ASan sạch, tự đấu 25+ ply 0 nước sai luật (trọng tài jieqi_rules),
# duel vs pikafish-mistboard 0 nước sai luật.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
REF="$(head -1 "$ROOT/jieqiai.ref" | tr -d '[:space:]')"
WORK="${TMPDIR:-/tmp}/jieqiai-build"
rm -rf "$WORK"
git clone --quiet https://github.com/arthuryangcs/JieqiAI.git "$WORK"
git -C "$WORK" checkout --quiet "$REF"
echo "Building JieqiAI at $(git -C "$WORK" rev-parse HEAD)"
patch -d "$WORK" -p1 --quiet < "$ROOT/jieqiai_patches.diff"
echo "Patched (see jieqiai_patches.diff for details)"
# -O2 gây segfault startup của BẢN GỐC (bug 1) — đã patch, giờ -O2 chạy ổn.
# Không dùng cmake (tránh phụ thuộc): engine là 1 thư mục source phẳng.
g++ -O2 -std=c++14 -o "$ROOT/jieqiai" "$WORK"/engine/*.cpp -lpthread
chmod +x "$ROOT/jieqiai"
echo "Built $ROOT/jieqiai (ref $REF)"
