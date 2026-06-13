# Configuration

All configuration via environment variables loaded from `.env` by `Settings.from_env()`.

## Network

| Variable | Default | Description |
|----------|---------|-------------|
| `MANTLE_RPC_URL` | `https://rpc.mantle.xyz` | Primary RPC endpoint. 4 fallback endpoints rotate on failure. |
| `CHAIN_ID` | `5000` | Mantle mainnet chain ID |
| `POOL_ADDRESS` | `0xf6c9...e2415` | WMNT/USDT Merchant Moe LB V2.2 pool (binStep 15) |
| `WMNT_ADDRESS` | `0x78c1...f4cb8` | Wrapped MNT (WMNT) token |
| `USDT_ADDRESS` | `0x201E...956aE` | USDT token (6 decimals) |
| `MOE_FACTORY_ADDRESS` | `0xa663...104054` | Merchant Moe LB Factory |
| `MOE_ROUTER_ADDRESS` | `0x013e...21E3a` | Merchant Moe LB Router |

## Wallet

| Variable | Default | Description |
|----------|---------|-------------|
| `WALLET_FILE` | `wallet.json` | Wallet JSON file (0o600 permissions) |
| `PRIVATE_KEY` | _(none)_ | Alternative to wallet file |
| `WALLET_ADDRESS` | _(none)_ | Override for read-only operations |

## Strategy Parameters

