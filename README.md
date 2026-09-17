# zai — Cờ Úp Bot (PikaJieQi)

Bot cờ úp (mystery_xiangqi) cho gamevh.net, dùng engine **PikaJieQi** (fork Pikafish jieqi branch).

## Tính năng

- **Engine**: PikaJieQi Linux native (build từ source, không cần wine)
- **Depth**: 19-25 trong 5s search
- **Movetime**: 5s/move (deadline-aware)
- **Bet**: 1000 xu
- **Auto-restart**: Engine tự restart nếu crash
- **Forbidden moves**: Retry với nước thay thế khi bị server reject

## Cấu trúc

```
cup_bot_jieqi.py          # Bot chính (Python)
build_engine.sh            # Script build PikaJieQi từ source
pikafish-jieqi.ref         # PikaJieQi git commit hash (pinned)
.github/workflows/cup_bot.yml  # GitHub Actions workflow
```

## Chạy local

```bash
# 1. Build engine
bash build_engine.sh

# 2. Install deps
pip install websocket-client requests

# 3. Run bot
python3 cup_bot_jieqi.py
```

## Chạy trên GitHub Actions

Workflow `.github/workflows/cup_bot.yml` chạy mỗi 6 giờ:
1. Checkout code
2. Setup Python 3.11
3. Install deps (websocket-client, requests)
4. Build PikaJieQi từ source (commit pinned trong `pikafish-jieqi.ref`)
5. Run bot (timeout 340 phút)

### Setup Secrets

Vào **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `CARO_USER19` | Tài khoản gamevh.net |
| `CARO_PASSWD19` | Mật khẩu gamevh.net |

Nếu không set secrets, bot dùng default (`nguyen15` / `nhat123456`).

## PikaJieQi Engine

- **Source**: `brianhliou/pikafish-jieqi-wasm` (branch `jieqi_old-mistboard`)
- **Commit**: `e75cee3a` (pinned trong `pikafish-jieqi.ref`)
- **Build**: `make -j ARCH=x86-64-sse41-popcnt build`
- **NNUE**: `pikafish.nnue` (included trong source repo)

PikaJieQi là fork của Pikafish với jieqi branch — hỗ trợ mystery_xiangqi FEN format (X/x cho quân úp + BAG) natively.

### Quan trọng

- **`go movetime N`** KHÔNG hoạt động (bug trong jieqi branch). Phải dùng `go infinite` + `stop`.
- **FEN với BAG** gây crash. Phải dùng `position startpos moves ...` thay vì `position fen ...`.
- PikaJieQi tự generate BAG từ board và track reveals qua move suffix (vd: `c3c4R` = reveal Rook).
