# zai — Cờ Úp Bot với ZaiQi

Bot cờ úp (`mystery_xiangqi`) cho `gamevh.net`, sử dụng **ZaiQi**, engine UCI độc lập được viết riêng trong repo này.

## Engine mới

ZaiQi không fork Pikafish, không dùng ForgeQi, không dùng NNUE và không tải source engine bên ngoài. Source nằm trong `zaiqi_engine.cpp` và được biên dịch thành binary `zaiqi`.

Engine hỗ trợ:

- FEN cờ úp với quân ẩn `X/x` và BAG;
- Nước có reveal suffix, ví dụ `c3c4R`;
- Giao thức UCI: `uci`, `isready`, `position`, `go infinite`, `stop`, `ponderhit`;
- Sinh nước hợp lệ cho tướng, sĩ, tượng, mã, xe, pháo và tốt;
- Kiểm tra chiếu tướng, không tự chiếu tướng và tướng đối mặt;
- Iterative deepening, alpha-beta, transposition table và move ordering;
- `banmoves` để tránh các nước server đã từ chối.

Do đây là engine mới, sức mạnh thực chiến chưa được xem là tương đương Pikafish. Cần theo dõi các ván thử trước khi dùng lâu dài.

## Chạy local

```bash
bash build_engine.sh
python3 -m py_compile cup_bot_jieqi.py n17.py
python3 cup_bot_jieqi.py
```

`build_engine.sh` chỉ biên dịch source nội bộ, chạy UCI smoke test và không tải NNUE.

## Cấu hình hiện tại

| Tham số | Giá trị |
|---|---:|
| Thời gian suy nghĩ | 5 giây/nước |
| Hash | 256 MB |
| Mức cược | 10.000 xu |
| Thời gian bàn | 10 phút |

## GitHub Actions

Workflow cài `g++`, chạy `build_engine.sh`, kiểm tra UCI rồi khởi động bot. Không cần binary engine hoặc file network được commit vào repo.
