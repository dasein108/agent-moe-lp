# Actual Trading Strategy — Live Behavior Reference

> Describes how the bot behaves in production: all edge cases, token flows, decision paths. Updated whenever trading strategy logic changes.

## Decision Loop (every 5 minutes)

```
1. Read pool state + position + wallet balances
2. Check: is there a position? Is it in range?
3. Decide: hold / enter / exit_and_reenter
4. Execute
5. Sleep 5 min, repeat
```

## Case 1: Position In Range → HOLD

Nothing happens. Position earns swap fees on every trade through your bins. Bot monitors.

## Case 2: Exit UP (price pumped above range)

**What happened**: Price rose through all bins. MNT sold for USDT as price traversed each bin. Position is now ~100% USDT below the active bin.

**Bot flow**:
1. **Remove position** → recovers mostly USDT
2. **Rebalance inventory** (`reentry_skip_rebalance=False`):
   - Bias signal from 5m candles (pump continuing or reversing?)
   - High confidence + aligned → swap USDT → MNT targeting ~70/30 MNT/USDT
   - Low/conflicting confidence → keep one-sided (skip swap)
3. **Select strategy** → narrow (trending) or wide (volatile/ranging)
4. **Create new position** centered on current price:
   - MNT into bins below active
   - USDT into bins above active
   - Both sides earn fees

## Case 3: Exit DOWN (price dropped below range)

**What happened**: Price fell through all bins. USDT bought into MNT. Position is now ~100% MNT above the active bin.

**Bot flow**:
1. **Remove position** → recovers mostly MNT
2. **Rebalance inventory**:
   - Bias signal check
   - High confidence + aligned → swap MNT → USDT targeting ~30/70 MNT/USDT
   - Low confidence → keep one-sided
3. **Select strategy** → narrow or wide by regime
4. **Create new position** centered on current price

## Case 4: Range Too Wide or Too Narrow

If Keltner changes significantly (vol regime shift), engine detects position bin count > 2x or < 0.5x optimal. Triggers same exit_and_reenter flow as Cases 2/3 to resize.

## Case 5: No Position, Market Overbought/Oversold → HOLD

Bot refuses to enter when:
- **Trending UP + overbought** (1h RSI > ~70) — avoids buying the top
- **Trending DOWN + oversold** (1h RSI < ~30) — avoids falling knife

Waits for conditions to normalize, then enters.

## Case 6: No Position, Favorable Conditions → ENTER

| Market Regime | Strategy | Bin Count | Why |
|---------------|----------|-----------|-----|
| Volatile / ATR > 12% | **Wide** (~90-120 bins) | Survive large swings |
| High vol / ATR 8-12% | **Wide** (~60-90 bins) | Moderate coverage |
| Ranging + Keltner confirms | **Wide** | Channel-width fee capture |
| Trending up or down | **Narrow** (~6-30 bins) | Concentrated fees on trend |
| Default (no data) | **Narrow** | Conservative fallback |

## Rebalance Decision Matrix (after exit)

| Exit Dir | Bias Signal | Confidence | Action | Target Ratio |
|----------|-------------|------------|--------|-------------|
| UP | Conflicting/weak | Any | Keep one-sided (USDT) | N/A |
| UP | Aligned + support | > 0.95 | Swap to 50/50 | 50% MNT |
| UP | Aligned | Medium | Swap to 70/30 | 70% MNT / 30% USDT |
| DOWN | Conflicting/weak | Any | Keep one-sided (MNT) | N/A |
| DOWN | Aligned + support | > 0.95 | Swap to 50/50 | 50% MNT |
| DOWN | Aligned | Medium | Swap to 30/70 | 30% MNT / 70% USDT |

After exit UP, bot buys MNT (mean reversion). After exit DOWN, bot sells MNT for USDT. Only if bias signal supports — otherwise keeps one-sided.

## Safety Mechanisms

- **Budget cap**: 80% of wallet (20% reserve for gas + rebalancing)
- **Gas reserve**: small native MNT reserve (`GAS_RESERVE_MNT`, default 2 — Mantle L2 gas is cheap)
- **Exit circuit breaker**: After 3 consecutive failed removals, holds and alerts via Telegram
- **Preview fail-fast**: Live removal aborts if preview reverts — never sends unvalidated tx
- **Dust bin filter**: Bins with LB token balance < 1e12 ignored for range calc (prevents phantom "in range" from residual dust)
- **Overbought/oversold gate**: Won't enter at RSI extremes during trends
- **Reentry cooldown**: 15 min minimum between exit and re-entry

## Merchant Moe Liquidity Book Mechanics

