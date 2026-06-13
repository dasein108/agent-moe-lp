# Strategy Guide

## Overview

Bot uses **StrategyEngine** (pure logic, no blockchain calls) to select one action per cycle. Market data is pre-fetched and passed as frozen dataclasses.

```
MarketState + PositionSnapshot + WalletComposition
  → StrategyEngine.select_strategy()
  → StrategyDecision (action + reason + confidence)
```

## Strategy Decision Matrix

```
┌─────────────────┬──────────────┬───────────────┬─────────────────────┐
│ Regime           │ Overbought/  │ Volatility    │ Strategy            │
│                  │ Oversold     │ (daily ATR)   │                     │
├─────────────────┼──────────────┼───────────────┼─────────────────────┤
│ TRENDING_UP      │ overbought   │ any           │ HOLD (wait for dip) │
│ TRENDING_DOWN    │ oversold     │ any           │ HOLD (wait)         │
│ VOLATILE / any   │ any          │ > 12%         │ WIDE (max range)    │
│ any              │ any          │ 8-12%         │ WIDE (survive vol)  │
│ RANGING          │ neither      │ < 8%          │ WIDE (fee capture)  │
│ TRENDING_UP      │ not OB       │ < 8%          │ NARROW (ride trend) │
│ TRENDING_DOWN    │ not OS       │ < 8%          │ NARROW              │
│ (no data)        │ n/a          │ n/a           │ NARROW (default)    │
└─────────────────┴──────────────┴───────────────┴─────────────────────┘
```

### Gate Priority (evaluated in order)

1. **Overbought/Oversold Hold** — TRENDING_UP + RSI>70 on 1h/4h → HOLD. TRENDING_DOWN + RSI<30 → HOLD.
2. **Volatile Regime** — daily ATR > 12% or regime=VOLATILE → WIDE
3. **High Volatility** — daily ATR >= 8% → WIDE
4. **Ranging Market** — regime=RANGING + Keltner (confidence+0.15) > `WIDE_CONFIDENCE_THRESHOLD` → WIDE
5. **Trending** — TRENDING_UP or TRENDING_DOWN → NARROW
6. **Default** → NARROW

### Position Fitness (in-range checks)

| Check | Threshold | Action |
|-------|-----------|--------|
| Out of range | `in_range=False` | EXIT_AND_REENTER |
| Range too wide | `bin_count > 2x optimal` | EXIT_AND_REENTER |
| Range too narrow | `bin_count < 0.5x optimal` | EXIT_AND_REENTER |
| At edge | Disabled (`EDGE_MARGIN_BINS=0`) | HOLD (reduces churn) |

Edge detection disabled — positions exit only when truly OOR, not when approaching the edge. Reduces gas-burning churn in volatile markets.

### Top-up

When position is in-range and free wallet value >= `MIN_TOP_UP_FREE_VALUE_USDT` ($20), engine offers a top-up using the same strategy. Top-ups merge into the existing registry position (not separate entries).

## Multi-Timeframe Analysis (MTF)

`MTFAnalyzer` fetches 5m, 1h, and 4h MNTUSDT candles from Bybit to build a complete market view:

| Timeframe | Candles | Period | Purpose |
|-----------|---------|--------|---------|
| 5m | 200 | 16 hours | Execution-level detail, Keltner |
| 1h | 100 | 4 days | Trend confirmation, RSI |
| 4h | 100 | 17 days | Regime classification, RSI |

### Regime Classification

| Regime | Condition |
|--------|-----------|
| TRENDING_UP | All timeframes BULL (EMA20 > EMA50) |
| TRENDING_DOWN | All timeframes BEAR |
| RANGING | Mixed signals, no dominant trend |
| VOLATILE | 1h ATR > 3% |

### Overbought/Oversold

- **Overbought**: RSI > 70 on 1h OR 4h
- **Oversold**: RSI < 30 on 1h OR 4h

### Daily ATR

Extrapolated from 4h ATR: `daily_atr_pct = atr_4h_pct * sqrt(6)`

Used by Gates 2 and 3 to select wide over narrow in volatile conditions.

## Strategy Override

```bash
--strategy auto     # Default: MTF + Keltner selection
--strategy narrow   # Force narrow
--strategy wide     # Force wide
```

## Strategy Types

