# Backtesting an LB LP position

Estimate how an LB position would have performed over historical price action,
**before** committing capital — fee yield, impermanent loss, in-range time,
rebalances, net PnL. Reuses the **live `StrategyEngine`** over history, so the
"strategy" column reflects what the bot would actually have done.

CLI: `moe-backtest` (package `src/moe_mantle_bot/backtest/`).

## What it does

- Pulls historical **Bybit MNTUSDT** candles (paginated, cached to
  `data/candles/`) for 5m / 1h / 4h — the same feeds the live analyzers use.
- Emulates a Liquidity Book position bin-by-bin: deterministic inventory and IL
  as price crosses bins (the limit-order conversion is exact).
- Accrues swap fees with a **volume-capture** model (see Assumptions).
- Runs two positions side by side:
  - **static** — enter once, never re-center (buy-and-hold-LP baseline)
  - **strategy** — replays `StrategyEngine` (Keltner + ATR + MTF) and
    exits/re-enters/resizes exactly as the live bot would.
- Maps all USD stablecoins (USDT, **USDT0**, USD0, USDC) 1:1 to the MNTUSDT feed.

## Quick start

```bash
# Backtest the CURRENT live position (seeds geometry, fees, depth, size from chain)
moe-backtest --seed-from-pool 0x<LBpair> --days 90

# Calibrate the fee magnitude to real on-chain volume (see below)
moe-backtest --seed-from-pool 0x<LBpair> --days 90 --pool-daily-volume-usd 500

# A hypothetical position (no chain), JSON out, save
moe-backtest --capital 200 --quote-usd 100 --bin-count 20 --bin-step 100 \
  --strategy auto --days 60 --json --save data/backtests/test.json
```

`--seed-from-pool` runs `moe --pool <addr> snapshot --with-lp-inventory --json`
to fill: `bin_step`, token decimals, `base_factor`, `protocol_share`,
`pool_tvl_usd`, the current position's `capital`/`quote` split, and the average
competing liquidity per bin. Never invent these — seed them.

## Reading the output

```
  static     fees=$202  IL=$14.87  net=$180 (145%)  feeAPR=784%  netAPR=699%  inRange=60.1%  rebal=0  maxDD=8%
  strategy   fees=$215  IL=$0.71   net=$187 (150%)  feeAPR=834%  netAPR=725%  inRange=68.3%  rebal=1  maxDD=8%
```

- **fees** — estimated swap fees earned (assumption-driven; see below).
- **IL** — HODL value minus LP value at the end (impermanent loss; **exact**).
- **net / netAPR** — fees − IL − gas − rebalance costs, vs initial capital.
- **inRange %** — fraction of time the active bin was inside the position.
- **rebal** — strategy re-centers (each costs simulated swap + gas).
- A higher-inRange, lower-IL strategy column means the engine's exits earned
  their keep; if `rebal` cost exceeds IL saved, they didn't.

## Assumptions (calibrate these — they drive the headline numbers)

On-chain historical pool volume is **not available**, so fees are estimated:

| Knob | Meaning | Default |
|------|---------|---------|
| `--pool-daily-volume-usd` | total daily swap volume through the pool (primary fee magnitude knob) | `1× pool TVL/day` (optimistic) |
| `--pool-active-liq-usd` | competing USD liquidity per bin (fee-share denominator) | seeded avg per bin |
| `--capture-ratio` | fallback if no daily-volume given: `pool_vol = bybit_turnover × ratio` | 1.0 |
| `--lp-fee-bps` | override the LP-net fee rate | `base_factor×bin_step/1e8 × (1−protocol_share)` |

The fee math per in-range candle:

```
pool_volume_step = pool_daily_volume × days × (candle_turnover / Σ turnover)
fee_step         = pool_volume_step × lp_fee_rate × your_active / (your_active + pool_active_liq)
```

**The default `1× TVL/day` is a placeholder.** For a thin/inactive pool the real
volume is far lower — always re-run with `--pool-daily-volume-usd` set to the
pool's observed daily volume before trusting the APR. Bybit turnover only shapes
*when* volume happens, not how much. IL and in-range % do **not** depend on these
knobs — only the fee/APR figures do.

## Useful flags

- `--days N` — history depth (needs > ~18d warmup for MTF; default 90).
- `--strategy auto|narrow|wide` — force a re-entry width or let the engine pick.
- `--no-rsi-gate` — disable the re-entry RSI/regime gate (always rebalance to
  50/50). By default the backtest mirrors the live `reentry_policy`: when exiting
  **down while oversold** (or in a **bear/ranging** regime) it keeps MNT instead
  of selling to 50/50 — avoiding selling the local low. This typically helps in
  V-shaped dips (the kept MNT catches the bounce) and is why the live bot does
  it; `--no-rsi-gate` shows the worse "always sell to 50/50" baseline.
- `--decision-period-min` — how often the engine is consulted (default 60).
- `--interval` — base marking/fee candle (default 5m).
- `--refresh` — ignore the candle cache and refetch.
- `--json` / `--save PATH` — machine-readable output.
- `--chart PATH` / `--no-chart` — write (or skip) a PNG with two panels: price +
  static/strategy LP-range bands (re-centers marked), and the equity curves vs
  initial budget. A chart is auto-written next to `--save` (or under
  `data/backtests/`) unless `--no-chart`. Requires `matplotlib`.

The text report ends with the **initial budget** and the **final budget** for
both static and strategy (`initial + net PnL`).

## Tuning findings (WMNT/USDT0 binStep-100, ~4mo walk-forward)

Run `scripts/backtest_walkforward.py`, `scripts/backtest_hyperopt.py`,
`scripts/backtest_compare.py` to reproduce.

- **Position width dominates; re-centering is ~inert.** A width sweep (static,
  no re-center) is an inverted-U peaking at **~20 bins (±10%)**: 10 bins +2.97%,
  **20 bins +3.92%**, 40 bins +1.90% mean net across 6 rolling windows. Too
  narrow → frequent out-of-range (misses fees); too wide → diluted fee share.
- **Hyperopt** (random search, scored on the walk-forward) found the top configs
  all share only `initial width = 20` — every re-center param (tolerance, RSI
  gate, stabilization, cadence, mode) had zero effect, because a 20-bin position
  never went OOR. Re-centering at extremes is net-negative; passive ties static.
- **Actionable**: tune `BIN_COUNT` to ~±10% in price terms. That is **20 bins on
  binStep-100**, but ~**133 bins on binStep-15** — width-in-bins is pool-specific
  (price-% ≈ bins × bin_step/100). No code change; `BIN_COUNT` is configurable.

## Caveats

- Fees are an estimate, not a guarantee — calibrate volume.
- No MOE-emission rewards (this build is fee-only, matching the live bot).
- Bybit perp price is a proxy for the on-chain pool price (good for MNT; the
  stablecoin peg is assumed exactly $1).
- Network: candle fetch needs outbound HTTPS to `api.bybit.com`.
