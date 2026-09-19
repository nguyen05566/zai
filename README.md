# zai — Cờ Úp Bot (ForgeQi)

Bot cờ úp (mystery_xiangqi) cho gamevh.net, dùng engine **[ForgeQi](https://github.com/nguyen05566/forgeqi)** — engine cờ úp độc lập viết từ đầu cho cờ úp (không phải fork Pikafish/Stockfish).

## Engine

- Repository: <https://github.com/nguyen05566/forgeqi>
- Classical eval tích hợp sẵn với **expected material** cho quân úp — **không cần NNUE**
- Hiểu quân úp (`X`/`x`), túi quân (BAG), reveal suffix (`c3c4R`), search phân nhánh theo reveal
- `do_move` tự kiểm tra hợp lệ (không để lại vua bị chiếu, không đối mặt hai vua)
- Hỗ trợ `banmoves` (chống lặp nước bị server reject) và `go infinite`/`stop` chuẩn UCI

Các engine cũ (Pikafish `jieqi_old`, patch KING-GUARD, binary `pikajieqi-native`, NNUE `zai_*.nnue`) đã được loại bỏ hoàn toàn.

## Features

- Native Linux engine (build ~4s, ~70KB, không Wine, không tải NNUE).
- Deadline-aware search với tự restart engine giữa ván.
- Recovery khi server reject nước: `banmoves` + ứng viên thay thế.
- Ponder tắt mặc định (ForgeQi trả PV 1 nước).

## Local run

```bash
bash build_engine.sh          # clone forgeqi + make + smoke test → ./forgeqi
pip install websocket-client requests
python3 cup_bot_jieqi.py
```

## GitHub Actions

`.github/workflows/cup_bot.yml` chạy mỗi 6 giờ (tài khoản chính), `.github/workflows/n17.yml` chạy tài khoản thứ hai. Cấu hình `CARO_USER19`, `CARO_PASSWD19`, `CARO_USER17`, `CARO_PASSWD17` dưới **Settings → Secrets and variables → Actions**.

Cả hai workflow đều dùng `concurrency` + `cancel-in-progress: false` — phiên mới chờ phiên trước kết thúc.

## Protocol notes

- Bot gửi `position fen <placement> <side> <BAG> 0 1` (dựng từ dữ liệu server — chính xác tuyệt đối, miễn nhiễm lỗi parser moves).
- Search qua `go infinite` + `stop` (không dùng `go movetime`).
- Sau reject: `banmoves <các nước bị reject>` trước lần `go` kế tiếp.

## License

ForgeQi và các file bot thuộc dự án này giữ nguyên license riêng của repo tương ứng.