| | **Narrow** | **Wide** |
|---|---|---|
| Bin count | 6-30 (Keltner half-width) | 20-200 (Keltner 1.2x width) |
| Capital | 90% of free | 100% of free |
| Distribution | slope/peak (concentrated center) | uniform (even spread) |
| Purpose | Concentrated fee capture, ride trends | Fee farming, survive volatility |

## Capital Budget

```
free_mnt    = min(wallet_mnt - total_reserve, wallet_mnt * MAX_BUDGET_PCT)
free_usdt   = wallet_usdt * MAX_BUDGET_PCT
```

Two constraints work together:
- **Native MNT reserve** — `GAS_RESERVE_MNT` (plus a native estimate headroom buffer) subtracted from wallet so gas is always covered
- **Proportional cap** (80%) — `MAX_BUDGET_PCT` ensures ~20% of wallet stays as headroom regardless of tx size

Mantle is a cheap L2, so the native gas reserve is small (`GAS_RESERVE_MNT` default 2). The 80% budget cap keeps a proportional buffer for any wallet size.

## Native MNT Min Balance Guard

Before each cycle, if native MNT < `MNT_MIN_BALANCE` (defaults to `GAS_RESERVE_MNT`):
1. Unwraps available WMNT
2. If still short, swaps USDT → WMNT and unwraps to 2x minimum

## Dust Bin Handling

Dust bins (~34k LBToken balance) are permanent on-chain residue from prior positions. They are:
- **Excluded from position range** — `min_bin_id`/`max_bin_id`/`in_range` use only real bins (balance >= 1M threshold)
- **Excluded from removal** — dust filter skips bins that would yield 0 tokens (prevents `0xfd447929` revert)
- **Excluded from strategy decisions** — `_build_position_snapshot` treats dust-only positions as empty

## Registry Merge

When a top-up adds capital to an existing position of the same strategy type, the registry **merges** instead of creating a separate entry:
- `bin_amounts` aggregated per-bin
- Bin range expands to `min(old, new) - max(old, new)`
- Capital totals accumulate
- Logged as MERGE, not ADD

Reflects Merchant Moe's on-chain behavior: bin balances from the same wallet are fungible.

## Re-entry Policy

After EXIT_AND_REENTER:

1. **Remove** current LP position
2. **Rebalance** inventory (if policy mode is not `continuation_safe`)
3. **Re-analyze** market with fresh Keltner + MTF
4. **Re-enter** with selected strategy (or HOLD if overbought/oversold)

### Inventory Policies

| Exit Direction | `continuation_safe` | `partial_rebalance` | `neutral_rebalance` |
|---|---|---|---|
| Price dropped (exit_down) | Keep MNT-heavy | Target 30/70 MNT/USDT | Target 50/50 |
| Price rose (exit_up) | Keep USDT-heavy | Target 70/30 MNT/USDT | Target 50/50 |
| Unknown | Keep as-is | Target 50/50 | Target 50/50 |

Default: `continuation_safe` (no swap).

## Keltner Channel

Used for ranging detection and wide bin count sizing.

| Config | EMA | ATR | Multiplier |
|--------|-----|-----|------------|
| BALANCED (default) | 20 | 14 | 1.5 |

Wide bin count: `max(40, keltner_width×2.5/0.05, 30×sqrt(width), ATR×0.5/0.05)` capped at 200. Keltner width is primary driver; ATR is safety floor only.

## Position Lifecycle

```
No Position → select_strategy() → NARROW or WIDE or HOLD
                                         │
                                    enter position
                                         │
                                    Position Active
                                    (earning fees)
                                         │
                                    next cycle check
                                         │
              ┌──────────┬───────────────┤
              │          │               │
          HOLD/TOP_UP   OOR         range mismatch
              │          │               │
              │     EXIT_AND_REENTER ────┘
              │          │
              │    remove → policy → analyze → re-enter or HOLD
              │
              └── (wait for next cycle)
```

## Telegram Notifications

Bot sends alerts for:
- **Strategy change** — MTF context and position price range
- **LP created** — amounts, bins, mode, price range, MTF context
- **LP removed** — recovered amounts, exit direction, price range
- **Re-entry rebalance** — swap details when inventory policy executes
- **Status report** — wallet, deployed, free, position in/out of range, ROI
- **Errors** — cycle failures with error details
