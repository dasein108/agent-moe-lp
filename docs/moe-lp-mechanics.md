# Merchant Moe Liquidity Book — LP Mechanics

Reference for LP deployment: bin composition rules, distribution math, the WrongAmounts problem and its fix, amount adjustment, partial removal. Merchant Moe LB is a Trader Joe Liquidity Book V2.2 fork, so the bin math is identical.

## Bin Composition Rules

Merchant Moe Liquidity Book distributes liquidity across discrete price bins. Each bin has strict rules on which tokens it can hold:

```
      token_x only (WMNT)         mixed          token_y only (USDT)
  ┌─────────────────────────┐ ┌──────────┐ ┌─────────────────────────┐
  │  bin +5  bin +4  bin +3 │ │  bin 0   │ │  bin -1  bin -2  bin -3 │
  │  bin +2  bin +1          │ │ (active) │ │  bin -4  bin -5         │
  └─────────────────────────┘ └──────────┘ └─────────────────────────┘
       delta_id > 0              delta_id = 0       delta_id < 0
       (price > active)          (current price)    (price < active)
```

- **Bins above active** (`delta_id > 0`): ONLY token_x (WMNT). `distribution_y[i]` MUST be 0.
- **Bins below active** (`delta_id < 0`): ONLY token_y (USDT). `distribution_x[i]` MUST be 0.
- **Active bin** (`delta_id = 0`): BOTH tokens. Ratio depends on current reserves.

**Violating these rules causes `LBRouter__WrongAmounts` revert.**

## Distribution Calculation

### Step 1: Candidate Modes

Bot tries three allocation modes and picks the best:

| Mode | Active bin gets | When used |
|------|----------------|-----------|
| `mixed` | Both token_x and token_y | Balanced MNT/USDT ratio |
| `x_only` | Only token_x | MNT-heavy wallet |
| `y_only` | Only token_y | USDT-heavy wallet |

For each mode, `_candidate_uniform_allocations()` computes per-bin allocations respecting composition rules.

### Step 2: Shape Weights (optional)

Shape weights (slope, curve) redistribute liquidity within each token's valid bins:

```python
# CORRECT: Apply weights only to bins that have that token
x_indices = [i for i in range(n) if x_allocs[i] > 0]  # active + above bins
y_indices = [i for i in range(n) if y_allocs[i] > 0]  # below + active bins
# Weights applied independently per token, preserving zero allocations
```

Previous bug: weights applied to ALL bins uniformly → token_x in below-active bins (violation). Fixed in `_apply_shape_weights()`.

### Step 3: Distribution Arrays

Per-bin allocations convert to integer distribution arrays:

```python
distribution_x[i] = int(x_alloc[i] / total_amount_x * 10^18)
distribution_y[i] = int(y_alloc[i] / total_amount_y * 10^18)
```

Router computes actual amounts: `bin_amount = total_amount * distribution[i] / 10^18`.

### Step 4: Amount Adjustment

Distribution math determines optimal token ratio from bin geometry, which may differ from the 50/50 allocation target. Bot adjusts `amount_x` and `amount_y` to match:

```python
actual_x = sum(distribution_x) * amount_x // ONE
actual_y = sum(distribution_y) * amount_y // ONE
```

Router receives exactly the amounts distributions encode. Without this, excess tokens cause `LBRouter__WrongAmounts`.

## WrongAmounts — Root Causes and Fixes

`LBRouter__WrongAmounts` (selector `0x9931a6ae`) reverts when token amounts don't match active bin composition. Three independent causes identified and fixed:

### Cause 1: Shape Weights Violating Bin Rules

**Problem:** Shape weights (slope/peak/curve) applied uniformly to all bins → token_x into y-only bins and vice versa.

**Fix:** `_apply_shape_weights()` applies weights independently per token's valid bins. Zero-allocation bins stay zero.

**Related — zero-weight edge bins:** Non-uniform shapes (`slope` any direction, `logarithmic` curve) assign `0.0` to an edge/center bin (e.g. ascending slope → leftmost bin = 0, valley → center bin = 0). A bin in range receiving zero tokens also reverts `WrongAmounts`. Shapes that are safe: `uniform`, `exponential`/`bell`/`u_curve` curves (all weights > 0). `farm_bot.py` passes `distribution_params=None` (falls back to global `uniform`) to stay safe. To re-enable slope/log, add a minimum floor in `calculate_slope_weights()` (`lp_shapes.py`) so every bin gets ≥1% then re-normalize, verify with `moe lp add --dry-run`, then switch `farm_bot.py` back to `get_narrow_distribution_params()`.

