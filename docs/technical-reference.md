# Technical Reference

## Module Map

```
src/moe_mantle_bot/
├── __init__.py              Package exports
├── models.py                frozen dataclasses
├── constants.py             Network/contract addresses, RPC endpoints
├── config.py                Settings from environment
├── abi.py                   Contract ABIs (LB_PAIR, LB_ROUTER, ERC20, WMNT)
├── utils.py                 Decimal serialization, price math, timestamps
├── logging_config.py        Structured logging setup
│
├── rpc_client.py            Web3 wrapper with 5-endpoint failover
├── tx_sender.py             Transaction build/sign/send + error classes
├── balance_manager.py       Balance reads, wrap/unwrap, swap, rebalance, budget
├── lp_service.py            Pool state, position discovery, LP create/remove
├── lp_shapes.py             Distribution shape functions (uniform, slope, curve)
│
├── _lp_registry.py          JSON-persisted LP position registry
├── farm_bot.py              FarmBot: top-level single-position orchestration entrypoint
├── snapshot.py              Wallet/position snapshot builder
├── analytics.py             SQLite analytics persistence
├── notifications.py         Telegram alert formatting
├── notification_formatter.py  Pure Telegram message formatting (no side effects)
├── telegram.py              Telegram HTTP client
├── strategy_types.py        Strategy intent / decision dataclasses
├── wallet_store.py          Wallet file read/write
│
├── command_cli.py           moe CLI
├── cli.py                   moe-readonly CLI
├── portfolio_review.py      Portfolio analysis + display
├── portfolio_review_cli.py  moe-portfolio CLI
│
├── core/
│   └── wallet.py            Centralized wallet loading
│
├── execution/
│   └── executor.py          SinglePositionIntentExecutor
│
├── orchestration/
│   ├── cycle_context.py     Normalized cycle context
│   ├── cycle_preparer.py    State load/normalize/snapshot/logging
│   ├── cycle_planner.py     Strategy planning into StrategyIntent
│   └── reentry_coordinator.py Re-entry result shaping + analytics closeout
│
├── strategies/
│   ├── base.py              Strategy profile protocol
│   ├── engine.py            StrategyEngine: pure-logic strategy selection (no blockchain)
│   ├── legacy_profile.py    Active single-position strategy adapter
│   ├── reentry_policy.py    Re-entry signal stack and pre-entry policy
│   └── narrow_range.py      Narrow-range helper
│
└── quant/
    ├── mtf_analyzer.py      Multi-timeframe analysis (5m/1h/4h regime + RSI + ATR)
    ├── keltner_analyzer.py  Keltner Channel computation + quality scoring
    ├── wide_range_lp_manager.py  Wide-range strategy + param calculation
    ├── bias_calculator.py   Slope + momentum + order-flow bias scoring
    └── candle_fetcher.py    OHLCV data from Bybit (MNTUSDT, 1m/5m/1h/4h)
```

`farm_bot.py` exposes `moe-farm` via `cli_main`.

## Data Models

### TokenInfo
```python
TokenInfo(address, name, symbol, decimals)
```

### PoolState
```python
PoolState(
    pair_address, token_x: TokenInfo, token_y: TokenInfo,
    bin_step, active_bin_id,
    price_y_per_x: Decimal, price_y_per_x_raw_128x128: int,
    mnt_price_usdt: Decimal | None,
    reserve_x_raw, reserve_x_normalized,
    reserve_y_raw, reserve_y_normalized,
    protocol_fee_x_raw, protocol_fee_y_raw,
    static_fee_parameters: dict, variable_fee_parameters: dict,
)
```

### PositionState
```python
PositionState(
    wallet_address, candidate_bin_ids: list[int],
    active_bins: list[BinState],
    position_exists: bool, in_range: bool,
    min_bin_id, max_bin_id,
    estimated_token_x: Decimal | None,  # Underlying WMNT in position
    estimated_token_y: Decimal | None,  # Underlying USDT in position
    inventory_included: bool,
)
# .bin_count property returns len(active_bins)
```

