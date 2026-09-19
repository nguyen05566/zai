# zai — Cờ Úp Bot (Pikafish jieqi_old)

Bot cờ úp (mystery_xiangqi) cho gamevh.net, dùng engine native được build trực tiếp từ **official-pikafish/Pikafish**.

## Engine source

- Repository: <https://github.com/official-pikafish/Pikafish>
- Branch: [`jieqi_old`](https://github.com/official-pikafish/Pikafish/tree/jieqi_old)
- Pinned commit: `23b9466c981f0f3a1133f92de1a6f86406c4eccc`
- Reason for `jieqi_old`: the existing mystery-xiangqi bot protocol and king-guard patch are compatible with this official branch. The newer `jieqi` branch does not accept the patch without a separate port.
- NNUE: trained `zai_cup_boost_v1.nnue` (HalfKAv2_hm, jieqi_old-compatible). `build_engine.sh` enables `USE_NNUEEVAL` and installs it as `pikafish.nnue`.

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

## Trained Cờ Úp NNUE (`zai_cup_boost_v1.nnue`)

Repo ships a custom evaluation net for mystery xiangqi:

| File | Role |
|------|------|
| `zai_cup_boost_v1.nnue` | **Primary** trained net (HalfKAv2_hm 1536×2-15-32, hash `0x6E24D6CA`) |
| `zai_jieqi_master.nnue` | Alias of the same net (backward compatible) |
| `pikafish.nnue` | Created by `build_engine.sh` as a copy of the trained net |

### Training summary (`zai_cup_boost_v1`)
- Self-play positions labeled by classical PikaJieQi (`jieqi_old`)
- 160 positions · depth 3 · 10 epochs (CPU trainer)
- Loss 0.680 → **0.604**, MAE 325 → **224 cp**
- Engine load verified: `info string NNUE evaluation using zai_cup_boost_v1.nnue enabled`

### Auto-load order
`cup_bot_jieqi.py` and `n17.py` try, in order:
1. `zai_cup_boost_v1.nnue`
2. `zai_jieqi_master.nnue`
3. `pikafish.nnue`

### Engine note
`jieqi_old` defaults to `USE_NNUEEVAL 0`. `build_engine.sh` flips it to `1` so the trained net is actually used in search.