### Cause 2: Stale Active Bin ID

**Problem:** `active_bin_id` read at start of `create_position()`, but many RPC calls (approvals, balance checks) happen before `addLiquidity`. On volatile pairs, active bin can move in this window.

**Fix:** Fresh `getActiveId()` call immediately before computing distributions. New ID used if bin moved.

### Cause 3: Amount/Distribution Mismatch

**Problem:** Allocation layer targets 50/50 by USD value (e.g., 633 MNT + $13.52 USDT). Bin geometry for 10 bins (5 below, 1 active, 4 above) requires a specific ratio. Router received full amounts but distributions encoded only a portion → excess tokens triggered revert.

**Fix:** After computing distributions, derive actual amounts from `sum(dist) * amount / ONE` and adjust `amount_x`/`amount_y`/`native_value`. Router receives exactly what distributions encode.

## Capital Budget Model

```
wallet_mnt  = native MNT + WMNT balance      (in wallet, spendable)
wallet_usdt = USDT balance                    (in wallet, spendable)
deployed_mnt  = MNT locked in LP position     (on-chain, not in wallet)
deployed_usdt = USDT locked in LP position    (on-chain, not in wallet)

total_mnt  = wallet_mnt + deployed_mnt
total_usdt = wallet_usdt + deployed_usdt
free_mnt   = min(wallet_mnt - gas_reserve, wallet_mnt * MAX_BUDGET_PCT)
free_usdt  = wallet_usdt * MAX_BUDGET_PCT     (capped at 80% by default)
```

Key points:
- Wallet balance does NOT include deployed capital (LP tokens locked in pool contract)
- Mantle is a cheap L2, so the native MNT gas reserve is small (`GAS_RESERVE_MNT` default 2) plus a small native estimate headroom buffer
- Native MNT min balance guard (`ensure_mnt_min_balance`) runs before budget computation each cycle; auto-replenishes native MNT when below threshold

Deployment rules:
- Narrow uses `90%` of free capital (`target_pct=0.9`)
- Wide uses `100%` of free capital (`target_pct=1.0`)
- Wide can optionally rebalance before entry when wallet is too skewed
- Adaptive gas reserve can reduce native MNT allocation further based on recent gas costs

## Partial Removal (per-bin LBToken tracking)

Partial removal works by tracking per-bin LBToken amounts in the registry:

### On Create

```python
# Snapshot balanceOfBatch BEFORE addLiquidity
pre_balances = pool.balanceOfBatch(wallet, target_bins)
# Execute addLiquidity
# Snapshot AFTER
post_balances = pool.balanceOfBatch(wallet, target_bins)
# Store delta as bin_amounts in registry
bin_amounts = {bid: post - pre for bid, post, pre in ...}
```

### On Remove (strategy-specific)

```python
# Read registered bin_amounts for this strategy
# Verify on-chain balance >= registered amount
# Call removeLiquidity with only this strategy's amounts
# Other strategy's tokens stay untouched
```

`removeLiquidity` accepts specific `amounts[]` per bin — pass only what's registered for the target strategy.

## Keltner Channel for Wide Bin Count

Keltner Channel determines the wide position's bin count.

### Channel Computation

```
Middle = EMA(close, period=20)
ATR    = RMA(true_range, period=14)    # Wilder's smoothing, not SMA
Upper  = Middle + ATR * 1.5            # BALANCED config multiplier
Lower  = Middle - ATR * 1.5
Width% = (Upper - Lower) / Middle * 100
```