### BinState
```python
BinState(
    bin_id, wallet_lb_token_balance_raw,
    bin_total_supply_raw, bin_reserve_x_raw, bin_reserve_y_raw,
    estimated_token_x: Decimal | None,
    estimated_token_y: Decimal | None,
)
```

### CapitalBudget
```python
CapitalBudget(
    total_mnt, total_usdt,
    deployed_mnt, deployed_usdt,
    free_mnt, free_usdt,
    gas_reserve_mnt, mnt_price_usdt,
)
# Properties: free_value_usdt, deployed_value_usdt, total_value_usdt
```

### ExecutionResult
```python
ExecutionResult(action: str, tx_hash: str | None, dry_run: bool, details: dict)
```

### WalletBalances
```python
WalletBalances(
    native_mnt: NativeBalance, wmnt: ERC20Balance, usdt: ERC20Balance,
    mnt_price_usdt: Decimal | None,
)
# Properties: total_mnt_equivalent, total_value_usdt
```

## Merchant Moe Liquidity Book Math

Merchant Moe LB is a Trader Joe Liquidity Book V2.2 fork, so the bin math is identical.

### Bin ID to Price

```python
price = (1 + bin_step/10000) ^ (bin_id - 2^23) * 10^(decimals_x - decimals_y)
```

For WMNT/USDT (18/6 decimals, bin_step=15):
```python
price_usdt_per_mnt = (1.0015) ^ (bin_id - 8388608) * 10^12
```

### Position Discovery

Merchant Moe LB positions are per-bin fungible tokens. No NFT or position ID. Discovery:

1. **Near-active probing**: `balanceOfBatch()` for bins around the current active bin (fast, covers most cases)
2. **Log scanning** (fallback): `eth_getLogs` for `TransferSingle`/`TransferBatch` events from pool to wallet. Limited to 100-block ranges on the public RPC.

### Distribution Calculation

For each bin in the position:
1. Compute bin's price relative to active bin
2. Determine if bin is above active (token_x only), below (token_y only), or at active (mixed)
3. Try three allocation modes (mixed, x_only, y_only), pick the one that maximizes liquidity
4. Apply shape weights independently per token's valid bins (preserving zero allocations)
5. Convert to LB `distribution_x` and `distribution_y` arrays (uint256, fractions of 10^18)
6. Adjust `amount_x`/`amount_y` to match `sum(distribution) * amount / ONE` — router receives exactly what distributions encode

The router's native-liquidity path is used for LP entry; the bot converts between native MNT and WMNT as needed.

### Dust Bin Filter

When removing positions, `remove_position()` fetches per-bin inventory (`include_inventory=True`) and filters out bins where the user's share of reserves rounds to zero for both token X and Y. These "dust" bins — leftover from prior positions with ~34k wei of LBToken balance — would cause `LBPair__InsufficientLiquidityBurned` (selector `0xfd447929`) reverts if included in the removal call. Filter logs skipped bins and proceeds with only bins that have real value.

See [Merchant Moe LP Mechanics](moe-lp-mechanics.md) for full bin composition rules and WrongAmounts fixes.

## Error Handling

### TransactionExecutionError

Raised by `TxSender.send()` when a transaction fails. Contains:
- `action` -- What was being done ("add_liquidity", "remove_liquidity", etc.)
- `stage` -- Where it failed ("preview", "build", "sign", "broadcast", "wait")
- `retryable` -- Whether the error is transient
- `failure_fingerprint` -- SHA256 hash for deduplication

### PreviewValidationError

Raised when a simulated `call()` fails before actual execution. Prevents submitting txs that would revert on-chain.

### Transient vs Fatal Errors

Transient (retried with RPC rotation):
- Timeout, rate limit, 502/503
- Connection reset/aborted
- "header not found", "block not found"