| Variable | Default | Description |
|----------|---------|-------------|
| `BIN_COUNT` | `10` | Bins for narrow-range positions |
| `SLIPPAGE_BPS` | `100` | Slippage tolerance in bps (100 = 1%) |
| `ID_SLIPPAGE` | `5` | Active bin movement tolerance (bins) |
| `TX_DEADLINE_SECONDS` | `1800` | Transaction deadline (30 min) |
| `TARGET_MNT_RATIO_BPS` | `5000` | Target MNT allocation (5000 = 50%). Range: 1000-9000 |
| `REENTRY_POLICY_EXIT_DOWN` | `continuation_safe` | Re-entry policy after lower-range exit: `continuation_safe`, `partial_rebalance`, `neutral_rebalance` |
| `REENTRY_POLICY_EXIT_UP` | `continuation_safe` | Re-entry policy after upper-range exit |
| `REENTRY_POLICY_NEUTRAL` | `continuation_safe` | Re-entry policy when direction unknown |
| `REENTRY_PARTIAL_EXIT_DOWN_MNT_RATIO_BPS` | `3000` | MNT target for partial rebalance after exit down (`30/70`) |
| `REENTRY_PARTIAL_EXIT_UP_MNT_RATIO_BPS` | `7000` | MNT target for partial rebalance after exit up (`70/30`) |
| `REENTRY_NEUTRAL_MNT_RATIO_BPS` | `5000` | MNT target for neutral rebalance (`50/50`) |
| `MIN_REENTRY_SWAP_USDT` | `10.0` | Skip re-entry swap plans smaller than this notional |
| `MIN_REENTRY_CONFIDENCE` | `0.8` | Minimum policy confidence required for a re-entry swap |
| `MAX_REENTRY_SWAP_PCT` | `0.35` | Max fraction of recovered wallet value allowed for pre-entry swap |
| `REENTRY_RSI_FILTER_ENABLED` | `false` | Phase 3 RSI reversal filter on top of candle-bias gate |
| `REENTRY_RSI_EXIT_DOWN_THRESHOLD` | `30.0` | Oversold RSI threshold for reversal buyback after `exit_down` |
| `REENTRY_RSI_EXIT_UP_THRESHOLD` | `70.0` | Overbought RSI threshold for reversal de-risking after `exit_up` |
| `REENTRY_TREND_FILTER_ENABLED` | `false` | Phase 3 EMA trend filter on top of candle-bias and RSI gates |
| `REENTRY_TREND_FAST_EMA` | `20` | Fast EMA span for re-entry trend filter |
| `REENTRY_TREND_SLOW_EMA` | `50` | Slow EMA span for re-entry trend filter |
| `REENTRY_TREND_FLATTENING_LOOKBACK` | `5` | Lookback for detecting fast/slow spread flattening toward reversal |
| `REENTRY_ENSEMBLE_ENABLED` | `false` | Phase 3 ensemble decision over candle bias, RSI, and trend filters |
| `REENTRY_ADAPTIVE_RATIO_ENABLED` | `false` | Phase 4.1 ratio optimizer on top of selected re-entry target |
| `REENTRY_ADAPTIVE_RATIO_LOOKBACK` | `5` | Recent resolved re-entry events for adaptive ratio scoring |
| `REENTRY_ADAPTIVE_RATIO_MIN_SAMPLES` | `3` | Min resolved re-entry events required before recent-performance scoring |
| `REENTRY_ADAPTIVE_RATIO_STEP_BPS` | `500` | Bps adjustment per adaptive score step |
| `REENTRY_ADAPTIVE_LOW_VOL_WIDTH_PCT` | `1.0` | Keltner width at/below counts as low-volatility regime |
| `REENTRY_ADAPTIVE_HIGH_VOL_WIDTH_PCT` | `2.0` | Keltner width at/above counts as high-volatility regime |
| `REENTRY_ADAPTIVE_POSITIVE_PNL_USDT` | `1.0` | Avg resolved re-entry PnL vs HODL needed to count as positive |
| `REENTRY_ADAPTIVE_NEGATIVE_PNL_USDT` | `-1.0` | Avg resolved re-entry PnL vs HODL at/below counts as weak |
| `REENTRY_ADAPTIVE_MIN_IN_RANGE_RATIO` | `0.5` | Min avg next-cycle in-range ratio required for supportive outcomes |
| `REENTRY_ADAPTIVE_LOW_FILL_PCT` | `60.0` | Avg fill % below this penalizes adaptive ratio score |
| `REENTRY_THRESHOLD_CALIBRATION_ENABLED` | `false` | Phase 4.2 threshold calibration on top of base re-entry guards |
| `REENTRY_THRESHOLD_CALIBRATION_LOOKBACK` | `5` | Recent resolved re-entry events for threshold calibration |
| `REENTRY_THRESHOLD_CALIBRATION_MIN_SAMPLES` | `3` | Min resolved re-entry events before calibration applies |
| `REENTRY_THRESHOLD_CONFIDENCE_STEP` | `0.05` | Confidence threshold adjustment per calibration step |
| `REENTRY_THRESHOLD_SWAP_PCT_STEP` | `0.05` | Swap-size guard adjustment per calibration step |
| `REENTRY_THRESHOLD_MIN_SWAP_USDT_STEP` | `2.5` | Min-swap notional adjustment per calibration step |
| `REENTRY_THRESHOLD_CONFIDENCE_FLOOR` | `0.6` | Lower clamp for calibrated confidence threshold |
| `REENTRY_THRESHOLD_CONFIDENCE_CEILING` | `0.95` | Upper clamp for calibrated confidence threshold |
| `REENTRY_THRESHOLD_MAX_SWAP_PCT_FLOOR` | `0.2` | Lower clamp for calibrated max swap percentage |
| `REENTRY_THRESHOLD_MAX_SWAP_PCT_CEILING` | `0.5` | Upper clamp for calibrated max swap percentage |
| `REENTRY_THRESHOLD_MIN_SWAP_USDT_FLOOR` | `5.0` | Lower clamp for calibrated minimum swap notional |
| `REENTRY_THRESHOLD_MIN_SWAP_USDT_CEILING` | `25.0` | Upper clamp for calibrated minimum swap notional |
| `WIDE_ENTRY_INVENTORY_GATE_ENABLED` | `true` | Block unsafe wide entry when free wallet is too MNT-skewed |
| `WIDE_ENTRY_MAX_MNT_WEIGHT_BPS` | `8500` | MNT-weight threshold for wide entry gating (`8500 = 85%`) |
| `WIDE_ENTRY_MIN_USDT` | `10.0` | Min free USDT required for wide entry without rebalance |
| `WIDE_ENTRY_REBALANCE_ENABLED` | `true` | Allow guarded pre-entry rebalance for wide entry / re-entry / top-up |
| `WIDE_ENTRY_REBALANCE_TARGET_MNT_RATIO_BPS` | `7000` | Target MNT ratio after pre-entry rebalance (`7000 = 70/30`) |
| `WIDE_ENTRY_REBALANCE_TOLERANCE_BPS` | `500` | Tolerance band around wide pre-entry target ratio |
| `WIDE_ENTRY_REBALANCE_MIN_TRADE_USDT` | `10.0` | Min trade notional before wide pre-entry rebalance executes |
| `WIDE_ENTRY_REBALANCE_MAX_SWAP_PCT` | `0.35` | Max wallet fraction allowed for wide pre-entry rebalance swap |
| `ADAPTIVE_GAS_RESERVE_ENABLED` | `true` | Expand effective native MNT gas reserve using recent add tx cost and bin count |
| `ADAPTIVE_GAS_RESERVE_LOOKBACK` | `5` | Recent add operations for adaptive gas estimate |
| `ADAPTIVE_GAS_RESERVE_MULTIPLIER` | `3.0` | Multiplier applied to recent avg add gas |
| `ADAPTIVE_GAS_RESERVE_DEFAULT_TX_MNT` | `1.0` | Fallback add-gas estimate when no recent history |
| `ADAPTIVE_GAS_RESERVE_BIN_BUFFER_MNT` | `0.03` | Extra native MNT reserve per LP bin on top of recent avg |
| `MIN_POSITION_SIZE_USDT` | `3.0` | Min LP position value in USDT. Range: 1-1000 |
| `MIN_TOP_UP_FILL_USDT` | `5.0` | Min fill value for top-up txs (lower than fresh entry) |
| `MIN_TOP_UP_FREE_VALUE_USDT` | `20.0` | Skip top-up planning if free wallet value below this |
| `WIDE_CONFIDENCE_THRESHOLD` | `0.5` | Keltner confidence needed for auto-wide selection. Range: 0.1-1.0 |
| `GAS_RESERVE_MNT` | `2.0` | Native MNT reserved for gas (Mantle L2 gas is cheap). Range: 1-10000 |
| `MAX_BUDGET_PCT` | `0.80` | Max fraction of wallet usable for LP; ~20% stays as headroom. Range: 0.1-1.0 |
| `MNT_MIN_BALANCE` | `0.0` | Min native MNT balance; below = auto-replenish to 2x via WMNT unwrap or USDT swap. 0 = use `GAS_RESERVE_MNT` as threshold |
| `NARROW_CAPITAL_PCT` | `0.5` | Capital split parameter (legacy) |
| `WIDE_CAPITAL_PCT` | `0.5` | Capital split parameter (legacy) |