- **Bins below active**: Only MNT/WMNT (token_x)
- **Active bin**: Both MNT + USDT (swap point)
- **Bins above active**: Only USDT (token_y)
- **One-sided entry**: Wallet mostly USDT → all into bins above active. Acts as limit sell — as price rises, USDT converts to MNT. No swap needed.
- **Bin step**: pool-dependent (target WMNT/USDT pool is 15 bps / 0.15% per bin).

## Key Config Parameters

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `MAX_BUDGET_PCT` | 0.80 | Max % of wallet to deploy |
| `GAS_RESERVE_MNT` | 2 | Native MNT reserved for gas (cheap L2) |
| `BIN_COUNT` | 30 | Default bins (overridden by Keltner) |
| `SLIPPAGE_BPS` | 100 | 1% slippage tolerance |
| `REENTRY_SKIP_REBALANCE` | False | Swap inventory on re-entry |
| `REENTRY_COOLDOWN_SECONDS` | 900 | Min seconds between exit and re-entry |
| `POLL_INTERVAL_SECONDS` | 300 | Cycle frequency |

## Code Locations

| Component | File | Key Lines |
|-----------|------|-----------|
| StrategyEngine.select_strategy() | strategies/engine.py | 129-267 |
| execute_cycle() | farm_bot.py | 1809-1920 |
| _exit_and_reenter() | farm_bot.py | 902-1145 |
| apply_inventory_policy() | strategies/reentry_policy.py | 668-868 |
| resolve_ensemble_decision() | strategies/reentry_policy.py | 584-627 |
| cycle_preparer.prepare() | orchestration/cycle_preparer.py | 49-79 |
| DUST_LB_TOKEN_THRESHOLD | lp_service.py | 47 |

---

## Change Log

All strategy-related changes are tracked here. When modifying any code that affects
trading decisions, append a dated entry below.

### 2026-06-14: Configurable OOR tolerance (passive mode) — opt-in, default unchanged
- **Changed**: `StrategyEngine` adaptive out-of-range tolerance floor/cap are now configurable (`engine.py` `__init__` `oor_tolerance_bins`/`oor_tolerance_cap_bins`; `config.py` `OOR_TOLERANCE_BINS`/`OOR_TOLERANCE_CAP_BINS` env; wired in `farm_bot.py`). Defaults remain **15/40** — no behavior change unless set.
- **Why**: Backtest walk-forward (6 rolling 30-day windows, WMNT/USDT0 binStep-100, ~4 months) showed the bot's re-centering is **net-negative vs a static position** (mean +0.3% vs +3.0%), driven by value-destroying re-centers at price extremes (buy-the-high / sell-the-low). Raising the tolerance to ~30 bins made the strategy **match static (6/6, mean +3.0%)** by holding through ordinary drift and only re-centering on extreme sustained moves. Trend-confirmation and stabilization-hold gates were tested and did **not** help (documented dead-ends).
- **Effect**: With defaults, none. Setting e.g. `OOR_TOLERANCE_BINS=30` makes the bot far more passive (fewer re-centers, lower tail risk). **Caveat**: tolerance is in BINS; price-% ≈ bins × bin_step/100, so the right value differs per pool (30 bins ≈ 4.5% on binStep-15, ≈ 30% on binStep-100). Validated only on one pool / 4-month window — tune per pool before relying on it.
- **Tooling**: New `moe-backtest` harness (`src/moe_mantle_bot/backtest/`) with the walk-forward / sweep scripts produced these findings.

### 2026-06-13: Initial strategy
- **Strategy**: Single-position WMNT/USDT fee farming on the Merchant Moe Liquidity Book V2.2 pool (Mantle mainnet). One live LP intent at a time, auto-selected narrow vs. wide via Keltner channel width plus an ATR floor (`--strategy auto`), or forced with `--strategy narrow|wide`.
- **Lifecycle**: Monitor active bin vs. position range; hold while in range; top-up when free wallet value is sufficient; exit-and-reenter when out of range (subject to OOR tolerance / dormant-hold logic); rebalance inventory per the re-entry policy stack before re-entry.
- **Safety**: 80% budget cap (`MAX_BUDGET_PCT`), small native MNT gas reserve (`GAS_RESERVE_MNT`, default 2 — cheap L2), MNT min-balance guard (`MNT_MIN_BALANCE`), dust bin filter, exit circuit breaker (hold + Telegram alert after 3 consecutive removal failures), and live-removal preview fail-fast.
- **Scope**: Fee-farming only — collects LB swap fees. No MOE-emission / MasterChef staking.
