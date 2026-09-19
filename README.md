# zai — Cờ Úp Bot (Pikafish jieqi_old)

Bot cờ úp (mystery_xiangqi) cho gamevh.net, dùng engine native được build trực tiếp từ **official-pikafish/Pikafish**.

## Engine source

- Repository: <https://github.com/official-pikafish/Pikafish>
- Branch: [`jieqi_old`](https://github.com/official-pikafish/Pikafish/tree/jieqi_old)
- Pinned commit: `23b9466c981f0f3a1133f92de1a6f86406c4eccc`
- Reason for `jieqi_old`: the existing mystery-xiangqi bot protocol and king-guard patch are compatible with this official branch. The newer `jieqi` branch does not accept the patch without a separate port.
- NNUE: `pikafish.nnue` from the official [Pikafish Networks](https://github.com/official-pikafish/Networks) release.

The previous build used the unofficial `brianhliou/pikafish-jieqi-wasm` fork and `jieqi_old-mistboard`; that dependency has been removed.

## Features

- Native Linux engine; no Wine required.
- Deadline-aware search with automatic engine restart.
- Retry with an alternative move when the game server rejects a move.
- King-guard patch retained for phantom-check positions in hidden-piece play.

## Local run

```bash
bash build_engine.sh
pip install websocket-client requests
python3 cup_bot_jieqi.py
```

The build script clones the official repository, checks out the pinned commit, applies `engine_king_guard.patch`, builds `PikaJieQi`, and writes `pikajieqi-native` plus `pikafish.nnue` at the repository root.

## GitHub Actions

`.github/workflows/cup_bot.yml` runs every six hours and `.github/workflows/n17.yml` runs the second account. Configure `CARO_USER19`, `CARO_PASSWD19`, `CARO_USER17`, and `CARO_PASSWD17` under **Settings → Secrets and variables → Actions**.

The engine uses `position startpos moves ...` with reveal suffixes such as `c3c4R`. Do not send a FEN containing a BAG, and use the bot's `go infinite`/`stop` search flow rather than `go movetime`.

## License

The engine remains covered by the upstream GPLv3 license. Bot-specific files retain their original project licensing and configuration responsibilities.

## Trained Cờ Úp NNUE (`zai_jieqi_master.nnue`)
Repo đã tích hợp mạng nơ-ron đánh giá vị trí chuyên biệt cho Cờ Úp (**zai_jieqi_master.nnue**):
- **Kiến trúc mạng**: HalfKAv2_hm với 8 layer stacks (psqt + accumulator) phù hợp với engine `pikafish jieqi_old`.
- **Tối ưu hóa lối chơi**:
  - Tăng cường định giá chủ động ăn quân và mở quân úp các lộ trọng điểm (Xe lộ 3, 7, Pháo lồng).
  - Tối ưu hóa điều động Sĩ, Tượng qua sông (luật cờ úp cho phép Sĩ/Tượng sang sông tham chiến linh hoạt).
  - Khắc phục lỗi bóng ma chiếu (phantom-check) và giữ an toàn tướng khi đối phương chưa lộ quân.
- **Tự động kích hoạt**: `cup_bot_jieqi.py` và `n17.py` sẽ tự động ưu tiên nạp file `zai_jieqi_master.nnue` nếu có sẵn trong thư mục bot, nếu không sẽ dùng `pikafish.nnue` mặc định.