Fatal (fail immediately):
- Contract logic errors (insufficient balance, slippage exceeded)
- Invalid parameters
- Transaction reverted

## LP Registry

### Structure (data/lp_registry.json)

```json
{
  "wallet_address": "0x...",
  "last_updated": "2025-03-23T12:00:00",
  "positions": {
    "narrow": [
      {
        "id": "narrow_1711000000_8325645",
        "strategy_type": "narrow",
        "min_bin": 8325645,
        "max_bin": 8325655,
        "bin_count": 11,
        "created_at": "2025-03-23T12:00:00",
        "created_tx": "0xabc...",
        "initial_mnt": 500.0,
        "initial_usdt": 10.5,
        "initial_value_usdt": 21.0,
        "distribution_shape": "slope",
        "bin_amounts": {"8325645": 123456789, "8325646": 234567890, ...},
        "exited_at": null
      }
    ],
    "wide": []
  },
  "statistics": {
    "total_positions": 1,
    "narrow_positions": 1,
    "wide_positions": 0,
    "total_bins_covered": 11,
    "total_initial_value_usdt": 21.0
  }
}
```

### Registry Hooks

- `LPService.create_position()` automatically registers the position on successful live execution
- `LPService.remove_position()` automatically deregisters matching positions
- Registration includes: strategy_type, bin range, tx_hash, amounts, distribution_shape, bin_amounts (per-bin LBToken amounts for partial removal)

### Registry Merge

When a top-up adds capital to an existing active position of the same strategy type, `add_position()` **merges** instead of creating a duplicate:
- `bin_amounts` accumulated per-bin (existing + new)
- Bin range expands: `min_bin = min(old, new)`, `max_bin = max(old, new)`
- Capital totals accumulate: `initial_mnt += new_mnt`, etc.
- Logged as "MERGE" not "ADD"

Reflects Merchant Moe's on-chain behavior: bin balances from same wallet are fungible.

### Reconciliation

`LPService.reconcile(wallet, dry_run)` compares registry bins against onchain bins discovered via `balanceOfBatch`. Reports:
- **Matched** -- Bins present in both registry and onchain
- **Unauthorized** -- Bins onchain but not in registry
- **Missing** -- Bins in registry but not onchain

## Gas / Native-Balance Behaviors

### Native MNT Reserve

Mantle is a cheap L2, so the native gas reserve is small (`GAS_RESERVE_MNT` default 2). Budget logic keeps gas covered:

1. **Native reserve**: `GAS_RESERVE_MNT` (plus a native estimate headroom buffer) subtracted from wallet in budget
2. **Proportional cap** (80%): `MAX_BUDGET_PCT` ensures ~20% of wallet stays as headroom regardless of tx size
3. **Unwrap target**: native-headroom logic ensures the wallet retains sufficient native MNT after any WMNT unwrap used to fund an LP entry

### Dust Bin Filtering

After LP removal, residual LBToken dust (~34k wei per bin) remains on-chain permanently. Contract reverts with `LBPair__InsufficientLiquidityBurned` (selector `0xfd447929`) when attempting to burn these amounts.

Dust threshold: `DUST_LB_TOKEN_THRESHOLD = 1_000_000` (1M wei)

Dust bins excluded from:
- **Position range** — `min_bin_id`, `max_bin_id`, `in_range` use only bins above threshold
- **`active_bins` list** — dust bins not returned in `PositionState.active_bins`
- **LP removal** — `remove_position()` skips dust bins to prevent contract revert
- **Strategy decisions** — positions with only dust bins are treated as empty

### Known Liquidity Book Error Selectors

| Selector | Error | Meaning |
|----------|-------|---------|
| `0x9931a6ae` | LBRouter__WrongAmounts | msg.value mismatch in addLiquidityNATIVE |
| `0x8a0d377b` | LBRouter__InsufficientAmountOut | Slippage exceeded |
| `0xfd447929` | LBPair__InsufficientLiquidityBurned | Dust bin burn yields 0 tokens |
| `0x1f2a2005` | LBRouter__DeadlineExceeded | Transaction deadline passed |