Defaults preserve one-sided re-entry behavior. To enable Phase 1 inventory rebalancing, set `REENTRY_POLICY_EXIT_DOWN` and/or `REENTRY_POLICY_EXIT_UP` to `partial_rebalance` or `neutral_rebalance`.

Phase 3.1 is optional, disabled by default. When `REENTRY_RSI_FILTER_ENABLED=true`, reversal re-entry swaps only proceed when RSI confirms overextension:
- `exit_down`: `RSI <= REENTRY_RSI_EXIT_DOWN_THRESHOLD`
- `exit_up`: `RSI >= REENTRY_RSI_EXIT_UP_THRESHOLD`

Phase 3.2 is optional, disabled by default. When `REENTRY_TREND_FILTER_ENABLED=true`, reversal re-entry swaps only proceed when EMA trend is already supportive:
- `exit_down`: fast/slow EMA is bullish or bearish spread is flattening toward bullish crossover
- `exit_up`: fast/slow EMA is bearish or bullish spread is flattening toward bearish crossover

Phase 3.3 is optional, disabled by default. When `REENTRY_ENSEMBLE_ENABLED=true`, the bot resolves one explicit re-entry decision from the combined signal stack:
- `keep_one_sided`
- `swap_to_30_70`
- `swap_to_50_50`
- `skip_reentry`

Phase 4.1 is optional, disabled by default. When `REENTRY_ADAPTIVE_RATIO_ENABLED=true`, the bot adjusts the resolved re-entry target ratio using:
- Current Keltner width as volatility-regime input
- Recent resolved re-entry outcomes from `analytics.db`
- Bounded bps step so adjustment stays within conservative directional limits

Phase 4.2 is optional, disabled by default. When `REENTRY_THRESHOLD_CALIBRATION_ENABLED=true`, the bot calibrates re-entry swap guards using recent resolved outcomes:
- Supportive outcomes relax confidence and swap-size guards
- Weak outcomes tighten them
- All adjustments stay inside explicit floors and ceilings

Wide entry safety is enabled by default:
- If free wallet is too MNT-heavy or has too little USDT, wide entry is gated before LP creation
- If `WIDE_ENTRY_REBALANCE_ENABLED=true`, the bot can rebalance toward `WIDE_ENTRY_REBALANCE_TARGET_MNT_RATIO_BPS` before wide entry, re-entry, or top-up
- If rebalance exceeds `WIDE_ENTRY_REBALANCE_MAX_SWAP_PCT`, the bot holds cash instead of forcing the trade