RMA (Wilder's smoothing) decays old volatility exponentially (`alpha = 1/period`), producing a tighter channel than SMA-based ATR. Spike 14 candles ago has ~5% weight in RMA vs ~7% in SMA.

### Bin Count from Keltner + ATR

Three candidates, largest wins (clamped to [40, 200]):

```python
from_keltner = int(width_pct * 2.5 / 0.05)     # primary: Keltner channel width
from_sqrt    = int(30 * sqrt(width_pct))         # secondary: diminishing returns
from_atr     = int(daily_atr_pct * 0.5 / 0.05)  # floor: ATR safety net

bin_count = max(40, from_keltner, from_sqrt, from_atr)
bin_count = min(200, bin_count)
```

| Keltner Width | Daily ATR | from_keltner | from_sqrt | from_atr | Result |
|--------------|-----------|-------------|----------|---------|--------|
| 1.5% | 8% | 75 | 36 | 80 | 80 |
| 2.0% | 10% | 100 | 42 | 100 | 100 |
| 3.0% | 12% | 150 | 51 | 120 | 150 |
| 5.0% | 14% | 250→200 | 67 | 140 | 200 (cap) |

Keltner width reflects realized ranging band — the price action LP can capture fees from. ATR includes trend/gap moves that blow through any range, so it's demoted to a safety floor (0.5×).

### Keltner Configs

| Config | EMA | ATR | Multiplier | Target Width | Max Width |
|--------|-----|-----|------------|-------------|-----------|
| CONSERVATIVE | 24 | 16 | 1.5 | 2% | 4% |
| BALANCED | 20 | 14 | 1.5 | 3% | 5% |
| AGGRESSIVE | 16 | 12 | 2.5 | 6% | 10% |
| WIDE_CAPTURE | 12 | 10 | 3.0 | 8% | 12% |

Default: BALANCED. Data source: Bybit MNTUSDT 5-minute candles (200 periods).

## Re-entry Inventory Policy

`exit_and_reenter` no longer assumes a blanket `50/50` rebalance.

Current flow:
1. Remove the LP and recover actual wallet inventory
2. Classify exit as `exit_down`, `exit_up`, or `neutral`
3. Apply configured inventory policy
4. Only swap if guard rails and bias gate allow it
5. For wide re-entry, run the same wide inventory gate used by fresh wide entry
6. Rebuild LP from resulting wallet balances

Default-safe policy modes:
- `continuation_safe`: no swap, keep recovered skew
- `partial_rebalance`: target `30/70` after `exit_down`, `70/30` after `exit_up`
- `neutral_rebalance`: target `50/50`

Guard rails:
- `MIN_REENTRY_SWAP_USDT`: skip uneconomic small swaps
- `MIN_REENTRY_CONFIDENCE`: require enough policy confidence
- `MAX_REENTRY_SWAP_PCT`: skip oversized wallet rotations

Phase 2 bias gate:
- `exit_down` partial rebalance only allowed when candle bias supports bullish reversal
- `exit_up` partial rebalance only allowed when candle bias supports bearish reversal
- Continuation bias blocks the swap, wallet stays one-sided for re-entry
- Once reversal swap allowed, target ratio follows confidence ladder:
  - Medium confidence → keep Phase 1 base target
  - Strong confidence → move toward `40/60` or `60/40`
  - Very strong confidence → allow `50/50`

Phase 3.1 optional RSI filter:
- Disabled by default behind `REENTRY_RSI_FILTER_ENABLED`
- When enabled, `exit_down` reversal swaps also require `RSI <= REENTRY_RSI_EXIT_DOWN_THRESHOLD`
- When enabled, `exit_up` reversal swaps also require `RSI >= REENTRY_RSI_EXIT_UP_THRESHOLD`
- If RSI doesn't confirm reversal, swap is blocked and recovered inventory stays unchanged

Phase 3.2 optional EMA trend filter:
- Disabled by default behind `REENTRY_TREND_FILTER_ENABLED`
- When enabled, reversal swaps also require fast/slow EMA trend support
- `exit_down` accepts bullish cross or bearish spread already flattening toward reversal
- `exit_up` accepts bearish cross or bullish spread already flattening toward reversal
- If fast/slow trend is still clearly continuation-aligned, reversal swap is blocked

Phase 3.3 optional ensemble decision:
- Disabled by default behind `REENTRY_ENSEMBLE_ENABLED`
- Combines candle-bias alignment, RSI filter, and EMA trend filter into one policy decision
- Outputs one of:
  - `keep_one_sided`
  - `swap_to_30_70`
  - `swap_to_50_50`
  - `skip_reentry`
- `skip_reentry` leaves wallet in cash after exit, waits for later cycle

Residual-position handling:
- Dust LP and sub-min residual LP do not permanently block fresh deployment
- If residual LP is smaller than `MIN_POSITION_SIZE_USDT` and wallet has enough free capital, bot treats position as non-blocking for strategy selection
- If in-range position exists and free capital is still meaningful, bot can top up same strategy instead of idling in `HOLD`

Wide entry safety extension:
- Bot rejects unsafe one-sided live add paths before gas is spent
- If free inventory is too MNT-skewed or free USDT is too small, wide inventory gate blocks entry
- If `WIDE_ENTRY_REBALANCE_ENABLED=true`, bot can execute one guarded swap toward `WIDE_ENTRY_REBALANCE_TARGET_MNT_RATIO_BPS` before wide entry
- If post-swap wallet is still too skewed, cycle stays in cash instead of forcing a dust LP