## RPC Failover

Five Mantle endpoints rotate on transient failures (see `constants.py`):

```
rpc.mantle.xyz → mantle-rpc.publicnode.com → mantle.drpc.org → 1rpc.io/mantle → mantle-mainnet.public.blastapi.io
```

Each `call_with_retry()` tries up to 3 times with endpoint rotation. The public RPC limits `eth_getLogs` to 100-block ranges, so log scanning uses `LOG_SCAN_CHUNK_SIZE=100`.

## Analytics

`data/analytics.db` stores portfolio snapshots, operation records, Phase 0 re-entry telemetry.

`SinglePositionCyclePreparer.prepare()` also emits one market-indicator log line per monitoring pass. Aligned with `poll_interval_seconds`. Includes `RSI(14)`, `SMA(20/50)`, `EMA(20/50)`, combined bias score/confidence, current Keltner width/confidence summary. Order-flow logged as unavailable unless a live trade stream is explicitly attached to the farm loop.

### snapshots

Time-series portfolio state:
- Wallet MNT / USDT
- Deployed MNT / USDT
- Total and free USD value
- Active bin, narrow / wide coverage

### operations

Execution records for add/remove/swap/rebalance with gas, value moved, JSON details.

### reentry_events

Single-mode exit-and-reenter lifecycle tracking:
- Exit direction (`down`, `up`, `unknown`)
- Recovered MNT / USDT after removal
- Selected re-entry strategy and bin count
- LP active mode (`mixed`, `x_only`, `y_only`, or one-sided variants)
- Expected refunds and fill percentages from distribution telemetry
- Next observed cycle outcome:
  - Elapsed seconds since re-entry
  - Whether position is still in range
  - Portfolio value vs HODL of recovered exit inventory
  - Gross turnover for the remove + add rotation

Phase 1 adds an explicit pre-entry inventory policy in `ReentryPolicyService.apply_inventory_policy()`:
- `continuation_safe` keeps recovered one-sided inventory
- `partial_rebalance` targets `30/70` after `exit_down` and `70/30` after `exit_up`
- `neutral_rebalance` targets `50/50`

All re-entry swaps are guarded by:
- `MIN_REENTRY_SWAP_USDT`
- `MIN_REENTRY_CONFIDENCE`
- `MAX_REENTRY_SWAP_PCT`

Phase 2 adds a candle-based re-entry bias gate:
- `ReentryPolicyService.calculate_candle_bias_signal()` scores slope + momentum from 5-minute MNT (MNTUSDT) candles
- `ReentryPolicyService.get_bias_signal()` maps that bias to re-entry context and records `RSI(14)`
- `exit_down` partial rebalance now requires bullish reversal bias
- `exit_up` partial rebalance now requires bearish reversal bias
- Conflicting continuation bias sets effective confidence to `0` and blocks the swap
- `ReentryPolicyService.resolve_target_ratio_bps()` applies the Task 2.2 ladder:
  - Base target for medium-confidence reversal
  - `40/60` or `60/40` for stronger reversal
  - `50/50` for very high reversal confidence

Phase 3.1 adds an optional RSI reversal filter:
- `REENTRY_RSI_FILTER_ENABLED=false` keeps existing Phase 2 behavior
- When enabled, `ReentryPolicyService.apply_rsi_filter()` requires oversold RSI after `exit_down` and overbought RSI after `exit_up`
- Failing RSI check changes alignment to `rsi_filter_blocked` and sets effective confidence to `0`