Adaptive gas reserve is enabled by default:
- `GAS_RESERVE_MNT` remains the floor
- Effective reserve increases with recent live add gas and bin count
- Extra reserve is subtracted from native MNT allocation before LP creation; wide can still use `100%` of free capital without consuming the last gas buffer

`NARROW_CAPITAL_PCT` / `WIDE_CAPITAL_PCT` remain as capital-split parameters and are not part of the active single-position workflow.

## LP Distribution Shapes

Distribution shapes control how liquidity is spread across bins. Each strategy has independent shape configuration.

### Global Defaults

| Variable | Default | Options |
|----------|---------|---------|
| `DISTRIBUTION_SHAPE` | `uniform` | `uniform`, `slope`, `curve`, `custom` |
| `SLOPE_DIRECTION` | `ascending` | `ascending`, `descending`, `peak`, `valley` |
| `SLOPE_STEEPNESS` | `1.0` | `0.1` - `5.0` |
| `CURVE_TYPE` | `exponential` | `exponential`, `logarithmic`, `bell`, `u_curve` |
| `CURVE_EXPONENT` | `2.0` | `0.1` - `10.0` |

### Narrow-Range Strategy (concentrated)

| Variable | Default | Purpose |
|----------|---------|---------|
| `NARROW_DISTRIBUTION_SHAPE` | `slope` | Concentrated shape for fee capture near price |
| `NARROW_SLOPE_DIRECTION` | `peak` | Max liquidity at center bin |
| `NARROW_SLOPE_STEEPNESS` | `2.5` | Strong center concentration |
| `NARROW_CURVE_TYPE` | `logarithmic` | Alternative curve for narrow |
| `NARROW_CURVE_EXPONENT` | `1.5` | Curve steepness |

### Wide-Range Strategy (fee farming)

| Variable | Default | Purpose |
|----------|---------|---------|
| `WIDE_DISTRIBUTION_SHAPE` | `uniform` | Even spread for fee capture |
| `WIDE_SLOPE_DIRECTION` | `ascending` | Direction if slope shape |
| `WIDE_SLOPE_STEEPNESS` | `1.0` | Moderate steepness |
| `WIDE_CURVE_TYPE` | `bell` | Bell curve for wide |
| `WIDE_CURVE_EXPONENT` | `1.0` | Gentle curve |

### How Shapes Work

**Uniform**: Equal liquidity per bin. Best for wide-range fee farming.

**Slope**: Linear gradient across bins.
- `ascending` -- More liquidity in higher-price bins
- `descending` -- More in lower-price bins
- `peak` -- Max at center, tapering to edges
- `valley` -- Min at center, more at edges

**Curve**: Non-linear distribution.
- `exponential` -- Sharply concentrated
- `logarithmic` -- Gently concentrated
- `bell` -- Gaussian bell curve
- `u_curve` -- Inverted bell, heavy on edges

`steepness` / `exponent` control concentration extremity. Higher = more extreme.

## Notifications

| Variable | Default | Description |
|----------|---------|-------------|
| `NOTIFICATIONS_ENABLED` | `true` | Master notification switch |
| `TELEGRAM_NOTIFICATIONS_ENABLED` | `true` | Telegram alerts |
| `TELEGRAM_BOT_TOKEN` | _(none)_ | Telegram bot token |
| `TELEGRAM_CHANNEL_ID` | _(none)_ | Telegram channel/chat ID |

## Debugging

| Variable | Default | Description |
|----------|---------|-------------|
| `MOE_DEBUG` | `false` | Verbose debug logging |
| `LOG_SCAN_START_BLOCK` | `0` | Starting block for historical log scanning |
| `LOG_SCAN_CHUNK_SIZE` | `100` | Block range per `eth_getLogs` call (public RPC limits to 100) |

## Data Persistence

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_DIR` | `data/` | Directory for persisted data |

Files created automatically:
- `data/lp_registry.json` -- LP position tracking (source of truth)
- `data/lp_registry_backup.json` -- Registry backup
- `data/latest_snapshot.json` -- Most recent wallet/position snapshot
- `data/analytics.db` -- Cycle snapshots, operations, ROI, re-entry telemetry
- `data/farm_bot.log` -- Runtime cycle log when not using `--json`
- `data/notifications.jsonl` -- Notification delivery history