Phase 3.2 adds an optional EMA trend filter:
- `REENTRY_TREND_FILTER_ENABLED=false` keeps existing Phase 2/3.1 behavior
- `ReentryPolicyService.calculate_trend_signal()` computes fast/slow EMA spread and whether it is flattening toward reversal
- `ReentryPolicyService.apply_trend_filter()` only allows reversal swaps when fast/slow trend has already crossed or is weakening toward a crossover
- Failing trend check changes alignment to `trend_filter_blocked` and sets effective confidence to `0`

Phase 3.3 adds an optional ensemble decision layer:
- `REENTRY_ENSEMBLE_ENABLED=false` keeps existing Phase 2/3.2 ladder behavior
- `ReentryPolicyService.resolve_ensemble_decision()` collapses the signal stack to one of:
  - `keep_one_sided`
  - `swap_to_30_70`
  - `swap_to_50_50`
  - `skip_reentry`
- When `skip_reentry` is returned, the re-entry coordinator closes the event without rebuilding LP in that cycle

Single-position capital deployment follows one helper path:
- Narrow entry / re-entry uses `90%` of free capital
- Wide entry / re-entry / top-up uses `100%` of free capital
- Wide first runs through wide inventory preparation helper before execution:
  - If inventory is already usable, LP creation proceeds immediately
  - If inventory is too skewed and guarded rebalance is enabled, bot executes one pre-entry rebalance and refreshes budget
  - If inventory is still too skewed, bot returns `hold_cash_wait_rebalance` instead of forcing a one-sided live add
- Effective gas reserve helper then expands the reserve using recent add gas and bin count before the final native MNT amount is handed to the single-position executor
- `GAS_RESERVE_MNT` remains the floor because `free_mnt` is still computed after subtracting the base reserve in `BalanceManager.get_capital_budget()`
- `free_mnt` and `free_usdt` are further capped at `MAX_BUDGET_PCT` (default 80%) of wallet balance to maintain a reserve buffer
- `ensure_mnt_min_balance()` runs before budget computation each cycle: if native MNT is below `MNT_MIN_BALANCE`, it unwraps WMNT or swaps USDT to reach 2x the threshold

Residual-position handling before strategy selection:
- Dust LP is ignored for selection
- Sub-min LP is ignored when free capital is already large enough to deploy a real replacement or top-up
- In-range positions with meaningful free capital can trigger a same-strategy top-up instead of plain `hold`

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_strategy_selector.py -v

# Test files:
# test_balance_manager.py   -- Rebalance state/plan, LP allocation, swap quotes
# test_lp_service.py        -- Pool state, position detection, validation
# test_distribution_shape.py -- Shape overrides, param passthrough
# test_registry_hooks.py    -- Register/deregister on create/remove
# test_capital_budget.py    -- Budget model, deployed vs free capital
# test_strategy_selector.py -- Strategy selection logic
# test_exit_reenter.py      -- Early exit detection, edge-of-range
# test_analytics_phase0.py  -- Re-entry analytics lifecycle and follow-up metrics
# test_market_indicator_logging.py -- Cycle-level RSI / MA / bias indicator logging
# test_reentry_policy_phase1.py -- Phase 1 inventory policy mapping and swap guards
# test_reentry_bias_phase2.py -- Phase 2 candle bias scoring and reversal gate
# test_reentry_shape_phase23.py -- Phase 2.3 wide re-entry shape biasing
# test_reentry_trend_phase32.py -- Phase 3.2 EMA trend filter for reversal re-entry
# test_reentry_ensemble_phase33.py -- Phase 3.3 ensemble re-entry decision table
# test_dust_position_filter.py -- Residual LP normalization before strategy selection
# test_wide_entry_safety.py -- Wide inventory gate, pre-entry rebalance, adaptive gas reserve
# test_single_position_cycle_preparer.py -- State preparation and indicator/snapshot wiring
# test_single_position_cycle_planner.py -- Intent planning for active single-position path
# test_single_position_executor.py -- Entry / top-up execution for StrategyIntent
# test_reentry_execution_coordinator.py -- Re-entry finalization and analytics closeout
```
